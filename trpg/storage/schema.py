"""M03 存储 · 建表 DDL。

表族：全部（15 张表；`turn_receipts` 为 B45 新增的回合级回执）
只做 SQL 存取，不校验业务规则；业务规则在 engine/ 与各模块。

doc: 03-模块/M03-存储/00-模块总述.md
module: M03
layer: L2
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    seed         TEXT,
    world_id     TEXT,
    era          TEXT,
    status       TEXT,
    created_at   TEXT,
    timeline     TEXT DEFAULT '',       -- 二期：开局用的时间点（academy_year1 等）
    world_day    INTEGER DEFAULT 1,     -- 三期：世界第几天（同世界连续多局会累加）
    human_ids_json TEXT DEFAULT '[]',   -- 二期：人类席位（重启后 resume 用）
    account_id   TEXT DEFAULT 'local',  -- P0：归属（accounts.id；未认证回退 'local'）
    updated_at   TEXT DEFAULT '',       -- P0：最后推进时间（列表排序用）
    current_turn INTEGER DEFAULT 0,     -- P0：已推进回合（列表展示用）
    summary      TEXT DEFAULT '',       -- P0：一句话摘要（列表展示用）
    config_locked INTEGER DEFAULT 0     -- P0：配置是否已锁死
);


CREATE TABLE IF NOT EXISTS world_agents (
    world_id   TEXT,
    agent_id   TEXT,
    account_id TEXT DEFAULT 'local',
    created_at TEXT,
    PRIMARY KEY (world_id, agent_id, account_id)
);

CREATE TABLE IF NOT EXISTS world_state (
    session_id   TEXT,
    version      INTEGER,
    turn         INTEGER DEFAULT 0,
    state_json   TEXT,
    PRIMARY KEY (session_id, version)
);

CREATE TABLE IF NOT EXISTS endings (
    session_id   TEXT PRIMARY KEY,
    kind         TEXT,
    turn         INTEGER,
    payload_json TEXT,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS turns (
    session_id   TEXT,
    idx          INTEGER,
    actor_id     TEXT,
    thought      TEXT,
    action       TEXT,
    narration    TEXT,
    options_json TEXT DEFAULT '[]',   -- P4：本回合结构化可选行动（每行同一份）
    PRIMARY KEY (session_id, idx, actor_id)
);

-- 经验（agent 跨局积累）：**程序记忆**，不是事件记忆。
-- 与 `memory_vectors`（情景记忆，靠检索回忆）的分工：
--   经验回答「我学到了什么」→ 少而精，直接进 prompt
--   记忆回答「发生过什么」→ 向量检索，可能记不准
-- 挂 `agent_id` 而不是 `session_id`：同一个 agent 换局、换存档，经验都跟着它。
CREATE TABLE IF NOT EXISTS agent_experience (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      TEXT,
    world_id      TEXT,
    kind          TEXT DEFAULT 'lesson',
    text          TEXT,
    weight        REAL DEFAULT 1.0,
    source_session TEXT DEFAULT '',
    source_turn   INTEGER DEFAULT 0,
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_exp ON agent_experience (agent_id, world_id);

-- 回合级回执（B45）：order / skipped / votes 是「这一回合」的单一记录，
-- 不适合塞进 per-actor 的 turns 表（会重复 N 行）
CREATE TABLE IF NOT EXISTS turn_receipts (
    session_id      TEXT,
    idx             INTEGER,
    order_json      TEXT DEFAULT '[]',
    skipped_json    TEXT DEFAULT '[]',
    votes_json      TEXT DEFAULT '[]',
    vote_result_json TEXT DEFAULT '{}',
    PRIMARY KEY (session_id, idx)
);

CREATE TABLE IF NOT EXISTS dice_rolls (
    session_id   TEXT,
    turn         INTEGER,
    actor_id     TEXT,
    expr         TEXT,
    rolls        TEXT,
    total        INTEGER,
    dc           INTEGER,
    margin       INTEGER,
    outcome      TEXT,
    rng_state    TEXT
);

CREATE TABLE IF NOT EXISTS character_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    char_id      TEXT,
    turn         INTEGER,
    world_day    INTEGER,
    world_hour   INTEGER,
    kind         TEXT,
    summary      TEXT,
    entities     TEXT,
    importance   INTEGER DEFAULT 5
);

CREATE INDEX IF NOT EXISTS idx_char_events
    ON character_events (session_id, char_id, turn);

CREATE TABLE IF NOT EXISTS characters (
    id           TEXT PRIMARY KEY,
    name         TEXT,
    profession   TEXT,
    card_json    TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    world_id     TEXT DEFAULT '',     -- 卡属于哪个世界包（空 = 通用卡）
    account_id   TEXT DEFAULT 'local' -- P0：归属（accounts.id）
);

-- 账号与登录会话（公网开放用；防未授权消耗 token）
CREATE TABLE IF NOT EXISTS accounts (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT DEFAULT 'user',
    disabled      INTEGER DEFAULT 0,
    created_at    TEXT,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    created_at TEXT,
    expires_at TEXT,
    user_agent TEXT
);

-- 局外成长（meta）：跨世界通用的抽象点数与元成长
CREATE TABLE IF NOT EXISTS meta_progress (
    owner_id   TEXT NOT NULL,
    world_id   TEXT DEFAULT '',
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at TEXT,
    PRIMARY KEY (owner_id, world_id, key)
);

CREATE TABLE IF NOT EXISTS meta_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   TEXT NOT NULL,
    world_id   TEXT DEFAULT '',
    key        TEXT NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT,
    session_id TEXT,
    ts         TEXT
);

-- 物品使用计数（prop → instance → template 的晋升判据）
CREATE TABLE IF NOT EXISTS item_usage (
    world_id   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    canonical  TEXT NOT NULL,
    kind       TEXT DEFAULT 'prop',
    tags       TEXT DEFAULT '[]',
    uses       INTEGER DEFAULT 0,
    sessions   INTEGER DEFAULT 0,
    first_turn INTEGER DEFAULT 0,
    last_turn  INTEGER DEFAULT 0,
    PRIMARY KEY (world_id, session_id, canonical)
);

-- S6-3：道具 × Agent 的使用维度（跨局稳定 agent 标识同批准备）
-- 与 item_usage 并存：前者是「全局累计」，这里按 agent 分开计数，
-- 晋升候选按 **道具 × Agent** 判定（agent_id 缺省为空 = 未标注来源的旧数据）。
CREATE TABLE IF NOT EXISTS item_usage_agent (
    world_id   TEXT NOT NULL,
    session_id TEXT NOT NULL,
    agent_id   TEXT NOT NULL DEFAULT '',
    canonical  TEXT NOT NULL,
    uses       INTEGER DEFAULT 0,
    first_turn INTEGER DEFAULT 0,
    last_turn  INTEGER DEFAULT 0,
    PRIMARY KEY (world_id, session_id, agent_id, canonical)
);

-- 世界账本：世界侧的跨局状态（NPC 生死、已发生的事件）
CREATE TABLE IF NOT EXISTS world_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    world_id       TEXT NOT NULL,
    key            TEXT NOT NULL,
    kind           TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    source_session TEXT,
    source_turn    INTEGER,
    ts             TEXT,
    pinned         INTEGER DEFAULT 0,
    account_id     TEXT DEFAULT 'local'  -- P0：归属（accounts.id）
);

CREATE INDEX IF NOT EXISTS idx_ledger_world_key ON world_ledger(world_id, key);

CREATE TABLE IF NOT EXISTS session_cards (
    session_id   TEXT,
    char_id      TEXT,
    card_json    TEXT,
    PRIMARY KEY (session_id, char_id)
);

-- P3：Agent（人）。一个 Agent 可以有多张角色卡（1:N），记忆挂 agent 跨局
CREATE TABLE IF NOT EXISTS agents (
    id                 TEXT PRIMARY KEY,
    account_id         TEXT DEFAULT 'local',
    name               TEXT,
    persona            TEXT DEFAULT '',
    voice              TEXT DEFAULT '',
    default_model_json TEXT DEFAULT '',
    created_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_agents_owner ON agents (account_id);

CREATE TABLE IF NOT EXISTS memory_vectors (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT,
    char_id      TEXT,
    turn         INTEGER,
    kind         TEXT,
    ref_id       TEXT,
    text         TEXT,
    embedding    TEXT
);

CREATE INDEX IF NOT EXISTS idx_mem_vec ON memory_vectors (session_id, char_id);

-- P2.5：一局在一个区域里的地图。开局生成一次，之后只读（运行时不得写 worlds/）
CREATE TABLE IF NOT EXISTS session_map (
    session_id   TEXT,
    region       TEXT,
    seed         TEXT,
    map_json     TEXT,
    created_at   TEXT,
    PRIMARY KEY (session_id, region)
);

-- B25：章节摘要（读档/回滚后回灌上下文，否则重启即失忆）
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT,
    upto_turn  INTEGER,
    text       TEXT,
    created_at TEXT,
    PRIMARY KEY (session_id, upto_turn)
);

CREATE TABLE IF NOT EXISTS agent_calls (
    session_id   TEXT,
    turn         INTEGER,
    agent        TEXT,
    task         TEXT DEFAULT '',
    model        TEXT,
    tokens_in    INTEGER,
    tokens_out   INTEGER,
    latency_ms   INTEGER,
    request_json TEXT,
    response_raw TEXT,
    retry        INTEGER DEFAULT 0
);

-- 信念历史（S2-2）：**每个角色每回合**一条信念快照。
-- 为什么单开一张表，而不是并进 `turn_receipts`：
--   turn_receipts 是「一回合一行」（order/skipped/votes），而信念是**每人一份**，
--   N 人一局就是 N 行/回合，塞进去必然要改成子表，形状完全不同。
-- 为什么不让 state 快照兜着：state 按 version 全量存，体积随回合线性膨胀，
--   而且「回滚到第 N 回合」会把信念一起回滚——信念应当**只增不改**地留痕。
-- 旧库兼容：新表走 `CREATE TABLE IF NOT EXISTS`，init_db 每次启动都会补建，无需迁移。
CREATE TABLE IF NOT EXISTS agent_beliefs (
    session_id  TEXT,
    turn        INTEGER,
    actor_id    TEXT,
    mindset_json TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT '',
    PRIMARY KEY (session_id, turn, actor_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_beliefs ON agent_beliefs (session_id, actor_id, turn);

-- 状态迁移日志（S3-1）：**append-only**，与状态快照同点写入（引擎 `_persist` 处）。
-- 为什么单开一张表，而不是塞进 `turns`：`turns` 是 per-actor 的一行行动，
-- 而迁移日志要串成「这一局发生了什么」的**时间线**（含判断、死亡、场景切换等）。
-- 为什么 append-only：回滚 = 状态快照 + 迁移日志重放，历史不能被覆盖。
-- 私密内容不进这里：迁移日志只记公开信息（行动 / 判定摘要 / 叙事摘要）。
CREATE TABLE IF NOT EXISTS state_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    turn        INTEGER,
    actor_id    TEXT DEFAULT '',
    kind        TEXT,
    payload_json TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_state_transitions ON state_transitions (session_id, turn);

-- NPC 关系（S5-1）：**带时间区间的事实**，append-only。
-- 为什么不是覆盖写：关系会变（盟友 → 破裂），只存当前值就不可回溯。
-- 双时态两维：valid_from/valid_to = 事实何时在世界里成立；recorded_turn = 系统何时知道。
-- 关掉一条关系 = 闭合区间（写 valid_to），历史不删。
CREATE TABLE IF NOT EXISTS npc_relations (
    session_id    TEXT,
    subject       TEXT,
    object        TEXT,
    kind          TEXT,
    weight        REAL DEFAULT 0,
    valid_from    INTEGER DEFAULT 0,
    valid_to      INTEGER,
    source_event  TEXT DEFAULT '',
    recorded_turn INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_npc_relations ON npc_relations (session_id, subject);
CREATE INDEX IF NOT EXISTS idx_npc_relations_at ON npc_relations (session_id, valid_from, valid_to);

-- 事件历史（S5-4）：**append-only**，事件由**纯代码调度**产生（不经 GM）。
-- 为什么不在 state 里：事件是「发生过的事」，不是玩法状态；
--   也不进 StateDelta（模型不得写事件）。冷却 = 同事件最后一次出现回合 + 声明的 cooldown_turns。
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    turn        INTEGER,
    event_id    TEXT,
    kind        TEXT DEFAULT '',
    payload_json TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_event_log ON event_log (session_id, turn);
"""
