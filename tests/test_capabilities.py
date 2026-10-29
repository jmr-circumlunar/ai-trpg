"""B39 装配器骨架：缺省全开、显式关闭、依赖闭合、explain 可追源。

这一层必须做到「不声明就等于现状」：`ironvale` / `longzu` 没有任何声明文件，
行为必须与今天逐字节相同，因此缺省路径是本文件最重的部分。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.rulespack import CapabilitySet, assemble  # noqa: E402
from trpg.rulespack.assemble import CapabilityError  # noqa: E402
from trpg.rulespack.manifest import systems as system_registry  # noqa: E402
from trpg.world.pack import load_world  # noqa: E402


@pytest.fixture(scope="module")
def pack():
    return load_world("ironvale", ROOT)


def test_absent_declaration_enables_every_active_system():
    caps = assemble(None)

    assert isinstance(caps, CapabilitySet)
    for sid, spec in system_registry.items():
        if spec.status == "active":
            assert caps.has(sid), f"{sid} 是已实现系统，缺省必须开启"


def test_absent_declaration_keeps_planned_system_off():
    caps = assemble(None)

    for sid, spec in system_registry.items():
        if spec.status == "planned":
            assert not caps.has(sid), f"{sid} 已预留未实现，缺省不应开启"


def test_explicit_disable_only_affects_that_system():
    caps = assemble({"combat": {"enabled": False}})

    assert not caps.has("combat")
    assert caps.has("equipment"), "关闭 combat 不应连带关闭 equipment"


def test_requires_closed_raises():
    with pytest.raises(CapabilityError) as err:
        assemble(
            {
                "combat": {"enabled": True, "requires": ["equipment"]},
                "equipment": {"enabled": False},
            }
        )

    assert "equipment" in str(err.value)


def test_required_system_cannot_be_disabled():
    with pytest.raises(CapabilityError) as err:
        assemble({"time": {"enabled": False}})

    assert "time" in str(err.value)


def test_unknown_system_raises():
    with pytest.raises(CapabilityError) as err:
        assemble({"flying": {"enabled": True}})

    assert "flying" in str(err.value)


def test_explain_reports_default_source(pack):
    assert "缺省" in assemble(pack).explain("combat")


def test_explain_reports_explicit_source():
    caps = assemble({"combat": {"enabled": False}})

    assert "显式" in caps.explain("combat")


def test_assemble_caches_result_on_pack(pack):
    """同一局只装配一次：装配结果挂在 pack 上，不重复读文件。"""
    assert assemble(pack) is assemble(pack)


def test_capabilities_are_immutable(pack):
    with pytest.raises(AttributeError):
        assemble(pack).enabled.add("weather")  # type: ignore[attr-defined]


def test_world_pack_only_exposes_declaration_not_assembly(pack):
    """world 层只提供声明数据；装配在 M18，避免 world → rulespack 向上依赖。"""
    declaration = pack.systems_declaration()

    assert isinstance(declaration, dict)
    assert declaration == {}, "ironvale 没有任何 systems.yaml，声明必须为空（= 缺省全开）"
