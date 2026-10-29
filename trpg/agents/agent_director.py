"""Director Agent：本局的导演。只输出结构化决策，不写叙事、不给数值。

一期职责与边界：

| 谁 | 决定什么 |
|---|---|
| **Director** | 本局主线是什么；这一回合该不该有固定事件；幕次什么时候推 |
| **GM** | 把已定的事实写成场景与后果 |
| **代码** | 成不成（骰子）、给多少（数值）、谁在场合法（时间窗口） |

Director 有两种实现：
- `Director`：纯规则（离线 / mock / LLM 失败兜底），主线从事件种子骨架里取
- `LLMDirector`：调模型生成更贴合的 1 主线 + 1~2 支线，失败即回落纯规则

固定事件来自世界包 `timeline.yaml` 的 `fixed_events`（如"第 1 回合开学典礼"），
由代码按 turn 精确匹配——**模型不能改固定事件的时机**。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import random
from typing import Any

from ..models import CharacterCard, GameState
from ..models.rng import derive
from . import prompts
from .spec import LLMConfig
from .providers import extract_json


class Director:
    """纯规则导演：不调模型，结果可复现。"""

    def __init__(self, pack=None, seed: str | int = "0") -> None:
        self.pack = pack
        self.rng = derive(seed, "director")
        self.log: list[dict] = []

    # ── 开局：主线 ──────────────────────────────────────────
    def open_session(self, state: GameState, cards: dict[str, CharacterCard]) -> list[dict]:
        """从事件种子里挑 2~3 条骨架当开局的 quests。"""
        seeds = list(getattr(self.pack, "seeds", None) or [])
        picked = self.rng.sample(seeds, k=min(3, len(seeds))) if seeds else []
        quests: list[dict] = []
        for idx, seed in enumerate(picked):
            title = str(seed.get("skeleton") or seed.get("id") or f"任务{idx + 1}")
            quests.append(
                {
                    "id": f"q_{seed.get('id') or idx + 1}",
                    "title": title[:18],
                    "status": "active",
                    "progress": 0,
                }
            )
        self.log.append(
            {"turn": 0, "source": "rule", "quests": [q["title"] for q in quests]}
        )
        return quests

    # ── 每回合：固定事件 / 幕次 ─────────────────────────────
    def fixed_event(self, state: GameState, turn: int) -> dict | None:
        """该回合被锁定的固定事件（世界包声明，代码匹配，模型改不了）。"""
        if self.pack is None:
            return None
        point = self.pack.timeline_point(state.world.timeline)
        for item in point.get("fixed_events") or []:
            if int(item.get("turn") or -1) != int(turn):
                continue
            seed_id = str(item.get("id") or "")
            for seed in getattr(self.pack, "seeds", None) or []:
                if str(seed.get("id")) == seed_id:
                    return seed
        return None

    def plan_turn(self, state: GameState, turn: int) -> dict[str, Any]:
        plan: dict[str, Any] = {
            "turn": turn,
            "source": "rule",
            "seed": None,
            "advance_stage": False,
        }
        fixed = self.fixed_event(state, turn)
        if fixed is not None:
            plan["seed"] = fixed
            plan["source"] = "fixed_event"
        # 兜底：每 10 回合推一幕（第 1 项之前就在这么做，保留为默认节奏）
        if turn % 10 == 0 and state.stage < 4:
            plan["advance_stage"] = True
        self.log.append({k: v for k, v in plan.items() if k != "seed"})
        return plan


class LLMDirector(Director):
    """让模型设计主线；任何失败都回落到纯规则导演。"""

    def __init__(
        self,
        config: LLMConfig,
        pack=None,
        seed: str | int = "0",
        provider: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        world_name: str = "",
        style: str = "",
        taboos: list[str] | None = None,
    ) -> None:
        super().__init__(pack, seed)
        self.config = config
        self.spec = config.resolve(
            provider=provider, model=model, reasoning_effort=reasoning_effort
        )
        self.world_name = world_name or getattr(pack, "name", "") or "这个世界"
        self.style = style
        self.taboos = list(taboos or [])
        self.usage: dict[str, int] = {"calls": 0, "fail": 0}

    def _chat_json(self, system: str, user: str, max_tokens: int = 900) -> dict:
        adapter = self.config.adapter(self.spec.provider)
        override = (
            {"reasoning_effort": self.spec.reasoning_effort}
            if self.spec.reasoning_effort
            else None
        )
        result = adapter.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            self.spec.model,
            self.spec.temperature,
            max_tokens,
            override,
        )
        self.usage["calls"] += 1
        return extract_json(result.content)

    def open_session(self, state: GameState, cards: dict[str, CharacterCard]) -> list[dict]:
        try:
            data = self._chat_json(
                prompts.director_system(self.world_name, self.style, self.taboos),
                prompts.director_open_user(
                    state,
                    cards,
                    seeds=list(getattr(self.pack, "seeds", None) or []),
                    npcs=self.pack.npcs_for(state.world.timeline) if self.pack else [],
                ),
            )
        except Exception:  # noqa: BLE001
            self.usage["fail"] += 1
            return super().open_session(state, cards)

        raw = data.get("quests")
        if not isinstance(raw, list) or not raw:
            return super().open_session(state, cards)

        quests: list[dict] = []
        for idx, item in enumerate(raw[:3]):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            quests.append(
                {
                    "id": str(item.get("id") or f"q_{idx + 1}"),
                    "title": title[:18],
                    "status": "active",
                    "progress": 0,
                }
            )
        if not quests:
            return super().open_session(state, cards)

        self.log.append(
            {
                "turn": 0,
                "source": "llm",
                "main": str(data.get("main") or "")[:60],
                "quests": [q["title"] for q in quests],
            }
        )
        return quests
