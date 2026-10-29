"""doc: 03-模块/M02-规则与判定/00-模块总述.md
module: M02
layer: L1
"""
from .dice import DiceEngine, DiceError
from .loader import Rules

__all__ = ["DiceEngine", "DiceError", "Rules"]
