"""业务层 AgentLLM：GM / 玩家 / 编年史的调用与降级。

与供应商无关——只认 `LLMConfig.resolve()` 给出的 ModelSpec。
每个角色可以有自己的 provider/model（角色卡 `extensions.ai_trpg.model`）。
"""

from __future__ import annotations

import random
from typing import Any

from ..models import CharacterCard, GameState, PlayerAction, StateDelta
from ..models.enums import Intent
from ..memory.recall import hybrid_recall
from ..memory.settings import MemorySettings
from . import prompts
from .config import LLMConfig, ModelSpec
from .providers import extract_json

# 各任务的输出预算下限（角色卡没指定 max_tokens 时用）
TASK_TOKENS = {"scene": 1200, "narrate": 2600, "player": 1200, "summary": 700}

DEFAULT_SKILLS = [
    "潜行", "观察", "交涉", "威吓", "攀爬", "医疗",
    "追踪", "学识", "生存", "器械", "撬锁", "意志",
]

# 每多少回合压一条回顾、保留几条
SUMMARY_EVERY = 5
SUMMARY_KEEP = 4

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
        reasoning_effort: str | None = None,
        rules=None,
        events=None,
        npcs: list[dict] | None = None,
        vectors=None,
        seeds: list[dict] | None = None,
        seed: str | int = 0,
        memory: MemorySettings | None = None,
    ) -> None:
        self.config = config
        self.cards = cards
        self.skills = skills or DEFAULT_SKILLS
        self.taboos = taboos or DEFAULT_TABOOS
        self.style = style
        self.world_name = world_name
        self.rules = rules
        self.events = events
        self.npcs = list(npcs or [])
        self.vectors = vectors
        self.recall_log: list[dict] = []  # 每次回忆的记录，供 trace 排查
        self._last_recall = None
        self.memory = memory or MemorySettings.load(getattr(config, "root", "."))
        self.seeds = list(seeds or [])
        self.seed_rng = random.Random(f"{seed}:seeds")
        self._used_seeds: set[str] = set()
        self.seed_log: list[dict] = []
        self.default_spec: ModelSpec = config.resolve(
            provider=provider, model=model, reasoning_effort=reasoning_effort
        )
        # CLI 覆盖：临时实验开关，优先级高于角色卡
        self._override_provider = provider
        self._override_model = model
        self._override_effort = reasoning_effort

        self.usage: dict[str, Any] = {
            "calls": 0,
            "in": 0,
            "out": 0,
            "fail": 0,
            "retry": 0,
            "net_retry": 0,
        }
        self._recent_public: list[str] = []
        self._summaries: list[str] = []  # 分章回顾（每 SUMMARY_EVERY 回合压一条，留最近 4 条）
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
        # 思考深度按 spec 覆盖（角色卡或全局），空则不传
        override = (
            {"reasoning_effort": spec.reasoning_effort}
            if spec.reasoning_effort
            else None
        )

        try:
            result = adapter.chat(
                messages, spec.model, spec.temperature, max_tokens, override
            )
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
                override,
            )
            self._count(result)
            return extract_json(result.content)

    def _gm_system(self, state: GameState) -> str:
        return prompts.gm_system(
            self.world_name, state.world.era, self.style, self.taboos
        )

    def _phase_label(self, state: GameState) -> str:
        if self.rules is None:
            return ""
        try:
            return self.rules.phase_label(self.rules.phase_of(state.clock.hour))
        except Exception:  # noqa: BLE001
            return ""

    def _step_options(self) -> list[dict]:
        return list(getattr(self.rules, "allowed_steps", []) or [])

    def _recall(self, actor_id: str, turn: int = 0) -> list[str]:
        """回忆：结构化（重要/最近）+ 向量（情境联想）→ RRF 融合。

        只查 `actor_id` 自己的记录——隔离靠物理过滤，不靠 prompt 叮嘱。
        每次调用都留一条日志，便于判断「没召回到」还是「召回到了但没用」。
        """
        if self.events is None and self.vectors is None:
            return []

        query = self._recent_public[-1] if self._recent_public else ""
        result = hybrid_recall(query, actor_id, self.events, self.vectors)
        self._last_recall = result
        self.recall_log.append(
            {
                "turn": turn,
                "char_id": actor_id,
                "query": query[:80],
                "debug": result.debug,
                "items": [
                    {"source": i.source, "score": round(i.score, 4), "text": i.text[:60]}
                    for i in result.items
                ],
            }
        )
        self.recall_log = self.recall_log[-50:]
        return result.texts(limit=6)

    # ── GM ──────────────────────────────────────────────────
    def _validate_npcs(self, raw) -> list[dict]:
        """GM 申报的 NPC 必须在 npcs.yaml 里，最多 2 个。"""
        if not isinstance(raw, list) or not self.npcs:
            return []
        by_id = {str(n.get("id")): n for n in self.npcs if n.get("id")}
        out, seen = [], set()
        for item in raw:
            nid = str(item)
            if nid in by_id and nid not in seen:
                seen.add(nid)
                out.append(by_id[nid])
        return out[:2]

    def _pick_seed(self, state: GameState) -> dict | None:
        """按 era / 幕次筛可用种子，按权重抽一个，同一局内不重复。"""
        pool = []
        for seed in self.seeds:
            sid = str(seed.get("id") or "")
            if not sid or sid in self._used_seeds:
                continue
            eras = seed.get("era") or []
            if eras and state.world.era not in eras:
                continue
            lo, hi = (seed.get("stage_range") or [1, 4])[:2]
            if not (lo <= state.stage <= hi):
                continue
            pool.append(seed)

        if not pool:
            return None
        weights = [float(s.get("weight_base", 1.0) or 1.0) for s in pool]
        chosen = self.seed_rng.choices(pool, weights=weights)[0]
        self._used_seeds.add(str(chosen.get("id")))
        self.seed_log.append({"id": chosen.get("id"), "type": chosen.get("type")})
        return chosen

    def describe_scene(self, state: GameState, turn: int) -> dict:
        seed_hint = self._pick_seed(state)
        try:
            data = self._chat_json(
                self._gm_system(state),
                prompts.gm_scene_user(
                    state,
                    self.cards,
                    turn,
                    self._phase_label(state),
                    self.npcs,
                    seed_hint,
                ),
                self.default_spec,
                "scene",
            )
            narration = str(data.get("narration") or "").strip()
            if not narration:
                raise ValueError("narration 为空")
            self._recent_public.append(narration)
            return {
                "narration": narration,
                "hooks": data.get("hooks") or [],
                "present_npcs": self._validate_npcs(data.get("present_npcs")),
            }
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"describe_scene: {exc}"
            fallback = f"（第 {turn} 回合）四周安静得不太对劲，像是在等谁先动。"
            self._recent_public.append(fallback)
            return {"narration": fallback, "hooks": [], "present_npcs": []}

    def narrate_batch(
        self,
        state: GameState,
        actions: list[PlayerAction],
        results: list,
        turn: int,
        npc_moves: list[dict] | None = None,
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
                prompts.gm_narrate_batch_user(
                    state,
                    self.cards,
                    items,
                    turn,
                    self._phase_label(state),
                    self._step_options(),
                    npc_moves,
                ),
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
                "npc_moves": npc_moves or [],
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
    # ── 玩家 Agent：ReAct（思考 → 调 tool / 提交行动）─────────
    MAX_STEPS = 3

    def _tool_call(
        self, actor_id: str, card: CharacterCard, state: GameState, tool: str,
        args: dict, turn: int,
    ):
        if tool == "recall":
            query = str(args.get("query") or "").strip()
            result = hybrid_recall(
                query,
                actor_id,
                self.events,
                self.vectors,
                limit=self.memory.top_k,
                vector_k=self.memory.vector_top_k,
                rrf_k=self.memory.rrf_k,
                important_limit=self.memory.important_limit,
                important_threshold=self.memory.important_threshold,
                recent_limit=self.memory.recent_limit,
            )
            self.recall_log.append(
                {
                    "turn": turn,
                    "char_id": actor_id,
                    "query": query[:80],
                    "debug": result.debug,
                    "items": [
                        {"source": i.source, "score": round(i.score, 4), "text": i.text[:60]}
                        for i in result.items
                    ],
                }
            )
            self.recall_log = self.recall_log[-50:]
            return result.texts(limit=5)

        if tool == "look":
            focus = str(args.get("focus") or "四周")
            parts = []
            if state.scene.description:
                parts.append(state.scene.description)
            if state.scene.present_npcs:
                parts.append("在场：" + "、".join(state.scene.present_npcs))
            if state.scene.exits:
                parts.append("出口：" + "、".join(e.to for e in state.scene.exits))
            return f"{focus} —— " + "；".join(parts) if parts else f"{focus}：没有更多细节"

        if tool == "check_self":
            ext = card.ext
            inv = "、".join(e.name_flavor or e.template_id for e in ext.equipment) or "空手"
            return (
                f"HP {ext.hp}/{ext.hp_max}，理智 {ext.sanity}/{ext.sanity_max}，"
                f"装备：{inv}，职业：{ext.profession}"
            )

        return f"没有这个工具：{tool}"

    def _to_action(self, actor_id: str, data: dict) -> PlayerAction:
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

    def run_agent_turn(
        self,
        actor_id: str,
        card: CharacterCard,
        state: GameState,
        turn: int,
        previous: list[str] | None = None,
    ) -> PlayerAction:
        """一个 Agent 的完整回合：思考 → 调 tool → 再思考 → 提交行动。

        短期记忆（近 3 回合）直接在上下文里；长期记忆只能通过 recall 主动取。
        正常情况第一步就 final，不需要任何工具。
        """
        spec = self.config.resolve(
            card_model=card.ext.model,
            provider=self._override_provider,
            model=self._override_model,
            reasoning_effort=self._override_effort,
        )
        system = prompts.pc_system(card, self.skills)
        scratch: list[str] = []
        recalled: list[str] = []

        for step in range(self.memory.max_steps):
            user = prompts.pc_user(
                state,
                card,
                self._recent_public,
                turn,
                recalled or None,
                self._summaries,
                previous,
                self.memory.recent_turns,
                )
            if scratch:
                user += "\n\n# 你已经查到的\n" + "\n".join(f"- {x}" for x in scratch)

            try:
                data = self._chat_json(system, user, spec, "player")
            except Exception as exc:  # noqa: BLE001
                self.usage["fail"] += 1
                self._last_error = f"agent_turn[{spec.provider}/{spec.model}]: {exc}"
                return self._fallback_action(actor_id)

            kind = str(data.get("type") or "").strip().lower()
            if kind == "tool":
                tool = str(data.get("tool") or "")
                args = data.get("args") or {}
                result = self._tool_call(actor_id, card, state, tool, args, turn)
                if tool == "recall":
                    recalled = result if isinstance(result, list) else []
                    scratch.append(f"recall({args.get('query', '')}) → 回忆起 {len(recalled)} 条")
                else:
                    scratch.append(f"{tool} → {result}")
                continue

            return self._to_action(actor_id, data)

        # 步数用尽：用最后一次判断兜底
        return self._fallback_action(actor_id)

    def _fallback_action(self, actor_id: str) -> PlayerAction:
        return PlayerAction(
            actor_id=actor_id,
            thought="（判断失误，先拖时间）",
            action="保持戒备，扫视四周的出口",
            approach="贴着墙，不抢先动",
            skill="观察",
            intent=Intent.careful,
        )

    def player_action(
        self,
        actor_id: str,
        card: CharacterCard,
        state: GameState,
        turn: int,
        previous: list[str] | None = None,
    ) -> PlayerAction:
        """兼容入口（loop / mock 协议同名）。"""
        return self.run_agent_turn(actor_id, card, state, turn, previous)

    # ── NPC（独立 Agent）────────────────────────────────────
    def npc_action(self, npc: dict, state: GameState, turn: int) -> dict:
        """让一个 NPC 按自己的目标行动。它们是独立 Agent，不是 GM 的台词道具。"""
        base = {"id": str(npc.get("id") or ""), "name": str(npc.get("name") or "")}
        try:
            data = self._chat_json(
                prompts.npc_system(npc, self.world_name),
                prompts.npc_user(state, npc, self._recent_public, turn),
                self.default_spec,
                "player",
            )
            return {
                **base,
                "action": str(data.get("action") or "").strip(),
                "speech": str(data.get("speech") or "").strip(),
            }
        except Exception as exc:  # noqa: BLE001
            self.usage["fail"] += 1
            self._last_error = f"npc_action[{base['id']}]: {exc}"
            return {**base, "action": "", "speech": ""}

    # ── 编年史 / 上下文压缩 ─────────────────────────────────
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

    def maybe_summarize(self, turn: int, window: int | None = None) -> None:
        """每 `window` 回合把积累的原文压成一条回顾，控制上下文长度。"""
        window = int(window or self.memory.summary_every)
        if window <= 0 or turn % window != 0 or not self._recent_public:
            return

        chunk = "\n".join(self._recent_public[-window * 4 :])
        try:
            data = self._chat_json(
                "你把游戏记录压缩成分章回顾，只输出 JSON。",
                prompts.chronicler_user(turn, chunk),
                self.default_spec,
                "summary",
            )
            texts = [
                str(s.get("text") or "").strip()
                for s in (data.get("summaries") or [])
                if isinstance(s, dict)
            ]
            joined = " ".join(t for t in texts if t)
            if joined:
                self._summaries.append(f"第{turn - window + 1}~{turn}回合：{joined}")
                self._summaries = self._summaries[-self.memory.chapter_keep :]
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"summarize: {exc}"
