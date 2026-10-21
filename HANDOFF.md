# HANDOFF · AI跑团（多 Agent 回合制 TRPG）

> 本文件用于新会话接手。读完这一份就能继续干活，不需要回看历史对话。

## 0. 30 秒看懂

一个**单机、本地运行**的多 Agent 跑团引擎。每个玩家角色是独立 Agent（有自己的人格、私有目标、记忆、模型配置），GM 是另一个 Agent（负责叙事），NPC 也是独立 Agent。判定与状态变更**全部由代码计算**，LLM 只负责"把已定的事实写成故事"。

技术栈：Python 3.12 + pydantic v2 + FastAPI + SQLite + MiniMax（OpenAI 兼容）。
仓库：`https://github.com/jmr-circumlunar/ai-trpg`（公开版只含原创占位世界 `ironvale`；正式世界 `longzu` 在 `.gitignore` 里，仅本地）。

## 1. 四期目标（已定，不再重议）

| 期 | 目标 | 状态 |
|---|---|---|
| **一期** | 完整多 Agent 跑团，**单一背景（龙族·卡塞尔学院）**的完整性 | **进行中** |
| 二期 | 人机混合 + 多背景多时间线 | 未开始 |
| 三期 | **局外成长体系**（跨局经验/创伤/遗物/退役） | 未开始 |
| 四期 | 每局对世界产生**有层级有权重**的影响（locality→city→nation→world）+ 章节小结局作新开局节点 | 未开始 |

## 2. 运行方式

```powershell
cd C:\workspace\bosssoft\ai-trpg
.\.venv\Scripts\Activate.ps1

# 离线验证（不烧 token）
python run.py run --provider mock --turns 20 --players 4

# 真实模型（MiniMax-M3.1-Flash）
python run.py run --provider auto --turns 3 --players 3

# 指定世界
python run.py run --provider auto --world longzu --turns 3 --players 3

# 网页
python -m uvicorn api:app --port 8000
# 游戏页 http://127.0.0.1:8000/    角色配置 http://127.0.0.1:8000/characters

# 测试
python -m pytest -q
```

**密钥**：`.env`（已 gitignore），字段 `MINIMAX_API_KEY` / `MINIMAX_BASE_URL` / `MINIMAX_MODEL`。

**服务器**：阿里云轻量 2C2G（Ubuntu 24.04），`root@39.106.64.72`，systemd 服务名 `ai-trpg`，代码在 `/opt/ai-trpg`。**8000 端口尚未在阿里云防火墙放行**，外网打不开。部署方式：本地 `git archive` → `scp` → 服务器 `tar -xf` → `systemctl restart ai-trpg`。

## 3. 当前状态

- 测试：**144 passed**
- 最新 commit：`fd6802a`（NPC 时间窗口 + 势力注入）
- 引擎：可跑通完整回合循环（场景 → 顺序决策 ReAct → 判定 → 叙事 → 状态合并 → 落库）
- 世界包：`longzu` 已建（52 NPC / 12 种子 / 15 装备 / 4 区域 / core 2631 字 / archive 3 篇）
- **卡点**：`longzu` 还开不了局——角色生成仍走 `professions.yaml`（盗贼/战士那套），需要接 `origins.yaml` + `paths.yaml`

## 4. 已完成模块

```
api/            FastAPI：世界/会话/角色库/推进/trace/救援/静态页
trpg/
├ models/       CharacterCard / GameState / StateDelta / Equipment / enums / memory
├ rules/        loader（YAML 只读）+ dice（可复现）+ clock（时间纯函数）
├ engine/       judge（判定）+ state_merge（Delta 校验）+ loop（回合状态机）
├ llm/          agent.py（业务）+ config.py（provider 解析）+ prompts.py + providers/*
├ memory/       embedder / vector_store / recall（RRF）/ settings
├ storage/      db（SQLite）+ memory（事件）+ characters（角色库）
├ world/        pack（分层加载 + NPC 时间窗口）
└ characters.py 角色卡生产侧（职业模板 + 预设小队）
```

### 已实现的关键能力

| 能力 | 位置 | 说明 |
|---|---|---|
| 四档判定 | `engine/judge.py` | crit_success / success / fail_forward / failure，四个 DC 修正源（dc_base / stage / intent / time） |
| StateDelta 校验 | `engine/state_merge.py` | 路径白名单 + 边界 + 幅度 + 结构复校验，非法整包回滚 |
| 时间系统 | `rules/clock.yaml` + `rules/clock.py` | 只能前进，档位 5min~24h，跨天进位，GM 未申报则按行动类型兜底 |
| 世界包分层 | `world/pack.py` | core 常驻 / region 按需 / archive 进向量 |
| 记忆三层 | `llm/agent.py` + `config/memory.yaml` | 近 3 回合常驻 + 每 5 回合摘要 + 长期靠 recall tool |
| 向量召回 | `memory/*` | MiniMax `embo-01`，暴力余弦，结构化 ∪ 向量 → RRF(k=60)，带召回日志 |
| ReAct Agent | `llm/agent.py::run_agent_turn` | tool: recall / look / check_self / final，最多 3 步 |
| 顺序决策 | `engine/loop.py` | 按 dex 降序逐个思考+判定，后者能看到前者结果 |
| NPC 独立 Agent | `llm/agent.py::npc_action` | GM 申报在场者（≤2 个，代码校验时间窗口），并行行动 |
| 角色库 CRUD | `api/app.py` + `storage/characters.py` | 5 个端点 + 预设导入 + 配置页 |
| 会话持久化 | `api/app.py` | 重启后 `resume` 能接着玩 |

