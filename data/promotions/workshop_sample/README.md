# 空白模板世界包 `worlds/_blank`

**这是建新世界的唯一模板。** 复制整个目录、改 YAML，就能得到一个能跑的世界——**0 行代码**。

```powershell
# 1) 复制模板
Copy-Item -Recurse worlds\_blank worlds\<你的世界 id>

# 2) 改 world.yaml 的 world_id / name / regions，填内容

# 3) 自检 + 跑起来
.\.venv\Scripts\python.exe tools\check_world.py <你的世界 id>
.\.venv\Scripts\python.exe run.py run --world <你的世界 id> --provider mock --turns 3
```

> 目录名以下划线开头（`_blank`）不会出现在「选世界」列表里，这是故意的：
> 模板本身不应该被当成一个可玩世界。跑它必须显式 `--world _blank`。
> 它同时是 CI 用例（`tests/test_blank_world.py`）：模板一旦和代码脱节，测试先红。

---

## 一、文件清单

| 文件 | 必填 | 缺省会怎样 | 说明 |
|---|---|---|---|
| `world.yaml` | ✅ | 开不了局 | 基调、组织、默认 era/region、日历 |
| `items.yaml` | ✅ | 开不了局 | 装备模板 + 报到初始装备 |
| `seeds.yaml` | ✅ | 开不了局（C32） | 事件种子，至少 1 条 |
| `npcs.yaml` | ✅ | 开不了局 | NPC 档案（出场时机只能写 `npcs_timeline.yaml`，C16） |
| `professions.yaml` | ✅ | 建卡失败 | 职业/身份牌 + `initial_pool` |
| `eras/<id>.yaml` | ✅ | 装备 tag 无来源 | 技术水平、`allowed_tags`（⊆ tag_vocab，C7）、`tier_max` 1–5 |
| `regions/<id>.md` | ✅ | 区域没有地点（C15） | 地点表，第一列必须是 `loc_*` |
| `systems.yaml` | ⬜ | **已实现系统全开** | 能力开关（见下方预设 B） |
| `turn_flow.yaml` | ⬜ | 用框架标准流程 | 阶段序列（见下方预设 C） |
| `dice.yaml` | ⬜ | 用 `rules/` 默认 d20 | 骰型、DC 基线、四档区间 |
| `memory.yaml` | ⬜ | 用 `config/memory.yaml` | 短期窗口 / 摘要周期 / 长期记忆 |
| `prompts/*.md` | ⬜ | 用代码内置骨架 | GM/NPC/PC 骨架内容（B47 后生效） |
| `origins.yaml` / `paths.yaml` | ⬜ | 用单层职业 | 三层职业世界才需要 |
| `meta_hooks.yaml` | ⬜ | 无局外成长 | 价目表；`target` 必须在本世界存在（C31） |
| `core/` / `archive/` | ⬜ | 无世界观块 | core 常驻 prompt；archive 按需检索 |
| `npcs_timeline.yaml` | ⬜ | NPC 全部常驻 | NPC 出场窗口 |

---

## 二、三种「换玩法」的预设

### 预设 A（默认）：全开

删掉 `systems.yaml` 就是全开。等价于 `systems.yaml` 里每个已实现系统都写 `enabled: true`。

### 预设 B：极简叙事（不掷骰、不战斗）

```yaml
systems:
  time:        { enabled: true, required: true }
  identity:    { enabled: true }
  death:       { enabled: true }
  judge:       { enabled: false }   # 不判定
  combat:      { enabled: false }
  equipment:   { enabled: false }
  map:         { enabled: false }
  long_memory: { enabled: false }
  meta_growth: { enabled: false }
  events:      { enabled: false }
  immersion:   { enabled: false }
  human_seat:  { enabled: false }
```

一个开关会在**四处**同时生效：阶段不执行、工具不可见、对应状态字段不可写、前端区块不渲染。
只关一处的话，玩家会看到「能启动但页面像坏了」。

### 预设 C：昼夜两套流程

同一个 `kind: action`，换参数就是两套流程——**框架里没有「夜晚」这个概念**：

```yaml
phases:
  - id: night              # 这个名字属于本世界，框架不认
    kind: action
    visibility: group      # 只有同组可见
    groups:
      - { by: camp, value: wolf, visible_to: group, mode: consensus }
  - id: dawn
    kind: broadcast
    source: eliminated_this_turn
  - id: discuss
    kind: action
    visibility: public
  - id: vote
    kind: collective
    rule: plurality
    tie: runoff
  - id: exile
    kind: apply
    target: collective_result
    effect: eliminate
  - id: check_win
    kind: check
    when: always
    break_if: win_decided
    repeat: 2
```

---

## 三、能力清单（自动生成，勿手改）

清单是**代码的投影**，不是第二份真相（C59）。要知道「能开什么、能排哪些阶段、每个阶段能填什么参数、哪些字段可写」，跑：

```powershell
.\.venv\Scripts\python.exe tools\describe_capabilities.py            # 打印
.\.venv\Scripts\python.exe tools\describe_capabilities.py --out worlds\_blank\能力清单.md
```

输出包含七节：系统开关 / 阶段种类 / 阶段谓词 / Agent 工具 / 可写状态字段 / 前端区块 / 世界包文件。

**为什么不在 README 里手抄一份**：手抄会漂移。代码加了参数而文档没加，世界包作者就会写出「校验不认」的配置。

---

## 四、常见错误 → 怎么改

| `check_world` 报错 | 原因 | 改法 |
|---|---|---|
| `C10 缺 seeds.yaml` / `C32 没有事件种子` | 没内容就开不了局 | 至少给 1 条 `seeds` |
| `C15 region main 的 entry=loc_99 不在地点表里` | `entry` 打错 | 改成 `regions/main.md` 里真实存在的 `loc_*` |
| `C7 item 含词表外 tag` | tag 不在 `rules/equipment.yaml: tag_vocab` | 换成本世界 era 允许的 tag |
| `C14 starter 的 item_x req_level 必须为 1` | 报到装备不能是高等级 | 改 `req_level: 1` 或换模板 |
| `未知系统 xxx` | 系统 id 拼错 | 跑 `describe_capabilities.py` 看准确 id |
| `已预留未实现` | 开了 `status: planned` 的系统（如 `weather`） | 关掉它，或等它实现 |
| `combat requires equipment` | 依赖没同开 | 同时开 `equipment`，或关掉 `combat` |
| `阶段 xxx 缺少 kind` | 只写了 `id` | 补 `kind`（7 个通用类型之一） |
| `kind=xxx 未注册` | 世界包发明了新 kind | 从 7 个里选；要新 kind 就落方案改框架 |
| `when=xxx 不在谓词注册表` | 谓词名拼错或非通用 | 见清单「阶段谓词」 |
| `when=location_tag 需要系统 map` | 谓词依赖的系统没开 | 开 `map`，或换谓词 |

---

## 五、改这个模板的纪律

1. **模板属于世界包，不属于框架**：这里可以出现任何世界词汇（`night`、`vote`），但 `trpg/` 里不行（C60）。
2. **改了模板必须让 `tests/test_blank_world.py` 继续通过**：它保证模板永远真的能跑。
3. **不要手写能力清单**：只写「怎么用」，能力清单交给 `describe_capabilities.py`。
