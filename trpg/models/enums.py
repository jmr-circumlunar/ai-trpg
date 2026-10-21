"""集中定义的枚举——不得散落。

命名纪律：`tier` 只指装备强度档；骰子结果档叫 `outcome`。
"""

from __future__ import annotations

from enum import Enum


class Quality(str, Enum):
    rough = "rough"
    common = "common"
    fine = "fine"
    rare = "rare"
    legendary = "legendary"

    @property
    def mult(self) -> float:
        return {
            Quality.rough: 0.6,
            Quality.common: 1.0,
            Quality.fine: 1.3,
            Quality.rare: 1.7,
            Quality.legendary: 2.5,
        }[self]


class Outcome(str, Enum):
    crit_success = "crit_success"
    success = "success"
    fail_forward = "fail_forward"
    failure = "failure"


class Intent(str, Enum):
    careful = "careful"
    normal = "normal"
    all_in = "all_in"


class Controller(str, Enum):
    llm = "llm"
    human = "human"
    ghost = "ghost"


class Position(str, Enum):
    scene = "scene"
    away = "away"
    dead = "dead"


class AgentRole(str, Enum):
    gm = "gm"
    player = "player"
    arbiter = "arbiter"
    chronicler = "chronicler"
    renderer = "renderer"


class MemoryScope(str, Enum):
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"


class LedgerKind(str, Enum):
    status = "status"
    relation = "relation"
    event = "event"
    death = "death"


class DeltaOp(str, Enum):
    set = "set"
    add = "add"
    sub = "sub"


class SourceKind(str, Enum):
    initial = "initial"
    generated = "generated"
    loot = "loot"
