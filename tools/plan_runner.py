"""plan_runner.py — 全域优化执行计划的「台账 + 租约」工具。

为什么需要这个脚本
----------------
自动执行任务最常见的三种翻车：
1. **重入**：上一次还没跑完，下一次又触发 → 两个会话同时改同一批文件；
2. **僵尸状态**：上一次崩了，步骤还停在 in_progress → 后续每一轮都以为「有任务在跑」，计划永远卡住；
3. **状态丢失**：实现做完了，但写状态那步崩了 → 同一份工作被重复做，或者做了不认账。

所以把「锁 / 心跳 / 状态迁移」从模型手里收到脚本手里：
模型只负责**判断与实现**，状态由本脚本原子地写。

命令
----
    python tools/plan_runner.py [--plan <name>] status
    python tools/plan_runner.py [--plan <name>] check
    python tools/plan_runner.py [--plan <name>] lease acquire --owner <id> [--ttl 1800] [--step S0-1]
    python tools/plan_runner.py [--plan <name>] lease heartbeat --owner <id>
    python tools/plan_runner.py [--plan <name>] lease release --owner <id> --outcome done|failed|blocked|interrupted [--note "..."]
    python tools/plan_runner.py [--plan <name>] step start   --id S0-1 --note "..."
    python tools/plan_runner.py [--plan <name>] step finish  --id S0-1 --verification "..."
    python tools/plan_runner.py [--plan <name>] step block   --id S0-1 --note "..."
    python tools/plan_runner.py [--plan <name>] step reset   --id S0-1 --note "..."
    python tools/plan_runner.py [--plan <name>] runs [--limit 20]

约定
----
- 台账：``data/plan/progress.json``（唯一状态来源）
- ``--plan <name>``：换用独立台账 ``data/plan/<name>-progress.json``（与默认台账互不覆盖）
- 租约：``data/plan/lease.json``（防重入 + 崩溃检测）
- 备份：``data/plan/progress.json.bak``（台账损坏时回退）
- 所有写盘都是**原子写**（临时文件 + os.replace），不会出现半截 JSON
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Windows 控制台默认 GBK：台账备注里可能出现 ⚠ 等字符，直接 print 会崩
# （`check` 在报僵尸状态时崩掉 = 最需要它的时候不能用）。统一 UTF-8 + 兜底替换。
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent.parent
PLAN_DIR = ROOT / "data" / "plan"
LEDGER = PLAN_DIR / "progress.json"
BACKUP = PLAN_DIR / "progress.json.bak"
LEASE = PLAN_DIR / "lease.json"

DEFAULT_TTL = 1800          # 租约有效期（秒）：30 分钟无心跳即视为失联
MAX_RUNS = 60               # 台账里保留的最近执行记录条数


def plan_paths(plan: str) -> tuple[Path, Path, Path]:
    """按计划名解析「台账 / 备份 / 租约」三个路径。

    ``default`` → 39 号方案的 ``progress.json``（旧命令零影响）；
    其他名字 → ``data/plan/<name>-progress.json`` 等，**独立台账互不覆盖**。
    （狼人杀持续优化循环用 ``--plan werewolf``。）
    """
    if not plan or plan == "default":
        return LEDGER, BACKUP, LEASE
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", plan).strip("-") or "plan"
    return (
        PLAN_DIR / f"{slug}-progress.json",
        PLAN_DIR / f"{slug}-progress.json.bak",
        PLAN_DIR / f"{slug}-lease.json",
    )


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write(path: Path, payload: dict) -> None:
    """原子写：先写同目录临时文件，再 replace。崩在中间也不会留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_ledger() -> dict:
    """读台账；损坏时回退备份，并记一条 warning（不静默丢数据）。"""
    data = _load(LEDGER)
    if data is not None:
        return data
    if BACKUP.exists():
        data = _load(BACKUP)
        if data is not None:
            print("[warn] 台账损坏，已回退到备份 progress.json.bak", file=sys.stderr)
            return data
    raise SystemExit("[fatal] 台账不存在且无可用备份：" + str(LEDGER))


def save_ledger(data: dict) -> None:
    """写台账（先备份旧版，再原子写）。"""
    if LEDGER.exists():
        shutil.copy2(LEDGER, BACKUP)
    data["updated_at"] = now_iso()
    runs = data.setdefault("runs", [])
    if len(runs) > MAX_RUNS:
        data["runs"] = runs[-MAX_RUNS:]
    _atomic_write(LEDGER, data)


def find_step(data: dict, step_id: str) -> dict:
    for s in data.get("steps", []):
        if s.get("id") == step_id:
            return s
    raise SystemExit("[fatal] 找不到步骤：" + step_id)


def step_by_id(data: dict, step_id: str) -> int:
    for i, s in enumerate(data.get("steps", [])):
        if s.get("id") == step_id:
            return i
    raise SystemExit("[fatal] 找不到步骤：" + step_id)


def next_step(data: dict) -> dict | None:
    """下一个该做的步骤：第一个非 done 且非 blocked 的步骤（按依赖顺序）。"""
    for s in data.get("steps", []):
        if s.get("status") in ("done",):
            continue
        if s.get("status") == "blocked":
            continue
        return s
    return None


