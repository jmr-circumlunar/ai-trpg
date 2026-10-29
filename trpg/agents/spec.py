"""供应商配置与模型解析。

解析优先级：角色卡 model -> 全局 defaults -> 旧 .env 变量（兼容）

doc: 03-模块/M12-Agent与交互/00-模块总述.md
module: M12
layer: L5
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..config import config_path as _config_path
from ..config import load_yaml
from ..models import ModelConfig
from .env import read_env_file
from .providers import KIND_MAP, ProviderAdapter


@dataclass
class ModelSpec:
    provider: str
    model: str
    temperature: float = 0.9
    max_tokens: int = 0  # 0 = 用调用处的任务默认值
    reasoning_effort: str = ""  # "" = 不覆盖，用 provider 默认


class LLMConfig:
    def __init__(self, root: Path | str, config_path: Path | None = None) -> None:
        self.root = Path(root)
        self.file_env = read_env_file(self.root / ".env")
        path = config_path or _config_path(self.root, "providers")
        self.raw = load_yaml(path)
        self.defaults = self.raw.get("defaults") or {}
        self.providers = self.raw.get("providers") or {}
        self._adapters: dict[str, ProviderAdapter] = {}
        self.net_retry = 0
        # 运行时思考档位（对局页顶栏切换）：叠加在 `tiers.pc` 之上
        self._effort_override: dict[str, Any] = {}
        self._effort_steps = 0

    # ── 环境变量 ────────────────────────────────────────────
    def api_key(self, name: str) -> str:
        cfg = self.providers.get(name) or {}
        env_name = cfg.get("api_key_env") or ""
        if env_name:
            return os.environ.get(env_name) or self.file_env.get(env_name, "")
        return cfg.get("api_key") or ""

    def _on_retry(self, exc) -> None:
        self.net_retry += 1

    # ── adapter ────────────────────────────────────────────
    def adapter(self, provider: str) -> ProviderAdapter:
        if provider in self._adapters:
            return self._adapters[provider]

        cfg = self.providers.get(provider)
        if not cfg:
            raise KeyError(
                f"未配置的供应商 '{provider}'，请在 config/providers.yaml 里添加"
            )

        kind = cfg.get("kind", "openai")
        cls = KIND_MAP.get(kind)
        if cls is None:
            raise KeyError(f"未知的 kind '{kind}'（可选：{', '.join(KIND_MAP)}）")

        adapter = cls(
            base_url=cfg["base_url"],
            endpoint=cfg.get("endpoint", "/chat/completions"),
            api_key=self.api_key(provider),
            timeout=float(cfg.get("timeout", 90)),
            on_retry=self._on_retry,
            extra=cfg.get("extra") or {},
        )
        self._adapters[provider] = adapter
        return adapter

        # 运行时思考档位（对局页顶栏切换）
        self._effort_override: dict[str, Any] = {}
        self._effort_steps = 0

    # ── 模型解析 ────────────────────────────────────────────
    def resolve(
        self,
        card_model: ModelConfig | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
    ) -> ModelSpec:
        provider = provider or (card_model.provider if card_model and card_model.provider else "")
        provider = provider or self.defaults.get("provider", "minimax")

        model = model or (card_model.model if card_model and card_model.model else "")
        model = model or self.defaults.get("model", "")

        temp = temperature
        if temp is None and card_model is not None:
            temp = card_model.temperature
        if temp is None:
            temp = float(self.defaults.get("temperature", 0.9))

        # 思考深度：CLI > 角色卡 > 全局默认 > provider 自带
        effort = reasoning_effort or ""
        if not effort and card_model is not None:
            effort = card_model.reasoning_effort or ""
        if not effort:
            effort = str(self.defaults.get("reasoning_effort", "") or "")

        max_tokens = card_model.max_tokens if card_model else 0
        return ModelSpec(
            provider=provider,
            model=model,
            temperature=temp,
            max_tokens=max_tokens,
            reasoning_effort=effort,
        )

    def spec_for(self, role: str, base: ModelSpec) -> ModelSpec:
        """按「分档」覆盖一个基础 spec（`config/providers.yaml: tiers`）。

        缺配置、缺字段一律回落 base——分档是优化，不是前置条件。
        """
        tier = self.tier(role)
        if not tier:
            return base
        temp = tier.get("temperature")
        return ModelSpec(
            provider=str(tier.get("provider") or base.provider),
            model=str(tier.get("model") or base.model),
            temperature=float(temp) if temp is not None else base.temperature,
            max_tokens=int(tier.get("max_tokens") or base.max_tokens),
            reasoning_effort=str(tier.get("reasoning_effort") or base.reasoning_effort),
        )

    def tier(self, role: str) -> dict:
        """取某个分档的原始配置（空 dict = 未配置，调用方应回落）。

        玩家 Agent（`pc`）会叠加**运行时思考档位**（`set_effort`）——
        对局页顶栏切档位后立刻生效，不用重启服务。
        """
        tier = dict((self.raw.get("tiers") or {}).get(role) or {})
        if role == "pc" and self._effort_override:
            tier.update(self._effort_override)
        return tier

    def effort(self, name: str) -> dict:
        """取一个「思考档位」的配置（`config/providers.yaml: efforts`）。"""
        return dict((self.raw.get("efforts") or {}).get(str(name or "")) or {})

    def effort_names(self) -> list[str]:
        """可选的思考档位（给前端下拉用）。"""
        return list((self.raw.get("efforts") or {}).keys())

    def set_effort(self, name: str) -> dict:
        """切换玩家 Agent 的思考档位（对局页顶栏）。

        影响三件事：**思考深度**（reasoning_effort）、**输出预算**（max_tokens）、
        **推理步数**（steps —— ReAct 最多来回想几轮）。
        """
        effort = self.effort(name)
        self._effort_override = {
            key: value
            for key, value in effort.items()
            if key in ("reasoning_effort", "max_tokens")
        }
        self._effort_steps = int(effort.get("steps") or 0)
        return effort

    def effort_steps(self) -> int:
        """当前档位允许的推理步数（0 = 用记忆层的默认）。"""
        return int(self._effort_steps or 0)

    def describe(self) -> str:
        return f"{self.defaults.get('provider')}/{self.defaults.get('model')}"
