"""M07 地图与空间：区域图生成、连通与邻接查询。

这是本模块**唯一的对外接口**——engine / api / game 只许从这里 import，
不要直接依赖 `map.worldgen` 的内部实现（实现可换，函数签名不变）。

doc: 03-模块/M07-地图与空间/00-模块总述.md
module: M07
layer: L3
"""

from .worldgen import (
    exits_for,
    generate_region_map,
    is_adjacent,
    name_minor_buildings,
    node_label,
)

__all__ = [
    "exits_for",
    "generate_region_map",
    "is_adjacent",
    "name_minor_buildings",
    "node_label",
]
