# ai-trpg

多 Agent 回合制跑团引擎（个人学习/娱乐项目）。

**判定归代码，叙事归模型**：骰子、难度、状态变更全部由代码计算并落库，LLM 只负责把已定的事实编织成场景与后果。

## 状态

S0 完成：CLI 跑通「场景 → 玩家行动 → 骰子判定 → 叙事 → 状态合并」，全流程可复现（同 seed 骨架一致）。
已接入 MiniMax（OpenAI 兼容的 `chatcompletion_v2`），也可用 `--provider mock` 完全离线跑。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 离线跑（假 LLM，验证编排与可复现）
python run.py run --provider mock --seed 42 --turns 20 --players 4

# 跑测试
python -m pytest -q
```

## 接真实模型

复制 `.env.example` 为 `.env`，填 `MINIMAX_API_KEY`（`.env` 已在 `.gitignore` 中）：

```
MINIMAX_API_KEY=sk-...
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=MiniMax-M3.1-Flash
MINIMAX_MODEL_PLAYER=          # 可选，玩家 Agent 单独模型
```

```powershell
python run.py run --provider minimax --turns 3 --players 3
```

真实调用每回合约 `2N+1` 次（1 次场景 + N 次玩家 + N 次叙事）。**先用 2~3 回合验证叙事质量**，再放量跑。

## 目录

```
run.py                       CLI
trpg/
├── models/                  数据契约：CharacterCard / GameState / PlayerAction / StateDelta / Equipment
├── rules/                   骰子（可复现）与规则表加载
├── engine/                  判定 / 状态增量校验 / 回合状态机
├── llm/                     MiniMax 客户端、mock、prompt 组装
└── storage/                 SQLite 表结构
rules/                       数值与表（YAML，唯一真相）
worlds/                      世界包（数据插件，不改代码）
tests/                       离线测试（36 个用例）
tools/import_env.py          从已有 .env 导入配置（不打印密钥）
```

## 设计文档

设计文档不在本仓库，位于个人知识库 `07-AI项目/AI跑团/`（概设、数据契约、规则数值、回合与 Prompt）。
本仓库只放「是什么 + 怎么跑」。

## 世界包

`worlds/<world_id>/` 一个目录即一个世界，需含 `world.yaml` / `eras/` / `regions/` / `seeds.yaml` / `items.yaml` / `npcs.yaml`。
模板见 `worlds/_template/`。基于他人作品的世界包请勿提交到公开仓库（见 `.gitignore`）。