def lease_age(lease: dict) -> float:
    try:
        hb = float(lease.get("heartbeat_ts") or 0)
    except Exception:
        hb = 0
    return max(0.0, time.time() - hb)


def lease_expired(lease: dict) -> bool:
    ttl = float(lease.get("ttl_seconds") or DEFAULT_TTL)
    return lease_age(lease) > ttl


# --------------------------------------------------------------------------
# 命令实现
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    data = load_ledger()
    steps = data.get("steps", [])
    done = sum(1 for s in steps if s.get("status") == "done")
    doing = [s["id"] for s in steps if s.get("status") == "in_progress"]
    blocked = [(s["id"], (s.get("notes") or "")[:80]) for s in steps if s.get("status") == "blocked"]
    nxt = next_step(data)

    lease = _load(LEASE)
    lease_info = "none"
    if lease:
        state = "EXPIRED(可接管)" if lease_expired(lease) else "HELD"
        lease_info = f"{state} owner={lease.get('owner')} age={int(lease_age(lease))}s step={lease.get('step_id')}"

    print(f"台账: {LEDGER}")
    print(f"进度: {done}/{len(steps)} done | in_progress={doing} | blocked={[b[0] for b in blocked]}")
    print(f"租约: {lease_info}")
    if nxt:
        deps = nxt.get("depends_on") or []
        unmet = [d for d in deps if find_step(data, d).get("status") != "done"]
        print(f"下一步: {nxt['id']} | {nxt.get('title','')} | 依赖={deps} 未满足={unmet}")
        print(f"  attempts={nxt.get('attempts', 0)} last_error={(nxt.get('last_error') or '')[:120]}")
    else:
        pend = [s["id"] for s in steps if s.get("status") != "done"]
        if pend:
            print(f"无可自动执行的步骤（剩余均 blocked）：{pend}")
        else:
            print("全部步骤已完成 → 请执行计划文档第七节的收尾检查，并把定时任务改为 PAUSED")
    for bid, note in blocked:
        print(f"  [blocked] {bid}: {note}")
    return 0


def cmd_check(args) -> int:
    """一致性自检：租约与台账是否对得上（僵尸状态检测）。"""
    data = load_ledger()
    problems = []
    lease = _load(LEASE)
    doing = [s for s in data.get("steps", []) if s.get("status") == "in_progress"]

    if lease and lease_expired(lease) and doing:
        problems.append(
            f"租约已过期但仍有 in_progress 步骤 {doing} → 僵尸状态；"
            f"下一轮可 lease acquire 接管并把该步 reset 回 pending"
        )
    if lease and not lease_expired(lease) and not doing:
        problems.append("租约有效但没有 in_progress 步骤 → 上一轮可能崩在写状态之前，建议 release 后重跑该步")
    if not lease and doing:
        problems.append(f"无租约但有 in_progress 步骤 {doing} → 上次未走 release；建议 reset 该步再重跑")
    for s in data.get("steps", []):
        if s.get("status") == "done" and not s.get("verification"):
            problems.append(f"{s['id']} 标 done 但没有 verification 记录 → 证据缺失")
        if s.get("status") == "blocked" and not s.get("notes"):
            problems.append(f"{s['id']} 标 blocked 但没有原因 → 需要写明阻塞原因")
    if problems:
        print("发现问题：")
        for p in problems:
            print("  - " + p)
        return 1
    print("一致性检查通过")
    return 0


