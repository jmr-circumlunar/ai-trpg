"""M16 · 评测三条命令的 CLI（`python -m trpg.eval <命令>`）。

纯内存批量：arena / leaderboard / calibrate。
不落库、不走网络；同 seed 同结果（跨批次可比）。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from .arena import run_arena
from .calibration import reliability_table


def _hostile_camps(text: str) -> tuple[str, ...]:
    return tuple(x for x in (text or "").replace(",", " ").split() if x)


def _run(args) -> dict:
    result = run_arena(
        ROOT,
        world=args.world,
        players=args.players,
        games=args.games,
        seed=args.seed,
        provider=args.provider,
        turns=args.turns,
        hostile_camps=_hostile_camps(args.hostile_camps),
        random_baseline=not args.no_baseline,
    )
    return {
        "result": result,
        "camp_win_rate": result.camp_win_rate(),
        "baseline_camp_win_rate": result.camp_win_rate(result.baseline),
        "camp_survival_rate": result.camp_survival_rate(),
        "agent_win_rate": result.agent_win_rate(),
        "exit_proxies": result.exit_proxies(),
        # 随机基线的**同一组指标**：没有基线对照，数值没有意义
        "baseline_exit_proxies": result.exit_proxies(result.baseline),
        "skill_proxies": result.skill_proxies(),
        "baseline_skill_proxies": result.skill_proxies(result.baseline),
        "calibration": result.calibration(),
    }


def _write_out(args, payload: dict) -> None:
    """把指标落盘（`--out`）：**可跨轮对比**的证据文件。

    落盘内容 = 指标 + 随机基线对照 + 跑动参数（seed / games / turns …）。
    """
    path = str(getattr(args, "out", "") or "").strip()
    if not path:
        return
    target = Path(path)
    if target.parent and str(target.parent):
        target.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in payload.items() if k != "result"}
    data["meta"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "world": getattr(args, "world", ""),
        "players": getattr(args, "players", 0),
        "games": getattr(args, "games", 0),
        "turns": getattr(args, "turns", 0),
        "seed": getattr(args, "seed", ""),
        "provider": getattr(args, "provider", ""),
        "hostile_camps": _hostile_camps(getattr(args, "hostile_camps", "")),
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] 指标已落盘：{target}")


def cmd_arena(args) -> int:
    data = _run(args)
    print(json.dumps({k: v for k, v in data.items() if k != "result"}, ensure_ascii=False, indent=2))
    _write_out(args, data)
    return 0


def cmd_leaderboard(args) -> int:
    """排行榜：**默认输出 Markdown**（可直接落知识库），`--format json` 给机器读。"""
    data = _run(args)
    rows = sorted(
        data["agent_win_rate"].items(), key=lambda kv: (-kv[1], kv[0])
    )
    fmt = str(getattr(args, "format", "markdown") or "markdown").lower()
    if fmt == "json":
        print(json.dumps({"leaderboard": rows, "baseline": data["baseline_camp_win_rate"]}, ensure_ascii=False, indent=2))
        return 0
    print(leaderboard_markdown(rows, data["baseline_camp_win_rate"], data["camp_win_rate"]))
    _write_out(args, data)
    return 0


def leaderboard_markdown(
    rows: list[tuple[str, float]],
    baseline: dict[str, float],
    camp_win_rate: dict[str, float] | None = None,
) -> str:
    """排行榜 → Markdown 表格（可直接贴进知识库笔记）。

    为什么要有 Markdown：评测结论要**留在文档里**才能被复盘与引用，
    JSON 只能进机器。表格里同时给出**随机基线**对照 —— 没有基线的胜率没有意义。
    """
    lines: list[str] = []
    lines.append("| 名次 | agent | 胜率 |")
    lines.append("|---|---|---|")
    for index, (agent, rate) in enumerate(rows, 1):
        lines.append(f"| {index} | {agent} | {rate:.1%} |")
    if not rows:
        lines.append("| — | （无数据） | — |")
    lines.append("")
    if camp_win_rate:
        lines.append("**阵营胜率**：" + "、".join(f"{k} {v:.1%}" for k, v in sorted(camp_win_rate.items())))
    lines.append("")
    if baseline:
        lines.append("**随机基线（对照）**：" + "、".join(f"{k} {v:.1%}" for k, v in sorted(baseline.items())))
    else:
        # 没有基线也要**说出来**：批次太短没跑出完整对局时，胜率没有对照就没有意义
        lines.append("**随机基线（对照）**：本批无完整对局（无胜方），暂无可对照基线")
    return "\n".join(lines)


def cmd_calibrate(args) -> int:
    data = _run(args)
    samples = []
    for game in data["result"].games:
        samples.extend(game.beliefs)
    payload = {
        "calibration": data["calibration"],
        "exit_proxies": data["exit_proxies"],
        "reliability": reliability_table(samples),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    _write_out(args, data)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 默认 GBK：stdout/stderr 都改成 UTF-8，JSON 输出才不会被调用方读成乱码
    for _stream in ("stdout", "stderr"):
        _s = getattr(sys, _stream, None)
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8")
            except Exception:
                pass
    parser = argparse.ArgumentParser(prog="trpg.eval", description="评测三件套（纯内存）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func, help_text in (
        ("arena", cmd_arena, "跑一批对局并出指标（含随机基线对照）"),
        ("leaderboard", cmd_leaderboard, "每 agent 胜率排行"),
        ("calibrate", cmd_calibrate, "怀疑度校准报告"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--world", required=True, help="世界包 id（必填；框架不认世界名）")
        p.add_argument("--players", type=int, default=8)
        p.add_argument("--games", type=int, default=4)
        p.add_argument("--seed", default="42")
        p.add_argument("--provider", default="mock")
        p.add_argument("--turns", type=int, default=6)
        p.add_argument(
            "--hostile-camps",
            default="",
            help="敌对阵营 id（空格分隔）；来自世界包词表，框架不解释含义",
        )
        p.add_argument("--no-baseline", action="store_true", help="不跑随机基线")
        p.add_argument(
            "--out",
            default="",
            help="把指标 JSON 落盘到该路径（含随机基线对照与跑动参数，可跨轮对比）",
        )
        if name == "leaderboard":
            p.add_argument(
                "--format",
                default="markdown",
                choices=("markdown", "json"),
                help="输出格式（默认 Markdown，可直接落知识库）",
            )
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001
        # 退出码语义：**0 成功，非 0 失败**（CI 要能判）
        print(f"[eval] {args.cmd} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
