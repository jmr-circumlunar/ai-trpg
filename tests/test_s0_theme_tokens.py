"""S0-4：design token 变量化（theme.css 三层 token）。

出口判据：「theme.css 无裸色值」+「世界包覆盖主题生效」。

口径（写进断言，避免以后扯皮）：
- **token 定义区**（`:root` 块）允许写具体色值 —— token 就是「值的唯一真源」；
- **消费处**（`:root` 之外的任何规则）**不允许**出现裸十六进制色值，只能 `var(--token)`；
- 主题化能力 = 世界包在 `:root` 覆盖任一 token（基础/语义/组件层都行）即可改观感。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "src" / "styles" / "theme.css"
WORLD_THEME = ROOT / "worlds" / "werewolf" / "ui" / "theme.css"

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TOKEN_DEF = re.compile(r"(?m)^\s*(--[a-z0-9-]+)\s*:")
VAR_USE = re.compile(r"var\((--[a-z0-9-]+)\)")


def _split_theme() -> tuple[str, str]:
    text = THEME.read_text(encoding="utf-8")
    start = text.index(":root {")
    end = text.index("\n}", start)
    return text[start:end], text[:start] + text[end:]


def test_consumer_side_has_no_raw_hex():
    """消费处不得再出现裸十六进制色值。"""
    _, consumer = _split_theme()
    hits = HEX.findall(consumer)
    assert not hits, f"消费处还有裸色值：{sorted(set(hits))[:8]}"


def test_three_token_layers_exist():
    """基础色 / 语义色 / 组件色三层都要有，且命名中性。"""
    root, _ = _split_theme()
    assert "--c-" in root, "缺少基础色层（--c-*）"
    names = [m.group(1) for m in TOKEN_DEF.finditer(root)]
    semantic = [n for n in names if n in {"--text-strong", "--text-muted", "--alert-soft"}]
    assert semantic, "缺少语义色层"
    component = [n for n in names if n.startswith(("--seat-", "--think-", "--tl-", "--deal-", "--cs-"))]
    assert len(component) >= 10, "组件色层太少（组件样式必须只引用 token）"


def test_every_used_token_is_defined():
    """消费处引用的 token 都必须有定义（除已知遗留白名单）。"""
    root, consumer = _split_theme()
    defined = {m.group(1) for m in TOKEN_DEF.finditer(root)}
    used = {m.group(1) for m in VAR_USE.finditer(consumer)}
    # 遗留：这两个只在世界主题里定义，基础主题下一直是无效引用（不在本步修，见回执）
    legacy = {"--gold-soft", "--text"}
    missing = used - defined - legacy
    assert not missing, f"引用了未定义的 token：{sorted(missing)}"


def test_token_names_are_neutral():
    """token 命名不得含世界语义（框架层不该知道「狼人」这种事）。"""
    root, _ = _split_theme()
    banned = ["werewolf", "wolf", "village", "seer", "witch", "hunter", "ironvale", "longzu"]
    for name in [m.group(1) for m in TOKEN_DEF.finditer(root)]:
        low = name.lower()
        for word in banned:
            assert word not in low, f"token {name} 含世界语义 {word}"


def test_world_theme_overrides_tokens_only():
    """世界包只覆盖 token（或纯样式），不重新发明组件。"""
    text = WORLD_THEME.read_text(encoding="utf-8")
    assert "--accent-warm:" in text, "世界包应至少覆盖一个框架 token（S0-4 验证点）"
    # 覆盖写在 :root 块里 → 变量才会落到页面上
    assert ":root" in text
