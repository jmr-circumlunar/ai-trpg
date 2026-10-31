"""信息泄露矩阵审计（可复跑的脚本）。

用法::

    python tools/audit_leak.py --world werewolf --players 8 --turns 4 --provider mock --seed 9

做什么：开局 → 逐回合推进 → 对**每个视角**（每个角色 + 上帝）拉**每个出口**的
真实响应 → 逐条过 `trpg/eval/leak_audit.py` 的规则 → 打印**违规清单**。

**退出码**：0 = 无违规（通过）；1 = 有违规（CI 可判）；2 = 跑不起来。

世界包 id 由命令行给，脚本本身不含任何世界词。

用法与口径见知识库 `过程记录/方案/43-信息泄露矩阵审计-W3`。
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.eval.leak_audit import (  # noqa: E402
    audit_identity_payload,
    audit_state_payload,
    summarize,
)


def _audit_session(args) -> tuple[list, dict]:
    """跑一局并逐视角审计。返回 (违规清单, 统计)。"""
    from fastapi.testclient import TestClient

    from trpg.api.app import app

    deps = importlib.import_module("trpg.api.deps")
    _is_observer = deps._is_observer
    tmpdir = tempfile.mkdtemp(prefix="leak_audit_")
    deps.DB_PATH = Path(tmpdir) / "audit.db"
    app.state.sessions = {}
    app.state.auth_required = False

    violations: list = []
    checked = {"turns": 0, "viewers": 0, "exits": 0, "payloads": 0}

    with TestClient(app) as client:
        res = client.post(
            "/api/sessions",
            json={
                "world": args.world,
                "players": args.players,
                "provider": args.provider,
                "seed": args.seed,
            },
        )
        if res.status_code != 200:
            raise RuntimeError(f"开局失败：{res.status_code} {res.text[:200]}")
        data = res.json()
        sid = data["session_id"]
        ids = [str(item["id"]) for item in data.get("characters") or []]
        if not ids:
            raise RuntimeError("开局没有角色")

        session = app.state.sessions.get(sid)
        if session is None:
            raise RuntimeError("会话没进内存表，审计中止")
        secrets = dict(getattr(session.pack, "secrets", None) or {})

        # 真相（阵营）只能从上帝视角拿：审计侧要拿真相去比对被审载荷
        camps: dict[str, str] = {}
        for cid in ids:
            ident = client.get(f"/api/sessions/{sid}/identity/{cid}").json()
            camps[cid] = str(ident.get("camp") or "")

        for turn in range(max(1, int(args.turns))):
            adv = client.post(f"/api/sessions/{sid}/advance")
            if adv.status_code != 200:
                raise RuntimeError(f"第 {turn + 1} 回合推进失败：{adv.text[:200]}")
            checked["turns"] += 1

            for viewer in ids:
                checked["viewers"] += 1
                # 出局者回到上帝视角（复盘语义）——**接口层的同一个单源判定**，
                # 不自己另写一套，否则会把合法可见判成泄露。
                observer = _is_observer(session, viewer)
                snap = client.get(f"/api/sessions/{sid}?viewer={viewer}").json()
                violations += audit_state_payload(
                    snap,
                    viewer,
                    secrets=secrets,
                    observer=observer,
                    exit_name="snapshot",
                )
                checked["exits"] += 1
                checked["payloads"] += 1

                table = client.get(f"/api/sessions/{sid}/table?viewer={viewer}").json()
                violations += audit_state_payload(
                    table,
                    viewer,
                    secrets=secrets,
                    observer=observer,
                    exit_name="table",
                )
                checked["exits"] += 1
                checked["payloads"] += 1

                for cid in ids:
                    ident = client.get(
                        f"/api/sessions/{sid}/identity/{cid}?viewer={viewer}"
                    ).json()
                    violations += audit_identity_payload(
                        ident,
                        viewer,
                        secrets=secrets,
                        observer=observer,
                        target_camp=camps.get(cid, ""),
                        viewer_camp=camps.get(viewer, ""),
                        exit_name="identity",
                    )
                    checked["exits"] += 1
                    checked["payloads"] += 1

    return violations, checked


def main(argv: list[str] | None = None) -> int:
    # Windows 默认 GBK：stdout/stderr 改成 UTF-8，输出才不会被调用方读成乱码
    for _stream in ("stdout", "stderr"):
        _s = getattr(sys, _stream, None)
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    parser = argparse.ArgumentParser(
        prog="audit_leak", description="信息泄露矩阵审计（每回合 × 每视角 × 每出口）"
    )
    parser.add_argument("--world", required=True, help="世界包 id（框架不认世界名）")
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--seed", default="9")
    parser.add_argument("--json", action="store_true", help="输出 JSON（默认输出文本）")
    args = parser.parse_args(argv)

    try:
        violations, checked = _audit_session(args)
    except Exception as exc:  # noqa: BLE001
        print(f"[audit_leak] 跑不起来：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    report = {"checked": checked, **summarize(violations)}
    if args.json:
        print(
            json.dumps(
                {
                    "checked": checked,
                    "summary": summarize(violations),
                    "violations": [item.as_dict() for item in violations],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"[audit_leak] world={args.world} 回合={checked['turns']} "
            f"视角={checked['viewers']} 出口={checked['exits']} 载荷={checked['payloads']}"
        )
        if violations:
            print(f"[audit_leak] 违规 {len(violations)} 条：")
            for item in violations:
                print(f"  - {item}")
        else:
            print("[audit_leak] 违规清单为空 → 通过")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
