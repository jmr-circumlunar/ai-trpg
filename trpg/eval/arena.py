"""M16 · 纯内存批量 arena（**不落库、不走网络**）。

跑一批同 seed 的对局，采集：
- 每局的胜方、存活名单、逐回合投票与出局；
- 每局的**校准样本**（怀疑度概率 + 真值），采样点在回合结束（对应 C-5 的「天亮」采样点）。

对照基线：
- **随机基线（行为）**：把模型的投票换成随机选人，跑同一批局 → 阵营胜率基线；
- **随机基线（怀疑度）**：怀疑度改成随机 → 校准 skill score 的参照。

**零世界词**：敌对阵营由调用方给 `hostile_camps`（世界包词表），框架只比阵营 id。

doc: 03-模块/M16-可观测与评测/00-模块总述.md
module: M16
layer: L7
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..models.rng import derive
from .calibration import Pair, brier_skill_score, murphy_decomposition, vote_distribution_entropy


@dataclass
class GameResult:
    """一局的结果（纯内存，不落库）。"""

    seed: str
    winner: str = ""
    winner_camp: str = ""
    survivors: list[str] = field(default_factory=list)
    survivor_camps: dict[str, str] = field(default_factory=dict)
    votes: list[tuple[int, str, str]] = field(default_factory=list)  # (turn, voter, target)
    died: list[tuple[int, str]] = field(default_factory=list)  # (turn, cid)
    beliefs: list[Pair] = field(default_factory=list)
    first_day_survivors: int = 0
    alive_at_start: int = 0


@dataclass
class ArenaResult:
    """一批局的结果与指标。"""

    games: list[GameResult] = field(default_factory=list)
    baseline: list[GameResult] = field(default_factory=list)

    # ── 指标 ──────────────────────────────────────────────
    def camp_win_rate(self, results: list[GameResult] | None = None) -> dict[str, float]:
        """阵营胜率：每个阵营赢了几局 / 总局数。"""
        games = results if results is not None else self.games
        wins: dict[str, int] = {}
        for game in games:
            camp = game.winner_camp
            if not camp:
                continue
            wins[camp] = wins.get(camp, 0) + 1
        total = len(games) or 1
        return {camp: count / total for camp, count in sorted(wins.items())}

    def camp_survival_rate(self, results: list[GameResult] | None = None) -> dict[str, float]:
        """阵营存活率：每个阵营的存活人数 / 初始人数。"""
        games = results if results is not None else self.games
        alive: dict[str, int] = {}
        total: dict[str, int] = {}
        for game in games:
            for cid, camp in game.survivor_camps.items():
                if camp:
                    alive[camp] = alive.get(camp, 0) + 1
            for cid, camp in getattr(game, "all_camps", {}).items():
                if camp:
                    total[camp] = total.get(camp, 0) + 1
        return {
            camp: alive.get(camp, 0) / count for camp, count in sorted(total.items()) if count
        }

    def agent_win_rate(self, results: list[GameResult] | None = None) -> dict[str, float]:
        """每 agent 胜率（本步的 mock 局里 agent = 席位，胜=所在阵营获胜）。"""
        games = results if results is not None else self.games
        wins: dict[str, int] = {}
        counts: dict[str, int] = {}
        for game in games:
            for cid, camp in game.survivor_camps.items():
                counts[cid] = counts.get(cid, 0) + 1
                if camp and camp == game.winner_camp:
                    wins[cid] = wins.get(cid, 0) + 1
        return {
            cid: wins.get(cid, 0) / count for cid, count in sorted(counts.items()) if count
        }

    def calibration(self, results: list[GameResult] | None = None) -> dict:
        games = results if results is not None else self.games
        samples: list[Pair] = []
        for game in games:
            samples.extend(game.beliefs)
        decomposition = murphy_decomposition(samples)
        return {
            "samples": len(samples),
            "skill_score": brier_skill_score(samples),
            **decomposition,
        }

    def exit_proxies(self, results: list[GameResult] | None = None) -> dict:
        """阶段一三条人工判据的机器近似（B-3）。"""
        games = results if results is not None else self.games
        if not games:
            return {"first_day_survival": 0.0, "vote_hit_rate": 0.0, "vote_entropy": 0.0}
        survival = []
        hit = []
        entropy = []
        for game in games:
            survival.append(
                game.first_day_survivors / game.alive_at_start if game.alive_at_start else 0.0
            )
            dead = {cid for _, cid in game.died}
            hits = [
                1 for _, _, target in game.votes if target in dead
            ]
            hit.append(sum(hits) / len(game.votes) if game.votes else 0.0)
            entropy.append(vote_distribution_entropy([t for _, _, t in game.votes]))
        n = len(games)
        return {
            "first_day_survival": sum(survival) / n,
            "vote_hit_rate": sum(hit) / n,
            "vote_entropy": sum(entropy) / n,
        }


class _RandomVoteLLM:
    """把模型的投票换成随机（**随机基线**：同 seed 可复现）。"""

    def __init__(self, inner: Any, seed: str) -> None:
        self._inner = inner
        self._seed = seed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def vote(self, actor_id: str, card, state, turn: int, candidates=None):
        options = [str(x) for x in (candidates or []) if str(x).strip()]
        if not options:
            return None
        rng = derive(self._seed, turn, f"random-vote-{actor_id}")
        return rng.choice(options)


def run_arena(
    root: Path | str,
    *,
    world: str = "",
    players: int = 8,
    games: int = 4,
    seed: str = "42",
    provider: str = "mock",
    turns: int = 6,
    era: str = "",
    hostile_camps: tuple[str, ...] = (),
    random_baseline: bool = True,
    adjust_delta: float = 0.3,
) -> ArenaResult:
    """跑一批纯内存对局（不落库、不联网）。

    `hostile_camps` 决定「谁是敌人」（真值）；空 = 不产出校准样本（只出行为指标）。
    """
    from ..game import build_cards, build_llm, build_state
    from ..rules.loader import Rules
    from ..world import load_world

    if not world:
        raise ValueError("必须指定世界包 id（框架不认世界名）")
    base = Path(root)
    rules = Rules(base)
    pack = load_world(world, base)
    result = ArenaResult()

    for index in range(max(1, int(games))):
        game_seed = f"{seed}-{index}"
        game = _run_one(
            base,
            rules,
            pack,
            players=players,
            seed=game_seed,
            provider=provider,
            turns=turns,
            era=era,
            hostile_camps=hostile_camps,
            adjust_delta=adjust_delta,
        )
        result.games.append(game)
        if random_baseline:
            result.baseline.append(
                _run_one(
                    base,
                    rules,
                    pack,
                    players=players,
                    seed=game_seed,
                    provider=provider,
                    turns=turns,
                    era=era,
                    hostile_camps=hostile_camps,
                    adjust_delta=adjust_delta,
                    random_votes=True,
                )
            )
    return result


def _run_one(
    base: Path,
    rules,
    pack,
    *,
    players: int,
    seed: str,
    provider: str,
    turns: int,
    era: str,
    hostile_camps: tuple[str, ...],
    adjust_delta: float,
    random_votes: bool = False,
) -> GameResult:
    """跑一局（纯内存）：建卡 → 建 state → 跑回合 → 采集。"""
    from ..engine.loop import GameLoop
    from ..game import build_cards, build_llm, build_state

    cards = build_cards(players, pack, seed)
    state = build_state(pack, cards, era or None, seed=seed)
    llm = build_llm(base, rules, pack, cards, provider, None, seed, "standard")
    if random_votes:
        llm = _RandomVoteLLM(llm, seed)

    loop = GameLoop(
        rules,
        cards,
        state,
        llm,
        seed=seed,
        conn=None,
        pack=pack,
    )
    records = asyncio.run(loop.run(turns))

    out = GameResult(seed=seed)
    out.alive_at_start = len(state.characters)
    camps = {cid: str(getattr(rt, "camp", "") or "") for cid, rt in state.characters.items()}
    out.all_camps = camps  # type: ignore[attr-defined]
    for rec in records:
        for vote in rec.votes or []:
            out.votes.append((int(rec.turn), str(vote.get("voter") or ""), str(vote.get("target") or "")))
        for cid in rec.deaths or []:
            name = cid
            if isinstance(cid, dict):
                name = cid.get("id") or cid.get("actor_id") or cid.get("cid") or ""
            out.died.append((int(rec.turn), str(name)))
        if rec.turn == 1:
            out.first_day_survivors = len(
                [cid for cid, rt in state.characters.items() if str(getattr(rt, "position", "")).endswith("scene")]
            )
    out.winner = str(getattr(state, "winner", "") or "")
    out.survivors = [
        cid for cid, rt in state.characters.items() if str(getattr(rt, "position", "")).endswith("scene")
    ]
    out.survivor_camps = {cid: camps.get(cid, "") for cid in out.survivors}
    # 胜方：优先用 state.winner；没有就按「某个阵营已全灭」判定（否则算未结束）
    alive_camps = {c for c in out.survivor_camps.values() if c}
    if out.winner:
        out.winner_camp = out.winner
    elif len(alive_camps) == 1:
        out.winner_camp = next(iter(alive_camps))
    else:
        out.winner_camp = ""
    out.beliefs = _belief_samples(out, camps, hostile_camps, adjust_delta, seed)
    return out


def _belief_samples(
    game: GameResult,
    camps: dict[str, str],
    hostile_camps: tuple[str, ...],
    adjust_delta: float,
    seed: str,
) -> list[Pair]:
    """采集校准样本：怀疑度概率 + 真值（是不是敌人）。

    怀疑度用**四步法的简化版**（S2-3 的先验 + 公开投票微调 + 守恒）：
    先验 = 剩余敌数 / 候选数；投票投给后来出局的人 → 加怀疑；投给活到最后的人 → 减怀疑。
    """
    if not hostile_camps:
        return []
    alive = list(game.survivors)
    if len(alive) < 2:
        return []
    candidates = [cid for cid in alive]
    if len(candidates) < 2:
        return []
    enemy_count = sum(1 for cid in alive if camps.get(cid, "") in hostile_camps)

    dead = {cid for _, cid in game.died}
    votes_for: dict[str, int] = {}
    for _, _, target in game.votes:
        votes_for[target] = votes_for.get(target, 0) + 1

    base = enemy_count / len(candidates)
    adjustments: dict[str, float] = {}
    for cid in candidates:
        delta = 0.0
        if cid in dead:
            delta += adjust_delta
        elif votes_for.get(cid, 0) > 0:
            delta -= adjust_delta
        adjustments[cid] = delta

    from ..engine.beliefs import compute_beliefs

    computed = compute_beliefs(
        candidates=candidates,
        enemy_count=enemy_count,
        adjustments=adjustments,
        params=None,
    )
    samples: list[Pair] = []
    for cid in candidates:
        p = float(computed.get(cid, {}).get("confidence", base) or 0.0)
        y = 1 if camps.get(cid, "") in hostile_camps else 0
        samples.append(Pair(p=p, y=y, group=cid))
    return samples


__all__ = ["ArenaResult", "GameResult", "run_arena"]