## 5. 未完成（一期剩余，按依赖排序）

| # | 工作 | 关键文件 | 说明 |
|---|---|---|---|
| 1 | **职业体系代码**（硬卡点） | `models/character.py` / `characters.py` / `world/pack.py` | `AIExtended.profession` 拆成 `origin` + `path` + `talents`；按 `origins.yaml` 权重抽；`path` 默认 `freshman` |
| 2 | **Director Agent** | 新建 `llm/agent_director.py` | 生成本局主线写进 `quests`；决定在场 NPC / 是否推幕次 / 是否进协商。纯结构化输出 |
| 3 | 装备接入 | `rules/equipment.yaml` | GM 申报 `loot` → 代码掷 tier/成色/属性；装备给 tag 修正（撬锁工具 DC −2） |
| 4 | 战斗系统（简单版） | 新建 `engine/combat.py` | 攻击/防御/逃跑 + 骰子判定 + 伤害 = 武器atk + 骰 - 敌def；敌人带 hp/atk/def |
| 5 | 死亡 DYING→DEAD | `engine/loop.py` | hp≤0 进 DYING（2 回合可救），超时 DEAD，死后不入场 |
| 6 | 结算快照 + 故事线重置 | `storage/db.py` + `api/app.py` | 手动结束/全灭 → epilogue + 结算落库；`reset?to_turn=N` 回滚 |
| 7 | trace 页 | `static/trace.html` | 含骰子明细、召回详情、状态变更 |
| 8 | SSE 真流式 | `api/app.py` | token 级推送，改善 30s 干等 |
| 9 | GM 配置页 | `static/gm.html` | 世界/风格/思考深度 |

**明确不做（一期）**：人机混合槽位、多世界内容、跨 era 传承、局对世界的影响、地图/装备图渲染。

## 6. 不可重议的设计决策

| # | 决策 | 理由 |
|---|---|---|
| 1 | **判定归代码，叙事归模型** | LLM 不得改判，否则不可复现 |
| 2 | 四档 outcome（含 `fail_forward`） | 失败要留出路，不能卡死剧情 |
| 3 | 单一真相源 `GameState` + `StateDelta` 增量 | 每次变更都过白名单与结构校验 |
| 4 | 骰子自实现（seed 注入），不用 `avrae/d20` | 可复现是硬指标 |
| 5 | **记忆是 tool，不是自动注入** | Agent 自己决定要不要回忆；正常回合不回忆 |
| 6 | 记忆三层：近 3 回合常驻 / 摘要 / 长期检索 | 短期"完全记得"，长期才检索 |
| 7 | 向量用 MiniMax `embo-01`（`texts` + `type=db\|query`） | 参数名与 OpenAI 不同 |
| 8 | 暴力余弦，**不用 sqlite-vec** | Windows Python 的 `load_extension` 常年不可用 |
| 9 | 结构化 ∪ 向量 → RRF(k=60) | 只结构化永远是那几条；只向量会漏重要的 |
| 10 | **顺序决策**（按 dex），不并行 | "看到队友干了什么再决定"是跑团核心；代价是耗时 ×N |
| 11 | 世界包分三层（core/region/archive） | 龙族信息量远超一个 prompt |
| 12 | 职业三层：origin（不可变）/ talent / path（剧情可变） | "参军"是 path 变更，不是换职业 |
| 13 | NPC 时间窗口独立成表（`npcs_timeline.yaml`） | 加时间点只动一张表，不动 52 条人物档案 |
| 14 | 学院按年份分级：恺撒 2007（高两级）、楚子航/诺诺 2008（高一级）、玩家/路明非 2009（同期）、夏弥/伊莎贝尔 2010（低一级） | 原著设定，已核实 |
| 15 | 模型统一 `MiniMax-M3.1-Flash`，`reasoning_effort: medium` | 不引入第二模型；medium 是为了保留思考过程可看 |
| 16 | 一期不做自动结局，只有全灭 / 手动结束 | 章节小结局留四期 |
| 17 | 龙族包只存本地，公开仓库只留 `ironvale` | 版权边界：只用设定与机构，人物剧情自建 |

## 7. 踩坑（必读，都是真实踩过的）

