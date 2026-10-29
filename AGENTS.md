# AGENTS.md · 本仓库的硬约束

> 给所有 AI 会话与协作者。优先级高于任何对话里的口头约定；与本文件冲突的实现一律改回来。

## 1. 设计与实现必须强一致

**设计基准在知识库，不在本仓库。** 知识库路径：`07-AI项目/AI跑团/`（Obsidian vault `AI员工知识库`）。

- 本仓库的 `HANDOFF.md` 只是**交接快照与命令手册**，不承载设计细节；细节以知识库为准。
- 知识库结构（2026-10-22 重构后）：`00-总述`（唯一入口）→ `02-约束/`（分层/可变性/契约点 C1–C45）→ `03-模块/M01–M15/`（每个模块「00-模块总述 + 能力文档」）→ `过程记录/`（历史与方案）。
- **模块 = 知识库文件夹 = 代码包**：`M11` ↔ `03-模块/M11-记忆/` ↔ `trpg/memory/`，机器可读声明在 `trpg/modules.yaml`。
- 过程产出（决策、踩坑、偏差、方案底稿）只进知识库 `过程记录/`，**不进设计文档，也不进本仓库**。

## 2. 变更顺序固定（不许倒置）

```
① 在知识库 过程记录/方案/ 写方案（问题 / 可选方案 / 结论 / 影响文件）
② 定稿后改设计文档（只写当前结果）
③ 再改代码与数据
④ 跑 pytest + check_contract，确认契约点同步
```

先改代码后补文档 = 流程违规，须在 `过程记录/01-决策日志` 登记偏差。

## 2.5 架构约束（改代码前先看）

知识库 `02-约束/01-分层与依赖.md` 是硬约束，要点：

- 依赖方向单向：八层 `L0 models/config/net → L1 rules/clock → L2 storage/world/memory → L3 equipment/combat/growth/map → L4 engine → L5 agents → L6 api → L7 ops`，不得反向；同层需白名单（`trpg/modules.yaml: whitelist`）。
- 三类可变性：内容 → `worlds/`；数值 → `rules/*.yaml`；机制 → 走八类变更的固定位置。
- **代码里不许藏世界**：人物、职业模板、地点、专有名词、默认世界名一律不进 `trpg/`。
- **世界形态分支只许两处**：`world/pack.py` 的能力查询、`characters.py` 的建卡策略。
- 新增 Agent 工具 = 加 `_tool_<name>` 方法 + `_TOOLS` 登记 + prompt 一条，不改分派逻辑。
- 新增状态字段 = 五点契约（模型 → 白名单 → prompt → 文档 → 字段集测试）。
- `trpg/meta/` 是**横切层**（只依赖 models + storage）：局外成长（点数/价目表/带入）都在这。
- 写 `card.ext` 只有两个授权入口：`trpg/rules/identity.py`（身份 grants）、`trpg/meta/apply.py`（局外成长）。
- **运行时不得写 `worlds/`**：物品晋升只生成待确认补丁 `data/promotions/*.yaml`，人工跑 `tools/apply_promotions.py --apply` 才合并。

## 3. 契约点：必须一致的最小集合

不是所有文字都要和代码绑死，但下列内容**必须**文档与实现一致（45 条清单见知识库 `02-约束/03-契约点索引`）：

| 契约点 | 文档 | 实现 |
|---|---|---|
| 字段与语义（C1–C4） | `03-模块/M01-数据模型/01-字段契约` | `trpg/models/*.py` |
| `StateDelta` 白名单（C3） | `03-模块/M13-回合编排/02-状态合并白名单` | `trpg/engine/state_merge.py` |
| 枚举取值（C4） | `03-模块/M01-数据模型/01-字段契约` §四 | `trpg/models/enums.py` |
| 数值与阈值（C5–C9/C19–C23） | `02-约束/03-契约点索引` | `rules/*.yaml` |
| 世界包结构（C10–C16） | `03-模块/M05-世界包/01-世界包规格` | `worlds/<id>/*.yaml` |

**双向锚点**：每个 `.py` 头部写 `doc:`（指向知识库模块总述），每篇能力文档 frontmatter 写 `code:`（指向代码）。两者由脚本比对，悬空即报错。

自查命令（四条门禁，缺一不可）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools\check_contract.py
.\.venv\Scripts\python.exe tools\check_world.py
.\.venv\Scripts\python.exe tools\check_docs.py --strict
```

## 4. 单一真相源

| 东西 | 唯一真相 | 文档的角色 |
|---|---|---|
| 数值 / 阈值 | `rules/*.yaml` | 解释为什么这么定 |
| 数据结构 | `trpg/models/*.py` | 解释字段为什么存在 |
| 流程 | `trpg/engine/loop.py` | 解释为什么是这个顺序 |
| 世界内容 | `worlds/<world_id>/` | 清点交付量 |

文档里出现数值时，必须是 `rules/*.yaml` 的**副本**，不得另立一套。

## 5. 判定归代码，叙事归模型

- LLM 不得改判、不得自造数值；骰子自实现、seed 可复现。
- 装备、道具、事件、时间在世界包里只是**种子**：GM 说「是什么」，代码算「多强」。
- 物品分三层 `prop → instance → template`，按 Agent 使用频率晋升，阈值在 `rules/equipment.yaml: promotion`。

## 6. 提交前

1. `python -m pytest -q` 全绿
2. `python tools/check_contract.py` 无 diff
3. `python tools/check_docs.py --strict` 无违规（doc 头 / code 字段 / 依赖层）
4. 对照本次改动的契约点，确认对应设计文档已同步
5. 新增/移动文件时，`.py` 头部 `doc:` 与文档 `code:` 同步更新
6. 世界包改动只提交 `ironvale`（`longzu` 在 `.gitignore`）
