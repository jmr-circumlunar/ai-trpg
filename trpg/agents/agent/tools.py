"""M12 Agent · 工具：`_TOOLS` 注册表与 `_tool_*` 实现。

新增一个工具 = ① 加 `_tool_<name>` 方法 ② 在 `_TOOLS` 登记 ③ `AGENT_PROTOCOL` 加一条。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import random
from typing import Any

from ...models import CharacterCard, GameState, PlayerAction, StateDelta
from ...models.enums import CombatMode, Intent, Position
from ...models.state import can_act
from ...memory.recall import hybrid_recall
from ...memory.settings import MemorySettings
from .. import prompts
from ..spec import LLMConfig, ModelSpec
from ..providers import extract_json

from .base import *  # noqa: F401,F403


class ToolMixin:
    # 工具注册表：新增工具 = 加一个 `_tool_<name>` 方法 + 在 `_TOOLS` 登记 + prompt 加一条
    # `say` 的可见性额外受世界包 `comms.mode` 控制（见 visible_tools）
    _TOOLS = ("recall", "look", "check_self", "say")

    def _comms_config(self) -> dict:
        """世界包的交流配置（`turn_flow.yaml: comms`）。没有 pack 时返回空 = 无交流。"""
        pack = getattr(self, "pack", None)
        if pack is None:
            return {}
        try:
            declaration = pack.turn_flow_declaration() or {}
        except Exception:  # noqa: BLE001
            return {}
        comms = declaration.get("comms") or {}
        return dict(comms) if isinstance(comms, dict) else {}

    def comms_mode(self) -> str:
        """`none` / `one_way` / `limited`；缺省 `none`（= 世界没有开交流能力）。"""
        return str(self._comms_config().get("mode") or "none")

    def visible_tools(self) -> tuple[str, ...]:
        """本局可见的工具（C51）。

        **判据只有一份**：`rulespack.assemble.tool_set()`。这里只负责按 `_TOOLS`
        的顺序排列——与 `prompts/pc.py::visible_tools` 用的是同一个函数，
        以前各写一遍，`tool_set` 自己反而漏了 comms 门。
        """
        try:
            from ...rulespack import assemble as build_caps
            from ...rulespack import tool_set
        except Exception:  # noqa: BLE001
            return tuple(self._TOOLS)

        mode = self.comms_mode()
        caps = None
        if getattr(self, "pack", None) is not None:
            try:
                caps = build_caps(self.pack)
            except Exception:  # noqa: BLE001
                caps = None

        if caps is None:
            # 没有 pack（离线 / 单测）：只保留 comms 这一条门
            return tuple(
                name for name in self._TOOLS if not (name == "say" and mode == "none")
            )

        allowed = tool_set(caps, mode)
        return tuple(name for name in self._TOOLS if name in allowed)

    def _tool_call(
        self, actor_id: str, card: CharacterCard, state: GameState, tool: str,
        args: dict, turn: int,
    ):
        if tool not in self.visible_tools():
            return f"没有这个工具：{tool}"
        handler = getattr(self, f"_tool_{tool}", None)
        if handler is None:
            return f"没有这个工具：{tool}"
        return handler(actor_id, card, state, args, turn)

    def _tool_say(self, actor_id, card, state, args, turn):
        """发言（C53）：出局禁言、配额由代码计数、超限拒绝且说明原因。

        发言只写进 `self.spoken`，由编排层在角色行动后收进 briefs——
        「谁能看到谁说的话」交给阶段可见性统一管，工具自己不负责传播。
        """
        rt = state.characters.get(actor_id)
        # 用统一的 `is_alive`：濒死（0 血但未正式出局）同样不许发言。
        # 「能行动」而不是「还活着」：濒死仍算在场（`is_alive` 为真），但这一回合不开口。
        # 用 `can_act` 才能让「清零不死」这类技能（`conditions` 带豁免标记）照常发言。
        if rt is not None and not can_act(rt):
            return "你已经出局，不能再发言。"

        config = self._comms_config()
        if str(config.get("mode") or "none") == "none":
            return "这个世界没有开启交流能力。"

        text = str(args.get("speech") or args.get("text") or "").strip()
        if not text:
            return "发言内容为空。"

        quota = str(config.get("quota") or "per_actor_per_turn")
        limit = max(1, int(config.get("max_per_turn") or 1))
        if quota == "per_actor_per_turn":
            used = dict(state.comms_used or {})
            if used.get(actor_id, 0) >= limit:
                return f"本回合你已经说过 {limit} 次了，不能再发言。"
            used[actor_id] = used.get(actor_id, 0) + 1
            state.comms_used = used

        spoken = getattr(self, "spoken", None)
        if spoken is None:
            spoken = []
            self.spoken = spoken
        spoken.append({"actor": actor_id, "name": card.name, "text": text[:200]})
        return f"你说了：{text[:80]}"

    def _tool_recall(self, actor_id, card, state, args, turn):
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
            min_score=self.memory.vector_min_score,
            rel_margin=self.memory.vector_rel_margin,
            scoring_mode=self.memory.scoring_mode,
            score_weights={
                "relevance": self.memory.weight_relevance,
                "importance": self.memory.weight_importance,
                "recency": self.memory.weight_recency,
            },
            half_life_turns=self.memory.half_life_turns,
            importance_scale=self.memory.importance_scale,
            structured_relevance=self.memory.structured_relevance,
            conflict_enabled=bool(self.memory.conflict_enabled),
            conflict_min_diff_chars=self.memory.conflict_min_diff_chars,
            conflict_min_shared_prefix=self.memory.conflict_min_shared_prefix,
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

    def _tool_look(self, actor_id, card, state, args, turn):
        focus = str(args.get("focus") or "四周")
        parts = []
        if state.scene.description:
            parts.append(state.scene.description)
        if state.scene.present_npcs:
            parts.append("在场：" + "、".join(state.scene.present_npcs))
        if state.scene.exits:
            parts.append("出口：" + "、".join(e.to for e in state.scene.exits))
        return f"{focus} —— " + "；".join(parts) if parts else f"{focus}：没有更多细节"

    def _tool_check_self(self, actor_id, card, state, args, turn):
        # 三期：列出即算一次「使用」（物品晋升判据）
        self._bump_usage(actor_id, card, state)
        ext = card.ext
        inv = "、".join(e.name_flavor or e.template_id for e in ext.equipment) or "空手"
        ident = prompts.identity_line(card, state.characters.get(actor_id), self.pack)
        talents = f"，天赋：{'、'.join(ext.talents)}" if ext.talents else ""
        return (
            f"HP {ext.hp}/{ext.hp_max}，理智 {ext.sanity}/{ext.sanity_max}，"
            f"装备：{inv}{ident}，职业：{ext.profession}{talents}"
        )

    def _bump_usage(self, actor_id: str, card: CharacterCard, state: GameState) -> None:
        """把「本次列出的物品」记一次使用（写 item_usage）。"""
        conn = getattr(getattr(self, "events", None), "conn", None)
        if conn is None:
            return
        # world_id 允许为空（没有 pack 的离线调用也要记），只有 session_id 是必须的
        world_id = str(getattr(self.pack, "world_id", "") or "")
        session_id = str(getattr(self.events, "session_id", "") or "")
        if not session_id:
            return
        names: list[str] = []
        for item in card.ext.equipment or []:
            name = str(item.name_flavor or item.template_id or "").strip()
            if name:
                names.append(name)
        runtime = state.characters.get(actor_id)
        for raw in (getattr(runtime, "inventory", None) or []):
            text = str(raw)
            if not text.startswith("eq_") and text.strip():
                names.append(text.strip())
        for name in dict.fromkeys(names):
            try:
                from ...storage import db

                db.bump_item_usage(conn, world_id, session_id, name, [], 0)
            except Exception:  # noqa: BLE001
                pass

    def _to_action(self, actor_id: str, data: dict) -> PlayerAction:
        """解析 LLM 输出。人类提交走同一个 `PlayerAction.from_raw`，两条路只此一份逻辑。"""
        return PlayerAction.from_raw(actor_id, data)


__all__ = ["ToolMixin"]
