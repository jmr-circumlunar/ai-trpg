"""ai-trpg CLI。

用法：
    python run.py worlds
    python run.py run --provider mock --turns 20 --players 4
    python run.py run --provider auto --turns 3 --players 3 --world ironvale
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，中文会乱码
for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.game import (  # noqa: E402
    build_cards,
    build_director,
    build_llm,
    build_memory,
    build_state,
    seed_lore,
)
from trpg.rules.loader import Rules  # noqa: E402
from trpg.storage import db  # noqa: E402
from trpg.storage.memory import EventStore  # noqa: E402
from trpg.world import discover_worlds, load_world  # noqa: E402


def cmd_worlds(_: argparse.Namespace) -> int:
    worlds = discover_worlds(ROOT)
    if not worlds:
        print("worlds/ 下没有可用的世界包")
        return 1
    for wid in worlds:
        try:
            pack = load_world(wid, ROOT)
        except Exception as exc:  # noqa: BLE001
            print(f"{wid:16s} 加载失败：{exc}")
            continue
        print(
            f"{wid:16s} {pack.name} | eras: {', '.join(pack.eras)}"
            f" | regions: {', '.join(pack.regions)}"
            f" | seeds: {len(pack.seeds)} items: {len(pack.items)} npcs: {len(pack.npcs)}"
        )
    return 0


def _has_combat_or_judge(pack) -> bool:
    """这个世界有「血量」这个维度吗？

    有战斗或判定的世界（传统跑团）才有 HP 的语义；纯信息对抗局里 HP 恒等于初值，
    展示它只会掩盖真正的生死状态（`position == dead`）。
    """
    try:
        from trpg.rulespack import assemble

        caps = assemble(pack)
        return caps.has("combat") or caps.has("judge")
    except Exception:  # noqa: BLE001  装配失败时按「有」处理（信息更全）
        return True


def cmd_run(args: argparse.Namespace) -> int:
    rules = Rules(ROOT)
    pack = load_world(args.world, ROOT)
    cards = build_cards(args.players, pack)
    state = build_state(pack, cards, args.era)
    turns = args.turns if args.turns is not None else (3 if args.provider != "mock" else 20)

    conn = None
    if not args.no_db:
        conn = db.connect(args.db)
        db.init_db(conn)
        db.save_session(
            conn,
            state.session_id,
            args.seed,
            state.world.world_id,
            state.world.era,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            name=f"{args.provider} 局",
        )
        db.save_state(conn, state)

    store = EventStore(conn, state.session_id)
    vectors = build_memory(ROOT, args.provider, conn, state.session_id)
    lore_count = seed_lore(vectors, pack)
    llm = build_llm(
        ROOT,
        rules,
        pack,
        cards,
        args.provider,
        args.model,
        args.seed,
        args.reasoning_effort,
        events=store,
        vectors=vectors,
    )
    print(
        f"world={pack.world_id}({pack.name}) era={state.world.era} region={state.scene.region}"
        f" | provider={args.provider} model={getattr(llm, 'model', 'mock')}"
        f" turns={turns} players={args.players}\n"
    )

    director = build_director(
        ROOT,
        rules,
        pack,
        cards,
        args.provider,
        args.seed,
        args.model,
        args.reasoning_effort,
    )
    loop = GameLoop(
        rules,
        cards,
        state,
        llm,
        seed=args.seed,
        conn=conn,
        force_seed=args.force_seed,
        events=store,
        vectors=vectors,
        pack=pack,
        director=director,
        # 默认 LangGraph；`--engine manual` 可切回手写 for 循环做对照排查
        engine=getattr(args, "engine", "graph"),
    )
    records = asyncio.run(loop.run(turns))

    counter: Counter[str] = Counter()
    for rec in records:
        print(f"\n[回合 {rec.turn}] {rec.scene}")
        for move in rec.npc_moves:
            line = f"  〔NPC〕{move.get('name')}：{move.get('action') or '无动作'}"
            if move.get("speech"):
                line += f"　说：「{move['speech']}」"
            print(line)
        for i, (actor, outcome, narration) in enumerate(
            zip(rec.actors, rec.outcomes, rec.narration)
        ):
            counter[outcome.split(" ")[0]] += 1
            print(f"  {actor}  {outcome}")
            if not args.quiet:
                thought = rec.thoughts[i] if i < len(rec.thoughts) else ""
                act = rec.actions[i] if i < len(rec.actions) else ""
                if thought:
                    print(f"    心中：{thought}")
                if act:
                    print(f"    行动：{act}")
                print(f"    {narration}")
        for item in rec.rejected:
            print(f"  ! 被拒 delta：{item}")

        if not args.quiet and hasattr(llm, "recall_log"):
            for entry in [x for x in llm.recall_log if x.get("turn") == rec.turn]:
                dbg = entry.get("debug") or {}
                vhits = dbg.get("vector_hits") or []
                print(
                    f"    〔回忆〕{entry['char_id']}：注入 {dbg.get('injected', 0)} 条"
                    f"（结构化候选 {dbg.get('structured_candidates', 0)}，向量命中 {len(vhits)}）"
                )
                for hit in vhits[:2]:
                    print(f"      · 向量 {hit['score']:.3f} {hit['text'][:40]}")

    hp_line = " ".join(f"{cid}={rt.hp}" for cid, rt in loop.state.characters.items())
    print("\n" + "=" * 60)
    print(f"回合数      {len(records)}")
    print(f"最终版本    v{loop.state.version}")
    print(f"幕次 / 张力 stage={loop.state.stage} tension={loop.state.tension}")
    _c = loop.state.clock
    print(
        f"游戏时间    第 {_c.day} 天 {_c.hour:02d}:{_c.minute:02d}"
        f"（{rules.phase_label(rules.phase_of(_c.hour))}）"
    )
    # HP 只在有战斗/判定的世界才展示：狼人杀这类纯信息对抗局里 HP 恒等于初值，
    # 打出来是噪音（实测 6 回合全是 11，反而让人以为「没打中」）。
    # 那种世界改看出局名单——那才是它们真正的生死状态。
    if _has_combat_or_judge(pack):
        print(f"HP          {hp_line}")
    else:
        from trpg.models.state import is_eliminated

        out = [cid for cid, rt in loop.state.characters.items() if is_eliminated(rt)]
        print(f"出局        {'、'.join(out) or '无'}")
    print(f"判定分布    {dict(counter)}")
    print(f"骰子状态    {loop.dice.rng_state}")
    from trpg.memory import MemorySettings  # noqa: PLC0415

    mem = getattr(llm, "memory", None) or MemorySettings.load(ROOT)
    print(f"记忆参数    {mem.describe()}")
    print(f"世界知识    core {len(pack.core_text)} 字常驻 / archive {lore_count} 篇进向量")

    if records:
        avg_ms = sum(r.elapsed_ms for r in records) / len(records)
        avg_declare = sum(r.declare_ms for r in records) / len(records)
        print(f"回合耗时    平均 {avg_ms:.0f}ms（玩家并行阶段 {avg_declare:.0f}ms）")

    usage = getattr(llm, "usage", None)
    if usage:
        net_retry = int(getattr(getattr(llm, "config", None), "net_retry", 0) or 0)
        print(
            f"LLM 调用    {usage['calls']} 次｜JSON 重试 {usage['retry']}"
            f"｜网络重试 {net_retry}｜降级 {usage['fail']}"
            f"｜tokens in {usage['in']} / out {usage['out']}"
        )
        if usage["fail"]:
            print(f"最近错误    {getattr(llm, '_last_error', '')}")

    if conn is not None:
        print(f"落库        {Path(args.db).resolve()}")
        # 带上回合号：trace / reset 都靠 world_state.turn 找快照
        db.save_state(conn, loop.state, loop.next_turn - 1)
        # 局末经验提炼：与 API 的 `_finish` 共用 `reflect_all`。
        # 不在这里做的话，CLI 跑出来的局**永远学不到东西**（实测 6 回合后表是空的）。
        from trpg.engine.reflection import char_to_agent, reflect_all

        learned = reflect_all(
            loop,
            records,
            f"第 {len(records)} 回合结束，胜者：{loop.state.winner or '未决'}",
            char_to_agent(loop.cards, pack.world_id),
            conn,
            pack.world_id,
            pack=pack,
        )
        if learned:
            print(f"经验        写入 {learned} 条")
        conn.close()

    return 0


def cmd_eval(args) -> int:
    """评测子命令（S3-2）：把参数转交给 `trpg.eval` 的 CLI，退出码原样透传。

    为什么不重复实现：评测逻辑全在 `trpg/eval/`（纯内存、离线），
    这里只做**入口**——一份参数、一套退出码语义（0 成功 / 非 0 失败）。
    """
    from trpg.eval.cli import main as eval_main

    argv = [args.cmd]
    for flag, value in (
        ("--world", args.world),
        ("--players", args.players),
        ("--games", args.games),
        ("--seed", args.seed),
        ("--provider", args.provider),
        ("--turns", args.turns),
        ("--hostile-camps", args.hostile_camps),
        ("--out", getattr(args, "out", "")),
    ):
        argv.extend([flag, str(value)])
    if args.no_baseline:
        argv.append("--no-baseline")
    if hasattr(args, "format"):
        argv.extend(["--format", args.format])
    return int(eval_main(argv))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trpg", description="AI 跑团引擎")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="跑一局")
    run.add_argument(
        "--provider",
        default="mock",
        help="mock | auto | minimax | deepseek | qwen | ollama（见 config/providers.yaml）",
    )
    run.add_argument("--seed", default="42", help="随机种子（同 seed 骨架一致）")
    run.add_argument("--turns", type=int, default=None, help="回合数（真实模型默认 3）")
    run.add_argument("--players", type=int, default=4)
    run.add_argument("--world", default="ironvale", help="worlds/<id>/ 里的世界包")
    run.add_argument(
        "--engine",
        default="graph",
        choices=("graph", "manual"),
        help="回合调度器：graph=LangGraph（默认），manual=手写 for 循环（排错对照用）",
    )
    run.add_argument("--era", default=None, help="覆盖世界包默认 era")
    run.add_argument("--model", default=None, help="覆盖默认模型名")
    run.add_argument(
        "--reasoning-effort",
        default=None,
        help="思考深度 low|medium|high（仅推理模型有效，覆盖全局默认）",
    )
    run.add_argument("--quiet", action="store_true", help="只打印判定，不打印叙事")
    run.add_argument("--db", default="run.db")
    run.add_argument("--no-db", action="store_true")
    run.add_argument(
        "--force-seed",
        default="",
        help="调试：第 1 回合强制使用指定事件种子 id（如 evt_first_field 验证战斗链路）",
    )
    run.set_defaults(func=cmd_run)

    worlds = sub.add_parser("worlds", help="列出可用世界包")
    worlds.set_defaults(func=cmd_worlds)

    # 评测三件套（S3-2）：arena / leaderboard / calibrate
    # 纯内存、离线可跑（mock provider 不需要 key），可进 CI。
    for name, help_text in (
        ("arena", "跑一批对局并出指标（含随机基线对照）"),
        ("leaderboard", "每 agent 胜率排行（默认 Markdown）"),
        ("calibrate", "怀疑度校准报告"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--world", default="ironvale", help="世界包 id（框架不认世界名）")
        p.add_argument("--players", type=int, default=8)
        p.add_argument("--games", type=int, default=4)
        p.add_argument("--seed", default="42", help="随机种子（同 seed 同结果）")
        p.add_argument("--provider", default="mock", help="mock | auto | 具体 provider")
        p.add_argument("--turns", type=int, default=6)
        p.add_argument(
            "--hostile-camps",
            default="",
            help="敌对阵营 id（空格分隔，来自世界包词表）",
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
        p.set_defaults(func=cmd_eval)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
