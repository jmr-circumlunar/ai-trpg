"""供应商配置与模型解析。

解析优先级：角色卡 model -> 全局 defaults -> 旧 .env 变量（兼容）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..models import ModelConfig
from .env import read_env_file
from .providers import KIND_MAP, ProviderAdapter


@dataclass
class ModelSpec:
    provider: str
    model: str
    temperature: float = 0.9
    max_tokens: int = 0  # 0 = 用调用处的任务默认值


class LLMConfig:
    def __init__(self, root: Path | str, config_path: Path | None = None) -> None:
        self.root = Path(root)
        self.file_env = read_env_file(self.root / ".env")
        path = config_path or (self.root / "config" / "providers.yaml")
        self.raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.defaults = self.raw.get("defaults") or {}
        self.providers = self.raw.get("providers") or {}
        self._adapters: dict[str, ProviderAdapter] = {}
        self.net_retry = 0

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

    # ── 模型解析 ────────────────────────────────────────────
    def resolve(
        self,
        card_model: ModelConfig | None = None,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
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

        max_tokens = card_model.max_tokens if card_model else 0
        return ModelSpec(provider=provider, model=model, temperature=temp, max_tokens=max_tokens)

    def describe(self) -> str:
        return f"{self.defaults.get('provider')}/{self.defaults.get('model')}"
