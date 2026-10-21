"""业务层 AgentLLM：GM / 玩家 / 编年史的调用与降级。

与供应商无关——只认 `LLMConfig.resolve()` 给出的 ModelSpec。
每个角色可以有自己的 provider/model（角色卡 `extensions.ai_trpg.model`）。
"""

from __future__ import annotations

from typing import Any

from ..models import CharacterCard, GameState, PlayerAction, StateDelta
from ..models.enums import Intent
from . import prompts
from .config import LLMConfig, ModelSpec
from .providers import extract_json

# 各任务的输出预算下限（角色卡没指定 max_tokens 时用）
TASK_TOKENS = {"scene": 1200, "narrate": 2600, "player": 1200, "summary": 700}

DEFAULT_SKILLS = [
    "潜行", "观察", "交涉", "威吓", "攀爬", "医疗",
    "追踪", "学识", "生存", "器械", "撬锁", "意志",
]

DEFAULT_TABOOS = [
    "不涉及现实政治与真实人物",
    "不做任何性内容",
    "不写针对未成年人的暴力细节",
]


class AgentLLM:
    def __init__(
        self,
        config: LLMConfig,
        cards: dict[str, CharacterCard],
        skills: list[str] | None = None,
        taboos: list[str] | None = None,
        style: str = "冷峻、克制，少形容词",
        world_name: str = "铁谷",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.config = config
        self.cards = cards
        self.skills = skills or DEFAULT_SKILLS
        self.taboos = taboos or DEFAULT_TABOOS
        self.style = style
        self.world_name = world_name
        self.default_spec: ModelSpec = config.resolve(provider=provider, model=model)
        # CLI 覆盖：临时实验开关，优先级高于角色卡
        self._override_provider = provider
        self._override_model = model

        self.usage: dict[str, Any] = {
            "calls": 0,
            "in": 0,
            "out": 0,
            "fail": 0,
            "retry": 0,
            "net_retry": 0,
        }
        self._recent_public: list[str] = []
        self._last_error: str = ""

    # ── 基础设施 ────────────────────────────────────────────
    @property
    def model(self) -> str:
        return f"{self.default_spec.provider}/{self.default_spec.model}"

    def _count(self, result) -> None:
        usage = result.usage or {}
        self.usage["calls"] += 1
        self.usage["in"] += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        self.usage["out"] += int(usage.get("completion_tokens") or 0)

    def _chat_json(
        self, system: str, user: str, spec: ModelSpec, task: str
    ) -> dict:
        max_tokens = spec.max_tokens or TASK_TOKENS.get(task, 1200)
        adapter = self.config.adapter(spec.provider)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        try:
            result = adapter.chat(messages, spec.model, spec.temperature, max_tokens)
            self._count(result)
            return extract_json(result.content)
        except Exception as exc:
            self.usage["retry"] += 1
            retry_user = (
                f"{user}\n\n# 上一次输出不合要求\n{exc}\n请严格只输出合法 JSON，不要任何额外文字。"
            )
            result = adapter.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": retry_user}],
                spec.model,
                spec.temperature,
                max_tokens,
            )
            self._count(result)
            return extract_json(result.content)

    def _gm_system(self, state: GameState) -> str:
        return prompts.gm_system(
            self.world_name, state.world.era, self.style, self.taboos
        )

    # ── GM ──────────────────────────────────────────────────
    def describe_scene(self, state: GameState, turn: int) -> dict:
        try:
            data = self._chat_json(
                self._gm_system(state),
                prompts.gm_scene_user(state, self.cards, turn),
                self.default_spec,
                "scene",
            )
            narration = str(data.get("narration") or "").strip()
            if not narration:
                raise ValueError("narration 为空")
            self._recent_public.append(narration)
            return {"narration": narration, "hooks": data.get("hooks") or []}
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"describe_scene: {exc}"
            fallback = f"（第 {turn} 回合）四周安静得不太对劲，像是在等谁先动。"
            self._recent_public.append(fallback)
            return {"narration": fallback, "hooks": []}

    def narrate_batch(
        self,
        state: GameState,
        actions: list[PlayerAction],
        results: list,
        turn: int,
    ) -> dict:
        """一次调用处理本回合全部玩家的后果叙事。

        串行调 N 次 narrate 是 S1 调试时发现的最大瓶颈（4 人 1 回合 ≈ 98s），
        合并后每回合只剩 1 次场景 + 1 次并行思考 + 1 次合并叙事。
        """
        items = []
        for action, result in zip(actions, results):
            card = self.cards.get(action.actor_id)
            items.append(
                {
                    "actor_id": action.actor_id,
                    "label": card.name if card else action.actor_id,
                    "action": action.action,
                    "outcome": result.outcome,
                    "dice_line": (
                        f"{result.expr} 骰面 {result.rolls} → 合计 {result.total}"
                        f" vs DC {result.dc}（差 {result.margin:+d}）"
                    ),
                }
            )

        try:
            data = self._chat_json(
                self._gm_system(state),
                prompts.gm_narrate_batch_user(state, self.cards, items, turn),
                self.default_spec,
                "narrate",
            )

            narrations: dict[str, str] = {}
            for entry in data.get("narrations") or []:
                if isinstance(entry, dict) and entry.get("actor_id"):
                    text = str(entry.get("text") or "").strip()
                    if text:
                        narrations[str(entry["actor_id"])] = text
            if not narrations:
                raise ValueError("narrations 为空")

            delta_payload = data.get("state_delta")
            delta = None
            if isinstance(delta_payload, dict):
                try:
                    delta_payload.setdefault("turn", turn)
                    delta = StateDelta.model_validate(delta_payload)
                except Exception:
                    delta = None

            for text in narrations.values():
                self._recent_public.append(text)

            return {
                "narrations": narrations,
                "narration_meta": data.get("narration_meta") or {},
                "state_delta": delta,
            }
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"narrate_batch: {exc}"
            fallback = {
                it["actor_id"]: f"{it['label']} 的尝试有了结果（{it['outcome']}），场面一时僵住。"
                for it in items
            }
            return {
                "narrations": fallback,
                "narration_meta": {},
                "state_delta": None,
            }

    # ── PC（按角色卡解析 provider/model）────────────────────
    def player_action(
        self, actor_id: str, card: CharacterCard, state: GameState, turn: int
    ) -> PlayerAction:
        spec = self.config.resolve(
            card_model=card.ext.model,
            provider=self._override_provider,
            model=self._override_model,
        )
        try:
            data = self._chat_json(
                prompts.pc_system(card, self.skills),
                prompts.pc_user(state, card, self._recent_public, turn),
                spec,
                "player",
            )
            # 模型可能给出表外值（如 investigate），一律降级为 normal，不让整条行动作废
            raw_intent = str(data.get("intent") or "normal").strip().lower()
            try:
                intent = Intent(raw_intent)
            except ValueError:
                intent = Intent.normal

            return PlayerAction(
                actor_id=actor_id,
                thought=str(data.get("thought") or ""),
                action=str(data.get("action") or "").strip() or "按兵不动",
                approach=str(data.get("approach") or ""),
                skill=data.get("skill") or None,
                target=data.get("target") or None,
                intent=intent,
            )
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"player_action[{spec.provider}/{spec.model}]: {exc}"
            return PlayerAction(
                actor_id=actor_id,
                thought="（判断失误，先拖时间）",
                action="保持戒备，扫视四周的出口",
                approach="贴着墙，不抢先动",
                skill="观察",
                intent=Intent.careful,
            )

    # ── 编年史 ──────────────────────────────────────────────
    def summarize(self, state: GameState, turn: int) -> dict:
        text = self._recent_public[-1] if self._recent_public else ""
        if not text:
            return {"summaries": [], "new_entities": []}
        try:
            return self._chat_json(
                "你把游戏记录压缩成事件摘要，只输出 JSON。",
                prompts.chronicler_user(turn, text),
                self.default_spec,
                "summary",
            )
        except Exception:
            self.usage["fail"] += 1
            return {"summaries": [], "new_entities": []}
