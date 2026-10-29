"""M18 规则包与能力包：能力清单（manifest）与运行期装配（assemble）。

设计要点：
- **框架只提供通用能力**：阶段种类、谓词、工具、可写字段、前端区块都是与具体世界无关的枚举；
- **世界包只选参数**：`systems` / `turn_flow` / `dice` / `memory` / `prompts` 五类声明；
- **清单是唯一真相**：`manifest.py` 定义，`tools/describe_capabilities.py` 导出给世界包作者。

层级：L2（与 M05 世界包同层，读它但不被它依赖，见 `02-约束/01` §一 白名单）。

doc: 03-模块/M18-规则包与能力包/00-模块总述.md
module: M18
layer: L2
"""

from __future__ import annotations

from . import manifest
from .assemble import CapabilityError, assemble, tool_set
from .systems import CapabilitySet

# 注意：`assemble` 这个名字被**函数**占用，所以 `trpg.rulespack.assemble` 这个**子模块**
# 在包外访问不到（`from trpg.rulespack.assemble import x` 会拿到函数而报错）。
# 需要模块里的东西，一律在这里补导出——本包只导 three 个，够用且不会踩坑。
__all__ = ["CapabilityError", "CapabilitySet", "assemble", "manifest", "tool_set"]
