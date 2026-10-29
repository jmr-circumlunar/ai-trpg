"""工具可见性只有一份判据。

修复前三份实现：

| # | 位置 | 问题 |
|---|---|---|
| 1 | `manifest.tools` | 只声明 `requires`，`say` 的 comms 规则写在注释里 |
| 2 | `assemble.tool_set()` | **漏了 comms 门** → 没有交流的世界仍返回 `say` |
| 3 | `pc.visible_tools` / `tools.visible_tools` | 各自手写一遍 `if name == "say"` |

第 2 处是**真漏洞**：拿 `tool_set` 做权限校验会漏 `say`。现在把 comms 规则声明到
`ToolSpec.requires_comms`，`tool_set` 统一判定，两个 `visible_tools` 只负责排序。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.agents.prompts.pc import _comms_mode, visible_tools  # noqa: E402
from trpg.rulespack import assemble, tool_set  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402

WORLDS = ("werewolf", "ironvale")


@pytest.mark.parametrize("world", WORLDS)
def test_tool_set_and_prompt_agree(world):
    """`tool_set()` 与 `visible_tools()` 必须给出同一份集合——这是修复前不一致的地方。"""
    pack = load_world(world, ROOT)
    caps = assemble(pack)
    mode = _comms_mode(pack)

    assert tool_set(caps, mode) == set(visible_tools(pack))


@pytest.mark.parametrize("world", WORLDS)
def test_say_follows_comms_mode(world):
    """`say` 只在世界开了交流时可见。"""
    pack = load_world(world, ROOT)
    caps = assemble(pack)
    mode = _comms_mode(pack)

    if mode == "none":
        assert "say" not in tool_set(caps, mode)
        assert "say" not in visible_tools(pack)
    else:
        assert "say" in tool_set(caps, mode)


def test_tool_set_defaults_to_no_comms():
    """不传 `comms_mode` 时按「没有交流」处理（保守），不会白送 `say`。"""
    pack = load_world("ironvale", ROOT)
    caps = assemble(pack)

    assert "say" not in tool_set(caps)


def test_prompt_order_is_stable():
    """prompt 里的工具顺序必须稳定，否则每次生成的前缀不同、缓存全废。"""
    pack = load_world("werewolf", ROOT)

    first = visible_tools(pack)
    second = visible_tools(pack)

    assert first == second