def cmd_lease(args) -> int:
    if args.lease_action == "acquire":
        lease = _load(LEASE)
        if lease and not lease_expired(lease):
            print(json.dumps({
                "acquired": False,
                "reason": "held",
                "holder": lease.get("owner"),
                "age_seconds": int(lease_age(lease)),
                "ttl_seconds": lease.get("ttl_seconds"),
            }, ensure_ascii=False))
            return 3          # 3 = 有别人在跑（不是错误）
        payload = {
            "owner": args.owner,
            "pid": os.getpid(),
            "acquired_at": now_iso(),
            "heartbeat_at": now_iso(),
            "heartbeat_ts": time.time(),
            "ttl_seconds": args.ttl,
            "step_id": args.step,
            "took_over_from": (lease or {}).get("owner") if lease else None,
        }
        _atomic_write(LEASE, payload)
        print(json.dumps({
            "acquired": True,
            "reason": "stale_takeover" if lease else "fresh",
            "owner": args.owner,
            "ttl_seconds": args.ttl,
            "step_id": args.step,
        }, ensure_ascii=False))
        return 0

    if args.lease_action == "heartbeat":
        lease = _load(LEASE)
        if not lease:
            print(json.dumps({"ok": False, "reason": "no_lease"}, ensure_ascii=False))
            return 1
        if lease.get("owner") != args.owner:
            print(json.dumps({"ok": False, "reason": "not_owner",
                              "holder": lease.get("owner")}, ensure_ascii=False))
            return 1
        lease["heartbeat_at"] = now_iso()
        lease["heartbeat_ts"] = time.time()
        _atomic_write(LEASE, lease)
        print(json.dumps({"ok": True, "owner": args.owner}, ensure_ascii=False))
        return 0

    if args.lease_action == "release":
        lease = _load(LEASE)
        if lease and lease.get("owner") != args.owner:
            print(json.dumps({"ok": False, "reason": "not_owner",
                              "holder": lease.get("owner")}, ensure_ascii=False))
            return 1
        data = load_ledger()
        touched = None
        if args.outcome in ("done", "failed", "blocked", "interrupted"):
            # 释放时同步收尾当前 in_progress 的步骤（不依赖调用方自己记得写）
            target = args.step or (lease or {}).get("step_id")
            if target:
                s = find_step(data, target)
                if args.outcome == "done":
                    s["status"] = "done"
                    s["finished_at"] = now_iso()
                    s["verification"] = args.note or s.get("verification")
                elif args.outcome == "failed":
                    s["status"] = "in_progress"
                    s["last_error"] = args.note or "failed"
                    s["attempts"] = int(s.get("attempts", 0)) + 1
                elif args.outcome == "blocked":
                    s["status"] = "blocked"
                    s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + f"[blocked] {args.note or ''}"
                    s["last_error"] = args.note
                else:  # interrupted
                    s["status"] = "pending"
                    s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + f"[interrupted] {args.note or ''}"
                touched = target
        data.setdefault("runs", []).append({
            "at": now_iso(),
            "owner": args.owner,
            "outcome": args.outcome,
            "step": touched or args.step,
            "note": (args.note or "")[:400],
        })
        save_ledger(data)
        if LEASE.exists():
            LEASE.unlink()
        print(json.dumps({"ok": True, "released": True, "outcome": args.outcome,
                          "step": touched}, ensure_ascii=False))
        return 0
    return 1


def cmd_step(args) -> int:
    data = load_ledger()
    s = find_step(data, args.id)
    if args.step_action == "start":
        s["status"] = "in_progress"
        s["started_at"] = now_iso()
        s["attempts"] = int(s.get("attempts", 0)) + 1
        if args.note:
            s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + args.note
    elif args.step_action == "finish":
        s["status"] = "done"
        s["finished_at"] = now_iso()
        s["verification"] = args.verification or s.get("verification")
        if args.note:
            s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + args.note
    elif args.step_action == "block":
        s["status"] = "blocked"
        s["last_error"] = args.note
        s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + f"[blocked] {args.note or ''}"
    elif args.step_action == "reset":
        s["status"] = "pending"
        s["started_at"] = None
        s["finished_at"] = None
        if args.note:
            s["notes"] = (s.get("notes") or "") + ("\n" if s.get("notes") else "") + f"[reset] {args.note or ''}"
    else:
        return 1
    save_ledger(data)
    print(json.dumps({"ok": True, "id": args.id, "status": s["status"],
                      "attempts": s.get("attempts", 0)}, ensure_ascii=False))
    return 0


def cmd_runs(args) -> int:
    data = load_ledger()
    runs = data.get("runs", [])[-args.limit:]
    if not runs:
        print("（无执行记录）")
        return 0
    for r in runs:
        print(f"{r.get('at')} | {r.get('owner')} | {r.get('outcome')} | {r.get('step')} | {(r.get('note') or '')[:120]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="全域优化执行计划：台账与租约工具")
    ap.add_argument(
        "--plan",
        default="default",
        help="台账名（全局选项，写在子命令前）：default=39 号计划 progress.json；"
             "其他名字=独立台账 data/plan/<name>-progress.json，如 werewolf",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="打印进度、下一步与租约状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check", help="一致性自检（僵尸状态 / 证据缺失）")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("lease", help="租约：acquire / heartbeat / release")
    pl = p.add_subparsers(dest="lease_action", required=True)
    a = pl.add_parser("acquire")
    a.add_argument("--owner", required=True)
    a.add_argument("--ttl", type=int, default=DEFAULT_TTL)
    a.add_argument("--step", default=None)
    h = pl.add_parser("heartbeat")
    h.add_argument("--owner", required=True)
    r = pl.add_parser("release")
    r.add_argument("--owner", required=True)
    r.add_argument("--outcome", required=True,
                   choices=["done", "failed", "blocked", "interrupted"])
    r.add_argument("--note", default="")
    r.add_argument("--step", default=None)
    p.set_defaults(func=cmd_lease)

    p = sub.add_parser("step", help="步骤状态迁移")
    ps = p.add_subparsers(dest="step_action", required=True)
    for act in ("start", "finish", "block", "reset"):
        q = ps.add_parser(act)
        q.add_argument("--id", required=True)
        q.add_argument("--note", default="")
        if act == "finish":
            q.add_argument("--verification", default="")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("runs", help="最近的执行记录")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)

    args = ap.parse_args()

    # 切换台账（--plan）：default 走 39 号计划的 progress.json，其余走独立台账
    global LEDGER, BACKUP, LEASE
    LEDGER, BACKUP, LEASE = plan_paths(args.plan)

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
