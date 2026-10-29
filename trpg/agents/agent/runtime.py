"""M12 Agent · 运行时：场景描述、批量叙事、回合推进、摘要与收尾。

只做「把状态翻译成 prompt、把模型输出翻译成结构化动作」，判定一律不在这里。

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import random
from typing import Any

from ...models import CharacterCard, GameState, PlayerAction, StateDelta
from ...models.enums import CombatMode, Intent
from ...memory.consolidation import (
    consolidate,
    render_consolidations,
    should_consolidate,
)
from ...memory.lorebook import match_lorebook, parse_entries, render_lorebook
from ...memory.recall import hybrid_recall
from ...memory.settings import MemorySettings
from .. import prompts
from ..schemas import AgentTurnOut, SkillChoiceOut
from ..spec import LLMConfig, ModelSpec
from ..providers import extract_json

from .base import *  # noqa: F401,F403
from .tools import ToolMixin


class RuntimeMixin:
    def _gm_system(self, state: GameState) -> str:
        text = prompts.gm_system(
            self.world_name,
            state.world.era,
            self.style,
            self.taboos,
            self.world_core,
            self.world_orgs,
            # B47：世界包可通过 prompts/gm.md 追加自己的叙述纪律
            pack=self.pack,
        )
        # 二期：跨局传承——上一局留下的传闻（不参与判定，只影响叙事）
        if self.legacy:
            text += f"\n\n# 上一局留下的传闻（真实发生过，可作为背景引用）\n{self.legacy}\n"
        return text

    def _phase_label(self, state: GameState) -> str:
        if self.rules is None:
            return ""
        try:
            return self.rules.phase_label(self.rules.phase_of(state.clock.hour))
        except Exception:  # noqa: BLE001
            return ""

    def _step_options(self) -> list[dict]:
        return list(getattr(self.rules, "allowed_steps", []) or [])

    def _consolidation_texts(self, actor_id: str, turn: int) -> list[str]:
        """反思层（S5-3）：回合到点时把最近记忆巩固成高阶结论。

        缺省关闭（`consolidation.every_n=0` 且 `threshold=0`）→ 返回空，行为零变化。
        """
        every_n = int(getattr(self.memory, "consolidation_every_n", 0) or 0)
        threshold = int(getattr(self.memory, "consolidation_threshold", 0) or 0)
        if (every_n <= 0 and threshold <= 0) or self.events is None:
            return []
        window = self.events.all_for(actor_id)[-max(every_n, 1):]
        importance_sum = sum(int(getattr(e, "importance", 0) or 0) for e in window)
        if not should_consolidate(
            turn,
            every_n=every_n,
            importance_sum=importance_sum,
            threshold=threshold,
        ):
            return []
        items = consolidate(
            window,
            actor_id,
            turn,
            min_cluster=int(getattr(self.memory, "consolidation_min_cluster", 2) or 2),
        )
        return render_consolidations(items)

    def _lorebook_texts(self) -> list[str]:
        """Lorebook 词条触发（S5-3）：关键词命中才注入。

        世界包没有词条（`lorebook_entries` 为空）→ 返回空，行为零变化。
        """
        raw = getattr(self.pack, "lorebook_entries", None) or []
        entries = parse_entries(list(raw))
        if not entries:
            return []
        depth = int(getattr(self.memory, "lorebook_scan_depth", 2) or 2)
        texts = list(getattr(self, "_recent_public", []) or [])[-depth:] if depth > 0 else []
        hits = match_lorebook(
            entries,
            texts,
            sticky_state=getattr(self, "_lorebook_sticky", None),
            max_entries=int(getattr(self.memory, "lorebook_max_entries", 3) or 3),
            default_scan_depth=depth,
        )
        return render_lorebook(hits)

    def _recall(self, actor_id: str, turn: int = 0) -> list[str]:
        """回忆：结构化（重要/最近）+ 向量（情境联想）→ RRF 融合。

        只查 `actor_id` 自己的记录——隔离靠物理过滤，不靠 prompt 叮嘱。
        每次调用都留一条日志，便于判断「没召回到」还是「召回到了但没用」。
        """
        if self.events is None and self.vectors is None:
            return []

        query = self._recent_public[-1] if self._recent_public else ""
        # 参数必须真的传下去：以前没传，改 config/memory.yaml 对自动回忆完全无效
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
        texts = result.texts(limit=6)
        # S5-3：反思层结论 + 词条触发（缺省都关闭 → 行为零变化）
        texts.extend(self._consolidation_texts(actor_id, turn))
        texts.extend(self._lorebook_texts())
        return texts

    # ── GM ──────────────────────────────────────────────────
    def _available_npcs(self, state: GameState) -> list[dict]:
        """当前时间点可遇的 NPC（毕业/未入学的自动排除）。"""
        if self.pack is not None:
            try:
                return self.pack.npcs_for(state.world.timeline)
            except Exception:  # noqa: BLE001
                pass
        return list(self.npcs)

    def _validate_npcs(self, raw, state: GameState | None = None) -> list[dict]:
        """GM 申报的 NPC 必须在当前时间点可遇，最多 2 个。"""
        pool = self._available_npcs(state) if state is not None else list(self.npcs)
        if not isinstance(raw, list) or not pool:
            return []
        by_id = {str(n.get("id")): n for n in pool if n.get("id")}
        out, seen = [], set()
        for item in raw:
            nid = str(item)
            if nid in by_id and nid not in seen:
                seen.add(nid)
                out.append(by_id[nid])
        return out[:2]

    def _pick_seed(self, state: GameState) -> dict | None:
        """按 era / 幕次 / **空间** 筛可用种子，按权重抽一个。

        三条 P5 修正：
        1. **换幕清空已用集合**：原来 `_used_seeds` 永不清空，longzu 幕次 1 只有 6 条合格
           种子 → 第 6 回合后一条骨架都不再注入，全靠 GM 自由发挥（就是"剧情失速"）。
        2. **空间约束**：种子声明了 `regions` / `locations` 才过滤，
           「宿舍楼后巷的实验体」就不会在教室抽中。
        3. **种子分层**：`tier=ambient`（氛围）不进已用集合（可复用，但不连续重复），
           `main` / `side` 仍一次性。
        """
        if self._seed_stage != state.stage:
            self._seed_stage = state.stage
            self._used_seeds.clear()

        pool = []
        for seed in self.seeds:
            sid = str(seed.get("id") or "")
            if not sid:
                continue
            tier = str(seed.get("tier") or "main")
            if sid in self._used_seeds and tier != "ambient":
                continue
            if tier == "ambient" and sid == self._last_seed_id:
                continue  # 氛围种子可复用，但不要连着两次同一条
            eras = seed.get("era") or []
            if eras and state.world.era not in eras:
                continue
            lo, hi = (seed.get("stage_range") or [1, 4])[:2]
            if not (lo <= state.stage <= hi):
                continue
            regions = seed.get("regions") or []
            if regions and state.scene.region not in regions:
                continue
            locations = seed.get("locations") or []
            if locations and state.scene.location_id not in locations:
                continue
            pool.append(seed)

        if not pool:
            return None
        weights = [float(s.get("weight_base", 1.0) or 1.0) for s in pool]
        chosen = self.seed_rng.choices(pool, weights=weights)[0]
        sid = str(chosen.get("id"))
        if str(chosen.get("tier") or "main") != "ambient":
            self._used_seeds.add(sid)
        self._last_seed_id = sid
        self.seed_log.append(
            {"id": sid, "type": chosen.get("type"), "tier": chosen.get("tier") or "main"}
        )
        return chosen

    def force_seed(self, seed: dict) -> None:
        """Director 的固定事件：下一回合优先使用，且不再参与随机抽取。"""
        self.forced_seed = dict(seed or {})
        sid = str(seed.get("id") or "")
        if sid:
            self._used_seeds.add(sid)

    def describe_scene(self, state: GameState, turn: int) -> dict:
        seed_hint = self.forced_seed or self._pick_seed(state)
        self.forced_seed = None
        try:
            data = self._chat_json(
                self._gm_system(state),
                prompts.gm_scene_user(
                    state,
                    self.cards,
                    turn,
                    self._phase_label(state),
                    self._available_npcs(state),
                    seed_hint,
                    pack=self.pack,
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
                # options 是新协议；hooks 是旧字段，兼容保留
                "options": data.get("options") or data.get("hooks") or [],
                "present_npcs": self._validate_npcs(data.get("present_npcs"), state),
                # 场景里申报的威胁：必须在玩家决策之前折算成敌人
                "enemies": data.get("enemies") or [],
            }
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"describe_scene: {exc}"
            fallback = f"（第 {turn} 回合）四周安静得不太对劲，像是在等谁先动。"
            self._recent_public.append(fallback)
            return {"narration": fallback, "hooks": [], "present_npcs": [], "enemies": []}

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
                    pack=self.pack,
                    loot_hint=prompts.loot_hint_block(
                        self.pack, getattr(self.rules, "equipment", None)
                    ),
                    condition_vocab=getattr(self.rules, "condition_vocab", None),
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
                # 战利品由代码掷档（B22：模型只给名字/slot/tag，价格与成色归 rules/equipment.yaml）
                "loot": data.get("loot") or [],
            }
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"narrate_batch: {exc}"
            fallback = {
                it["actor_id"]: f"{it['label']} 的尝试有了结果（{it['outcome']}），场面一时僵住。"
                for it in items
            }
            # 兜底路径的键集必须与成功路径一致，调用方不写防御式 .get
            return {
                "narrations": fallback,
                "narration_meta": {},
                "state_delta": None,
                "npc_moves": npc_moves or [],
                "loot": [],
            }

    # ── PC（按角色卡解析 provider/model）────────────────────
    # ── 玩家 Agent：ReAct（思考 → 调 tool / 提交行动）─────────
    MAX_STEPS = 3

    def run_agent_turn(
        self,
        actor_id: str,
        card: CharacterCard,
        state: GameState,
        turn: int,
        previous: list[str] | None = None,
        knowledge: str = "",
    ) -> PlayerAction:
        """一个 Agent 的完整回合：思考 → 调 tool → 再思考 → 提交行动。

        短期记忆（近 3 回合）直接在上下文里；长期记忆只能通过 recall 主动取。
        正常情况第一步就 final，不需要任何工具。
        """
        # B23：PC 也走分档（tiers.pc）；卡上显式指定的 provider/model 仍然优先
        spec = self._pc_spec(card)
        # `experience` 是**这个 actor 自己**跨局积累的判断（程序记忆）。
        # 取不到就是空 → prompt 与接线前逐字相同，存量世界不受影响。
        system = prompts.pc_system(
            card,
            self.skills,
            pack=self.pack,
            experience=self.experience.get(actor_id),
        )
        from ..reasoner import AgentState, build_reason_graph, run_reason

        def think(gs: AgentState) -> dict:
            """一次思考：调模型，决定「调工具」还是「给出行动」。"""
            user = prompts.pc_user(
                state,
                card,
                self._recent_public,
                turn,
                gs.recalled or None,
                self._summaries,
                previous,
                self.memory.recent_turns,
                pack=self.pack,
                knowledge=knowledge,
            )
            if gs.scratch:
                user += "\n\n# 你已经查到的\n" + "\n".join(f"- {x}" for x in gs.scratch)
            try:
                # M2：给 schema 就用结构化输出（字段名/类型由 schema 保证）；
                # mock 与自研 adapter 没有 structured()，自动落回自由文本 + extract_json。
                data = self._chat_json(system, user, spec, "player", schema=AgentTurnOut)
            except Exception as exc:  # noqa: BLE001
                self.usage["fail"] += 1
                self._last_error = f"agent_turn[{spec.provider}/{spec.model}]: {exc}"
                return {"done": True, "error": "model_error", "output": {}}

            kind = str(data.get("type") or "").strip().lower()
            trace = list(gs.trace)
            trace.append({"step": gs.steps + 1, "kind": "think", "type": kind})
            if kind == "tool":
                return {"output": data, "steps": gs.steps + 1, "trace": trace}
            return {"output": data, "steps": gs.steps + 1, "done": True, "trace": trace}

        def act(gs: AgentState) -> dict:
            """执行工具，把观察结果放进 scratch（下一轮思考能看到）。"""
            data = gs.output or {}
            tool = str(data.get("tool") or "")
            args = data.get("args") or {}
            result = self._tool_call(actor_id, card, state, tool, args, turn)
            scratch = list(gs.scratch)
            recalled = list(gs.recalled)
            # 按返回类型分派，不按工具名分派：新增工具不用改这里。
            # 返回 list = 记忆类工具，结果要作为「你临时想起的事」注入后续上下文。
            if isinstance(result, list):
                recalled = list(result)
                args_hint = args.get("query", "") if isinstance(args, dict) else ""
                scratch.append(f"{tool}({args_hint}) → 回忆起 {len(result)} 条")
            else:
                scratch.append(f"{tool} → {result}")
            trace = list(gs.trace)
            trace.append(
                {"step": gs.steps, "kind": "act", "tool": tool, "observation": str(result)[:120]}
            )
            return {"scratch": scratch, "recalled": recalled, "trace": trace}

        # 推理循环用 **LangGraph 图**表达（方案 38 / C70）：思考 → 工具 → 观察 → 再思考。
        # 模型调用、工具集合、schema、步数上限、兜底行为与旧的 for 循环**逐字一致**，
        # 换来的���可观测 trace 与可插 checkpointer。
        graph = build_reason_graph(think, act, max_steps=self._max_steps())
        final = run_reason(graph, AgentState())
        self.last_trace = list(getattr(final, "trace", []) or [])

        if getattr(final, "error", ""):
            # 步数用尽或模型调用失败：用最后一次判断兜底（与旧循环一致）
            return self._fallback_action(actor_id, card)
        data = getattr(final, "output", {}) or {}
        if not data:
            return self._fallback_action(actor_id, card)
        return self._to_action(actor_id, data)

        # 步数用尽：用最后一次判断兜底
        return self._fallback_action(actor_id, card)

    def _max_steps(self) -> int:
        """ReAct 最多允许的推理轮数。

        思考档位（对局页顶栏）可以调高——「让角色多想一会儿」就是调这里；
        没切档位时用记忆层的默认值。
        """
        try:
            steps = int(self.config.effort_steps() or 0)
        except Exception:  # noqa: BLE001
            steps = 0
        return steps or int(self.memory.max_steps or 3)

    def _fallback_action(self, actor_id: str, card=None) -> PlayerAction:
        """兜底行动。技能取**卡上真实存在的**技能，没有就 None（通用检定）。

        硬编码某个技能名会在换世界/改技能表时静默失效。
        """
        card_skills = list((card.ext.skills or {}).keys()) if card is not None else []
        return PlayerAction(
            actor_id=actor_id,
            thought="（判断失误，先拖时间）",
            action="保持戒备，扫视四周的出口",
            approach="贴着墙，不抢先动",
            skill=card_skills[0] if card_skills else None,
            intent=Intent.careful,
        )

    def player_action(
        self,
        actor_id: str,
        card: CharacterCard,
        state: GameState,
        turn: int,
        previous: list[str] | None = None,
        knowledge: str = "",
    ) -> PlayerAction:
        """兼容入口（loop / mock 协议同名）。"""
        return self.run_agent_turn(actor_id, card, state, turn, previous, knowledge)

    # ── 结算 ────────────────────────────────────────────────
    def epilogue(
        self,
        state: GameState,
        cards: dict[str, CharacterCard],
        kind: str = "ended",
        turn: int = 0,
    ) -> str:
        """结局文本。失败回落规则版——收尾不能卡住。"""
        try:
            data = self._chat_json(
                self._gm_system(state),
                prompts.epilogue_user(state, cards, kind, turn),
                self.default_spec,
                "narrate",
            )
            text = str(data.get("epilogue") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            self.usage["fail"] += 1
        from ...engine.ending import epilogue_text

        return epilogue_text(state, cards, kind)

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

    def vote(self, actor_id: str, card, state, turn: int, candidates=None) -> str | None:
        """投票（集体决策 / 组内共议）：从候选里选一个。**兼容旧调用方**（只给目标）。"""
        detail = self.vote_detail(actor_id, card, state, turn, candidates)
        return detail.get("target") if detail else None

    def vote_detail(
        self, actor_id: str, card, state, turn: int, candidates=None
    ) -> dict:
        """投票 + **理由**（S2-4）：返回 `{"target": ..., "reason": ...}`。

        **为什么不能复用 `player_action`**：行动回答「我要做什么」，投票回答「我选谁」。
        混在一起时模型只写行动、不填 `target`，于是全票弃权——
        真实模型跑 6 回合 8 人局时就是这么翻的（`counts = {"__abstain__": 8}`），
        而 mock 有自己的 `vote()`，测试一直是绿的。

        理由是**可选**的：模型没写就空着（旧行为逐字节不变）。
        """
        options = [str(item) for item in (candidates or []) if str(item).strip()]
        if not options:
            return {}

        system = (
            f"你在扮演 {card.data.name}。现在是投票环节。\n"
            f"候选人：{'、'.join(options)}\n\n"
            "只输出 JSON，不要解释：\n"
            '{"target": "候选人 id", "reason": "一句话理由"}\n\n'
            "必须从候选人里选一个（填他的 id，不要填名字）。"
            "只有在你确实没有任何偏好时才填 null。"
        )
        user = f"第 {turn} 回合，你投谁？"

        try:
            data = self._chat_json(system, user, self._pc_spec(card), "vote")
        except Exception:  # noqa: BLE001  投票失败按弃权处理，不把回合搞崩
            self.usage["fail"] += 1
            return {}

        target = str((data or {}).get("target") or "").strip()
        reason = str((data or {}).get("reason") or "").strip()[:80]
        return {
            "target": target if target in options else None,
            "reason": reason,
        }

    def last_words(
        self, actor_id: str, card, state, turn: int, cause: str = "", knowledge: str = ""
    ) -> str:
        """**遗言**：出局那一刻说的一句公开话。

        标准规则里首夜死者与被投出者都有遗言——它是信息战的一部分：
        临死前可以指认凶手、可以交代身份，**也可以撒谎**。
        没有遗言，出局就只是「少了一个人」，博弈少一整轮。

        返回空串表示没留下话，编排层不会往回执里塞空条目。
        """
        system = (
            f"你在扮演 {card.data.name}，你刚刚出局。\n"
            '只输出 JSON：{"say": "你的遗言（不超过 50 字）"}\n'
            "可以说出你的判断、怀疑或身份，也可以撒谎。不要旁白、不要解释。"
        )
        # 背景放 system 开头：遗言要求「只输出 JSON」，指令必须留在最后
        system = knowledge + "\n\n" + system if knowledge else system
        user = f"第 {turn} 回合，你出局了（原因：{cause or '未知'}）。你说什么？"

        try:
            data = self._chat_json(system, user, self._pc_spec(card), "eulogy")
        except Exception:  # noqa: BLE001
            self.usage["fail"] += 1
            return ""

        for key in ("say", "line", "text", "speech"):
            value = str((data or {}).get(key) or "").strip()
            if value:
                return value[:160]
        return ""

    def council_say(
        self, actor_id: str, card, state, turn: int, transcript=None, knowledge: str = ""
    ) -> str:
        """组内商议时的一句发言（**只有同组看得见**）。

        与 `say` 工具的区别：这是编排层驱动的**受控发言**——轮次与真实时间由代码限制
        （`groups[].rounds` / `budget_s`），模型只负责「这一轮说什么」，
        不自己决定什么时候开口、说几轮。这样「交流有约束」不依赖模型自觉。

        返回空串表示这一轮选择不说，编排层会跳过它、不影响其他人。
        """
        # **必须写成 JSON**：这条走 `_chat_json`，让模型输出纯文本会解析失败、
        # 触发重试（实测一次 4 人局重试 6 次、降级 2 次）。MiniMax 对
        # `response_format` 是弱约束，prompt 里这行不能省。
        system = (
            f"你在扮演 {card.data.name}。你和几个自己人正在私下交换意见。\n"
            '只输出 JSON：{"say": "你要说的一句话（不超过 40 字）"}\n'
            "不要旁白、不要解释、不要写自己的名字。"
        )
        lines = [str(item) for item in (transcript or [])][-8:]
        # 背景放 system 开头，JSON 指令留在最后（同 skill_choice 的理由）
        system = knowledge + "\n\n" + system if knowledge else system
        user = f"第 {turn} 回合，私下商议中。\n"
        if lines:
            user += "刚才说过：\n" + "\n".join(lines) + "\n\n"
        user += "你说什么？"

        try:
            data = self._chat_json(system, user, self._pc_spec(card), "council")
        except Exception:  # noqa: BLE001
            self.usage["fail"] += 1
            return ""

        # 模型可能用不同键名回同一句话，挨个试——比要求它严格按 schema 更省事
        for key in ("say", "line", "speech", "text", "action"):
            value = str((data or {}).get(key) or "").strip()
            if value:
                return value[:120]
        return ""

    def _display_name(self, cid: str) -> str:
        """角色 id → 展示名；拿不到就退回 id（不猜、不编号）。"""
        card = (getattr(self, "cards", {}) or {}).get(cid)
        name = str(getattr(getattr(card, "data", None), "name", "") or "")
        return name or str(cid)

    @staticmethod
    def _alive(runtime) -> bool:
        """生死只认 `is_alive`（濒死仍算在场）——单源见 02-约束/04。"""
        from ...models.state import is_alive

        return bool(is_alive(runtime))

    def skill_choice(self, actor_id, card, state, turn, skills, candidates=None, knowledge: str = ""):
        """主动技能选择（A2）：返回 `{"skill": id, "target": cid}`；不发动返回 `None`。

        与 `council_say` 同一类**受控发言**：能选哪些技能、能选谁，全由编排层给定
        （`skills` + `candidates`），模型只负责「用哪个、对谁」。
        返回非法值时一律当作「不发动」——不猜、不兜底代打，避免读出规则外的效果。
        """
        options = {str(key): list(val or []) for key, val in dict(candidates or {}).items()}
        usable = [item for item in (skills or []) if options.get(str(item.get("id")))]
        if not usable:
            return None

        lines = []
        for item in usable:
            skill_id = str(item.get("id"))
            label = str(item.get("label") or skill_id)
            targets = ", ".join(self._display_name(cid) for cid in (options.get(skill_id) or []))
            extra = []
            if item.get("hint"):
                extra.append(str(item["hint"]))
            cost = item.get("resource") or {}
            if cost.get("key"):
                extra.append(f"消耗「{cost.get('key')}」")
            tail = ("；" + "；".join(extra)) if extra else ""
            lines.append(f"- {skill_id}（{label}）：{targets}{tail}")

        # 局势：以前这里只写「你可以用这些技能，选个目标」，模型只能在真空里瞎猜——
        # 首夜盲毒、白天乱自爆就是这么来的。现在给它身份、在场与已出局名单。
        runtime = (getattr(state, "characters", {}) or {}).get(actor_id)
        camp = str(getattr(runtime, "camp", "") or "")
        stance = f"你的身份：{card.ext.profession or '未知'}" + (f"（阵营：{camp}）" if camp else "")
        alive = [
            self._display_name(cid)
            for cid, rt in (getattr(state, "characters", {}) or {}).items()
            if self._alive(rt)
        ]
        dead = [
            self._display_name(cid)
            for cid, rt in (getattr(state, "characters", {}) or {}).items()
            if not self._alive(rt)
        ]

        system = (
            "你在玩一局信息不对称的回合制对局，现在轮到你决定是否使用能力。\n"
            f"{stance}\n"
            f"存活：{'、'.join(alive) or '无'}；已出局：{'、'.join(dead) or '无'}\n\n"
            "可用能力：\n" + "\n".join(lines) + "\n\n"
            "判断准则（重要）：\n"
            "1) 没有信息时**不要**做不可逆的伤害动作（例如毒杀、自爆）。宁可不用。\n"
            "2) 每个能力都有代价（消耗次数、暴露自己、自己出局）。先想清楚换到了什么。\n"
            "3) 想不出明确收益，「不发动」是**常见且正确**的选择。\n"
            '只输出 JSON：{"skill": "技能 id", "target": "角色 id"}；'
            '不发动输出 {"skill": "不发动", "target": ""}。不要解释。'
        )
        # 知识视图放在 **system 开头**（背景信息），JSON 指令留在最后——
        # 之前把长段背景接在 user 末尾，会稀释「只输出 JSON」这条指令，
        # 模型改成说人话 → 解析失败 → 技能**静默不发动**。
        system = knowledge + "\n\n" + system if knowledge else system
        user = f"第 {turn} 回合，你选哪个？"

        try:
            data = self._chat_json(
                system, user, self._pc_spec(card), "skill", schema=SkillChoiceOut
            )
        except Exception as exc:  # noqa: BLE001
            self.usage["fail"] += 1
            self._last_error = f"skill_choice[{self._pc_spec(card).provider}]: {exc}"
            return None

        skill_id = str((data or {}).get("skill") or "").strip()
        target = str((data or {}).get("target") or "").strip()
        if skill_id not in options:
            return None

        allowed = list(options.get(skill_id) or [])
        if target not in allowed:
            # 提示里给的是**显示名**，模型常回名字；这里把名字换回 id。
            # 以前不做这一步 → 名字被当成非法目标**静默丢弃** → 技能不发动。
            by_name = {self._display_name(cid): cid for cid in allowed}
            target = by_name.get(target, "")
        if not target:
            return None
        return {"skill": skill_id, "target": target}

    def reflect(
        self,
        ending: str,
        events: list[str],
        existing: list[str],
        limit: int = 3,
    ) -> list[dict]:
        """局末自反思（Reflexion，Shinn et al. 2023）：写下这一局学到的东西。

        **经验是 agent 自己写的**，所以会带上它的性格与立场偏差——这是设计意图，
        不是缺陷。不客观没关系，但必须与本局的实际结果对得上，否则下局会害到自己。

        与「分章回顾」的区别：回顾是「发生了什么」（叙事摘要），经验是「我学到了什么」
        （可复用的判断）。前者每 N 回合跑一次、留在本局；后者局末跑一次、跟着 agent 走。
        """
        system = (
            "你在回顾刚结束的这一局，写下**你学到的东西**——不是复述发生了什么。\n\n"
            "要求：\n"
            f"- 最多 {limit} 条，每条一句话，第一人称。\n"
            "- 只写**你自己**的判断（该信谁、什么时候该闭嘴、什么话不能说），"
            "不要写规则、数值或世界设定。\n"
            "- 用你的性格和立场去写，不必客观；但**必须和你这一局的实际结果对得上**。\n"
            "- 拿不准就别写：这些经验会被带进下一局，写错了会害到你自己。\n"
            '- 只输出 JSON：{"experiences": [{"kind": "lesson|habit|read", "text": "…"}]}'
        )
        event_text = "\n".join(f"- {item}" for item in events[:30]) or "（没有记录）"
        existing_text = "\n".join(f"- {item}" for item in existing[:12]) or "（还没有）"
        user = (
            f"# 这一局的结局\n{ending or '（没有结局文本）'}\n\n"
            f"# 你这一局的经历\n{event_text}\n\n"
            f"# 你已经有的经验（不要重复它们）\n{existing_text}"
        )

        try:
            data = self._chat_json(system, user, self.default_spec, "reflect")
        except Exception:  # noqa: BLE001
            # 提炼失败不算错：经验是锦上添花，不能因为一次调用失败把结局流程搞崩
            self.usage["fail"] += 1
            return []

        out: list[dict] = []
        for item in (data.get("experiences") or [])[:limit]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                out.append({"kind": str(item.get("kind") or "lesson"), "text": text})
        return out

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
                chapter = f"第{turn - window + 1}~{turn}回合：{joined}"
                self._summaries.append(chapter)
                self._summaries = self._summaries[-self.memory.chapter_keep :]
                # B25：同步落库；失败只记账，不打断回合（读档少了回顾也能玩）
                sink = getattr(self, "_summary_sink", None)
                if callable(sink):
                    try:
                        sink(chapter, int(turn))
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = f"summary_sink: {exc}"
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"summarize: {exc}"


__all__ = ["RuntimeMixin"]
