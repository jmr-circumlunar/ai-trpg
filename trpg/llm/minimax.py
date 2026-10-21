"""MiniMax 接入（OpenAI 兼容的 chatcompletion_v2）。

端点：{base_url}/text/chatcompletion_v2
默认：https://api.minimaxi.com/v1 + MiniMax-M3

设计要点：
- 结构化输出：要求模型只吐 JSON，代码侧做 extract + pydantic 校验
- 失败降级链：重试 1 次 -> 该 Agent 降级（PC 观望 / GM 模板句），不阻塞整局
- 所有调用计入 usage，供成本统计
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from ..models import CharacterCard, GameState, PlayerAction, StateDelta
from ..models.enums import Intent
from . import prompts
from .http import HttpError, post_json

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.+?)```", re.S)

# 可重试的网络/服务端错误（529 = MiniMax 集群过载）
_NET_RETRY_STATUS = {0, 429, 500, 502, 503, 504, 529}


def extract_json(text: str) -> dict:
    raw = (text or "").strip()
    block = _JSON_BLOCK.search(raw)
    if block:
        raw = block.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start : end + 1])
    raise ValueError(f"响应中没有 JSON：{raw[:200]}")


class MiniMaxClient:
    def __init__(
        self,
        api_key: str,
        cards: dict[str, CharacterCard],
        base_url: str = "https://api.minimaxi.com/v1",
        model: str = "MiniMax-M3",
        model_player: str | None = None,
        temperature: float = 0.9,
        timeout: float = 90.0,
        skills: list[str] | None = None,
        taboos: list[str] | None = None,
        style: str = "冷峻、克制，少形容词",
        world_name: str = "铁谷",
    ) -> None:
        if not api_key:
            raise ValueError("缺少 MiniMax API Key（设置 MINIMAX_API_KEY 或写入 .env）")
        self.api_key = api_key
        self.cards = cards
        self.base_url = base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/text/chatcompletion_v2"
        self.model = model
        self.model_player = model_player or model
        self.temperature = temperature
        self.timeout = timeout
        self.skills = skills or [
            "潜行", "观察", "交涉", "威吓", "攀爬", "医疗",
            "追踪", "学识", "生存", "器械", "撬锁", "意志",
        ]
        self.taboos = taboos or [
            "不涉及现实政治与真实人物",
            "不做任何性内容",
            "不写针对未成年人的暴力细节",
        ]
        self.style = style
        self.world_name = world_name

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

    # ── 底层 ────────────────────────────────────────────────
    def _post(self, payload: dict) -> dict:
        """带指数退避的网络请求：429 / 5xx / 529（集群过载）/ 连接失败。"""
        delay = 1.0
        attempts = 3
        for attempt in range(attempts):
            try:
                return post_json(
                    self.endpoint,
                    payload,
                    {"Authorization": f"Bearer {self.api_key}"},
                    timeout=self.timeout,
                )
            except HttpError as exc:
                retryable = exc.status in _NET_RETRY_STATUS
                if retryable and attempt < attempts - 1:
                    self.usage["net_retry"] += 1
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise

        raise RuntimeError("unreachable")

    def _chat(self, messages: list[dict], model: str, max_tokens: int = 1000) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        data = self._post(payload)

        base_resp = data.get("base_resp") or {}
        code = base_resp.get("status_code", 0)
        if code not in (0, None):
            raise RuntimeError(f"MiniMax 返回错误 {code}: {base_resp.get('status_msg')}")

        choices = data.get("choices") or []
        content = ""
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        if not content:
            content = data.get("reply") or ""
        if not content:
            raise RuntimeError("MiniMax 返回空内容")

        usage = data.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["in"] += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        self.usage["out"] += int(usage.get("completion_tokens") or 0)
        return content

    def _chat_json(
        self,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 1000,
    ) -> dict:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            return extract_json(self._chat(messages, model, max_tokens))
        except Exception as exc:  # 重试 1 次，把错误原因回灌
            self.usage["retry"] += 1
            retry_user = (
                f"{user}\n\n# 上一次输出不合要求\n{exc}\n请严格只输出合法 JSON，不要任何额外文字。"
            )
            return extract_json(
                self._chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": retry_user}],
                    model,
                    max_tokens,
                )
            )

    # ── GM ──────────────────────────────────────────────────
    def describe_scene(self, state: GameState, turn: int) -> dict:
        try:
            data = self._chat_json(
                prompts.gm_system(self.world_name, state.world.era, self.style, self.taboos),
                prompts.gm_scene_user(state, self.cards, turn),
                self.model,
                max_tokens=700,
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

    def narrate(self, state: GameState, action: PlayerAction, result, turn: int) -> dict:
        dice_line = (
            f"{result.expr} 骰面 {result.rolls} → 合计 {result.total} vs DC {result.dc}"
            f"（差 {result.margin:+d}）"
        )
        card = self.cards.get(action.actor_id)
        label = card.name if card else action.actor_id
        try:
            data = self._chat_json(
                prompts.gm_system(self.world_name, state.world.era, self.style, self.taboos),
                prompts.gm_narrate_user(
                    state, self.cards, action.action, result.outcome, dice_line, turn, label
                ),
                self.model,
                max_tokens=900,
            )
            narration = str(data.get("narration") or "").strip()
            if not narration:
                raise ValueError("narration 为空")

            delta_payload = data.get("state_delta")
            delta = None
            if isinstance(delta_payload, dict):
                try:
                    delta_payload.setdefault("turn", turn)
                    delta = StateDelta.model_validate(delta_payload)
                except Exception:
                    delta = None

            self._recent_public.append(narration)
            return {
                "narration": narration,
                "narration_meta": data.get("narration_meta") or {},
                "state_delta": delta,
            }
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"narrate: {exc}"
            return {
                "narration": f"{action.actor_id} 的尝试有了结果（{result.outcome}），场面一时僵住。",
                "narration_meta": {},
                "state_delta": None,
            }

    # ── PC ──────────────────────────────────────────────────
    def player_action(
        self, actor_id: str, card: CharacterCard, state: GameState, turn: int
    ) -> PlayerAction:
        try:
            data = self._chat_json(
                prompts.pc_system(card, self.skills),
                prompts.pc_user(state, card, self._recent_public, turn),
                self.model_player,
                max_tokens=900,
            )
            return PlayerAction(
                actor_id=actor_id,
                thought=str(data.get("thought") or ""),
                action=str(data.get("action") or "").strip() or "按兵不动",
                approach=str(data.get("approach") or ""),
                skill=data.get("skill") or None,
                target=data.get("target") or None,
                intent=Intent(str(data.get("intent") or "normal")),
            )
        except Exception as exc:
            self.usage["fail"] += 1
            self._last_error = f"player_action: {exc}"
            return PlayerAction(
                actor_id=actor_id,
                thought="（判断失误，先拖时间）",
                action="保持戒备，扫视四周的出口",
                approach="贴着墙，不抢先动",
                skill="观察",
                intent=Intent.careful,
            )

    # ── 编年史（S0 只记录，不入库） ──────────────────────────
    def summarize(self, state: GameState, turn: int) -> dict:
        text = self._recent_public[-1] if self._recent_public else ""
        if not text:
            return {"summaries": [], "new_entities": []}
        try:
            return self._chat_json(
                "你把游戏记录压缩成事件摘要，只输出 JSON。",
                prompts.chronicler_user(turn, text),
                self.model,
                max_tokens=400,
            )
        except Exception:
            self.usage["fail"] += 1
            return {"summaries": [], "new_entities": []}

    def sleep_hint(self) -> None:
        """给外部留一个节流口（MiniMax 有 QPS 限制）。"""
        time.sleep(0.2)
