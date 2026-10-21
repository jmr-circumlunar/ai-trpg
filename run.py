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
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from trpg.engine.loop import GameLoop  # noqa: E402
from trpg.game import build_cards, build_llm, build_memory, build_state  # noqa: E402
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


def cmd_run(args: argparse.Namespace) -> int:
    rules = Rules(ROOT)
    pack = load_world(args.world, ROOT)
    cards = build_cards(args.players)
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

    loop = GameLoop(
        rules, cards, state, llm, seed=args.seed, conn=conn, events=store, vectors=vectors
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
    print(f"HP          {hp_line}")
    print(f"判定分布    {dict(counter)}")
    print(f"骰子状态    {loop.dice.rng_state}")
    if hasattr(llm, "memory"):
        print(f"记忆参数    {llm.memory.describe()}")

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
        db.save_state(conn, loop.state)
        conn.close()

    return 0


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
    run.set_defaults(func=cmd_run)

    worlds = sub.add_parser("worlds", help="列出可用世界包")
    worlds.set_defaults(func=cmd_worlds)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
