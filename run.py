"""ai-trpg CLI。

用法：
    python run.py run --provider mock --seed 42 --turns 20 --players 4
    python run.py run --provider minimax --turns 3 --players 3
"""

from __future__ import annotations

import argparse
import sys
import uuid
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
from trpg.llm import MockLLM, MiniMaxClient  # noqa: E402
from trpg.llm.env import llm_settings  # noqa: E402
from trpg.models import CharacterRuntime, GameState, Party, Scene, WorldRef  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from trpg.storage import db  # noqa: E402
from trpg.testing import make_card  # noqa: E402

PROFESSIONS = ["盗贼", "战士", "学者", "医师", "斥候", "术士"]

# S0 无世界包，先用一组原创人设把角色区分开（目标互相冲突，AI 才不会全员配合）
PERSONAS = {
    "盗贼": ("港口区的撬锁人，左手少了两指", "话少、不信人、对货物有执念", "找回被教会没收的妹妹遗物"),
    "战士": ("退役的守备队老兵，右耳听不见", "直、护短、不肯认错", "查清三年前那场火灾是谁下的令"),
    "学者": ("替教会抄书的年轻人，眼睛不好", "好奇、迂腐、记性极好", "读到禁书区最里面那一本"),
    "医师": ("走街的游医，箱子比人还重", "温和、怕血、嘴上不饶人", "找到消失了两年的师父"),
    "斥候": ("给商队带路的外乡人", "警觉、爱开玩笑、随时准备跑", "攒够钱离开这座城"),
    "术士": ("被同行称作骗子的符术师", "浮夸、精于算计、真有点本事", "证明自己不是骗子"),
}

TABOOS = [
    "不涉及现实政治与真实人物",
    "不做任何性内容",
    "不写针对未成年人的暴力细节",
]


def build_world(seed: int, players: int):
    rules = Rules(ROOT)

    cards = {}
    for i in range(players):
        cid = f"chr_p{i + 1}"
        profession = PROFESSIONS[i % len(PROFESSIONS)]
        card = make_card(profession=profession)
        desc, persona, goal = PERSONAS[profession]
        card.data.name = f"角色{i + 1}（{profession}）"
        card.data.description = desc
        card.data.personality = persona
        card.ext.private_goal = goal
        cards[cid] = card

    state = GameState(
        session_id=f"ses_{uuid.uuid4().hex[:6]}",
        world=WorldRef(world_id="ironvale", era="medieval"),
        scene=Scene(
            location_id="loc_plaza",
            region="north_port",
            description="港城的广场，雨刚停。",
        ),
        party=Party(gold=30, supplies=4),
        characters={
            cid: CharacterRuntime(hp=card.ext.hp, sanity=card.ext.sanity)
            for cid, card in cards.items()
        },
    )
    return rules, cards, state


def build_client(args, cards, rules):
    if args.provider == "mock":
        return MockLLM(args.seed)

    settings = llm_settings(ROOT)
    return MiniMaxClient(
        api_key=settings["api_key"],
        cards=cards,
        base_url=args.base_url or settings["base_url"],
        model=args.model or settings["model"],
        model_player=args.model_player or settings["model_player"],
        skills=list(rules.skill_attr.keys()),
        taboos=TABOOS,
    )


def cmd_run(args: argparse.Namespace) -> int:
    rules, cards, state = build_world(args.seed, args.players)
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

    llm = build_client(args, cards, rules)
    model_note = getattr(llm, "model", "mock")
    print(f"provider={args.provider} model={model_note} turns={turns} players={args.players}\n")

    loop = GameLoop(rules, cards, state, llm, seed=args.seed, conn=conn)
    records = loop.run(turns)

    counter: Counter[str] = Counter()
    for rec in records:
        print(f"\n[回合 {rec.turn}] {rec.scene}")
        for actor, outcome, narration in zip(rec.actors, rec.outcomes, rec.narration):
            counter[outcome.split(" ")[0]] += 1
            if args.quiet:
                print(f"  {actor}  {outcome}")
            else:
                print(f"  {actor}  {outcome}")
                print(f"    {narration}")
        for item in rec.rejected:
            print(f"  ! 被拒 delta：{item}")

    hp_line = " ".join(f"{cid}={rt.hp}" for cid, rt in loop.state.characters.items())
    print("\n" + "=" * 60)
    print(f"回合数      {len(records)}")
    print(f"最终版本    v{loop.state.version}")
    print(f"幕次 / 张力 stage={loop.state.stage} tension={loop.state.tension}")
    print(f"HP          {hp_line}")
    print(f"判定分布    {dict(counter)}")
    print(f"骰子状态    {loop.dice.rng_state}")

    usage = getattr(llm, "usage", None)
    if usage:
        print(
            f"LLM 调用    {usage['calls']} 次｜JSON 重试 {usage['retry']}"
            f"｜网络重试 {usage.get('net_retry', 0)}｜降级 {usage['fail']}"
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
    run.add_argument("--provider", choices=["mock", "minimax"], default="mock")
    run.add_argument("--seed", default="42", help="随机种子（同 seed 骨架一致）")
    run.add_argument("--turns", type=int, default=None, help="回合数（minimax 默认 3）")
    run.add_argument("--players", type=int, default=4)
    run.add_argument("--model", default=None, help="覆盖 MINIMAX_MODEL")
    run.add_argument("--model-player", default=None, help="玩家 Agent 单独模型")
    run.add_argument("--base-url", default=None, help="覆盖 MINIMAX_BASE_URL")
    run.add_argument("--quiet", action="store_true", help="只打印判定，不打印叙事")
    run.add_argument("--db", default="run.db")
    run.add_argument("--no-db", action="store_true")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