| # | 坑 | 修法 |
|---|---|---|
| 1 | f-string 里的 JSON 示例把解释器带崩 | 花括号一律双写 `{{ }}`；prompt 当代码审 |
| 2 | StateDelta 把 dict 塞进 pydantic 字段，下一步才爆 | `apply_delta` 末尾做 `GameState.model_validate` 复校验 |
| 3 | MiniMax 529（集群过载）被当业务失败 | 网络层单独重试：429/5xx/529 → 退避 1s/2s/4s |
| 4 | 输出被 max_tokens 截断 | `finish_reason == length` 时按 1.6 倍预算重试 |
| 5 | Windows 控制台 GBK 打碎中文 | `sys.stdout.reconfigure(encoding="utf-8")` |
| 6 | **推理模型把 max_tokens 吃光，content 为空** | `reasoning_content` 计入输出；预算上调 + 截断重试 |
| 7 | workbench CLI 不支持轻量服务器 | 轻量走 `swas-open` API；只能用 SSH |
| 8 | 官方 install.ps1 在非 SYSTEM 身份下装到 Program Files 失败 | 手动下载 zip + 校验 SHA256 + 装用户目录 |
| 9 | 推理模型单次 30s+ | `reasoning_effort: low` 曾把 108s 降到 18s；**但现在为看思考过程改回 medium** |
| 10 | **并行把同 seed 可复现打碎** | 共享 RNG 被多线程交错消费；改每个 `(actor, turn)` 独立 RNG |
| 11 | Clock 字段存在 ≠ 时间机制存在 | 三处"看起来做了"（模型/白名单/渲染）但没人推进；加兜底 + 测试 |
| 12 | f-string 里写多行表达式直接 SyntaxError | 需要的值先赋局部变量再进模板 |
| 13 | GM 把 list 字段写成数组导致嵌套 `[["exposed"]]` | `add` 到 list 时摊平 |

## 8. 文件地图（世界包）

```
worlds/longzu/            ← 本地私有，gitignore
├── world.yaml            基调/禁忌/7 个势力/4 个区域
├── timeline.yaml         4 个时间点（只开 academy_year1）
├── npcs.yaml             52 个 NPC（档案：goal/secret/attitude/tags）
├── npcs_timeline.yaml    时间窗口（enters/leaves，与档案分表）
├── origins.yaml          5 种血统出身（权重 5/52/30/11/2）+ 6 天赋
├── paths.yaml            8 种身份（新生→执行部→叛逃/堕落）
├── items.yaml            15 个装备 + 七宗罪（传说）
├── seeds.yaml            12 条事件种子
├── eras/academy.yaml     学院时期，tag 白名单
├── core/                 常驻 prompt（2631 字）
│   ├── 01-血统与言灵.md
│   ├── 02-世界铁律.md
│   └── 03-学院与时代.md
├── regions/              campus / dorm / underground / chicago（各 5~10 地点）
└── archive/              进向量库（2677 字）
    ├── glossary.md       名词表（言灵序列/血之哀/龙侍/七宗罪…）
    ├── history.md        黑王/白王叛乱/四大君主/学院建立
    └── storylines.md     加图索家/蛇岐八家/猛鬼众/路鸣泽契约
```

## 9. 常用命令

```powershell
# 数据检查
python run.py worlds                                          # 列出世界包
python -c "from trpg.world import load_world; p=load_world('longzu','.'); print(len(p.npcs_for('academy_year1')))"

# 记忆/上下文参数调优：改 config/memory.yaml，不改代码
# 召回日志：run.py 输出里看「〔回忆〕」行

# 提交
git add -A
git -c user.email="jmr.circumlunar@gmail.com" -c user.name="jmr-circumlunar" commit -m "..."
git -c http.proxy=http://127.0.0.1:7897 push origin main
```

## 10. 新会话第一件事

**做第 1 项：职业体系代码。** 具体：

1. `models/character.py`：`AIExtended` 加 `origin: str` / `path: str` / `talents: list[str]`，保留 `profession` 作为兼容字段
2. `world/pack.py`：加载 `origins.yaml` / `paths.yaml`，提供 `origins_pool()` / `path_default()`
3. `characters.py`：新增 `new_card_by_origin(pack, origin_id)`，按权重抽 origin、抽天赋、path 默认 `freshman`
4. `engine/state_merge.py`：白名单加 `characters.<id>.path`（允许剧情改身份）
5. `game.build_cards`：世界包有 origins 时走新逻辑，否则回落旧 professions
6. 测试：抽 1000 次权重分布合理；path 变更被接受、非法 path 被拒

做完就能 `python run.py run --provider auto --world longzu --turns 3 --players 3` 跑第一个龙族局，再验证 core/archive/NPC 时间窗口在真实叙事里的效果。

## 11. 沟通约定

- 用户是项目负责人（蒋明睿），懂架构，会指出设计错误（已经纠正过多次，比如"记忆应该是 tool""恺撒只高一级"）
- **不要用速度换质量**：明确说过"不要求速度，需要的结果"
- **设定要核实**：涉及原著的内容，先联网查，不要凭印象
- **世界包能力不应该每次靠改代码实现**：加内容前先想清楚 schema 够不够
- 文档纪律：文档即契约、只写结果、过程分离、架构先出方案
