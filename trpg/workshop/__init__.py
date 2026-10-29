"""M17 世界包工坊：**文本 → 中间表示 → YAML 骨架**（S7-2）。

三段形态（D-7）：
1. **抽取**：规则文本 / 小说 → 结构化中间表示（IR）；
2. **生成**：IR → 世界包文件（骨架）；
3. **校验—修补闭环**：生成物必须过校验闸（`tools/workshop.py` 调用），
   **产物只进待确认区**，人工确认后才合并（绝不运行时写 `worlds/`）。

本包（L1 纯变换层）只做前两段：**不依赖任何模块**（只用标准库 + yaml），
所以它可以被工具层、API 层随意调用。校验与合并留给工具层（`tools/workshop.py`）。

**不引入 DSL**（D-5）：IR 是**结构化数据**，不是一门语言。

doc: 03-模块/M17-世界包工坊/00-模块总述.md
module: M17
layer: L1
"""

from __future__ import annotations

from .ir import WorldIR, parse_ir
from .scaffold import scaffold_world

__all__ = ["WorldIR", "parse_ir", "scaffold_world"]
