"""经验存储：agent 跨局积累的判断（程序记忆）。

与 `memory_vectors`（情景记忆）的分工：

| | 经验 | 记忆 |
|---|---|---|
| 回答 | 我学到了什么 | 发生过什么 |
| 形态 | 少而精的几句话 | 大量事件文本 |
| 用法 | 直接进 prompt（不走检索） | 向量检索，可能记不准 |
| 归属 | **agent**（跨局、跨存档） | 角色 × 本局 |

设计依据：Reflexion（Shinn et al. 2023）的语言化反思 + Voyager（Wang et al. 2023）
「验证成功才入库」的门槛 + 认知科学的「程序记忆固化进提示词」。

doc: 03-模块/M03-存储与存档/00-模块总述.md
module: M03
layer: L1
"""

from __future__ import annotations

import difflib
import os
import sqlite3
from datetime import datetime, timezone

#: 后端选择（L4）：
#: - `sqlite`（默认）：表 `agent_experience`，逐行存，已在用
#: - `store`：LangGraph `SqliteStore`（BaseStore），namespace `("agent", <id>, "experience")`
#:
#: 为什么两个后端并存：`store` 是目标形态（能接向量检索、与图共享 Store 抽象），
#: 但切默认要有一个观察期——真出问题时 `TRPG_EXPERIENCE_BACKEND=sqlite` 一键回去。
BACKEND_ENV = "TRPG_EXPERIENCE_BACKEND"

#: 两条经验相似度到这个程度就算「同一件事」，不重复写。
#: 依据 langmem 的 store manager 做法：先查相关记忆再决定 add/update，
#: 无脑追加会让同一条教训被写五遍，把 prompt 撑满。
DEDUPE_RATIO = 0.85

#: 每个 (agent, world) 最多保留多少条经验。
#: Reflexion 明确要求 cap 住反思数量——不封顶会让 prompt 越来越长，且旧经验会稀释新经验。
DEFAULT_KEEP = 12

#: 一次局末最多提炼几条。宁少勿滥：写错的经验会带进下一局害到自己。
MAX_PER_RUN = 3

_VALID_KINDS = ("lesson", "habit", "read")


def backend() -> str:
    """当前经验后端：`sqlite` / `store`。"""
    return str(os.environ.get(BACKEND_ENV) or "sqlite").strip().lower()


def namespace(agent_id: str) -> tuple[str, ...]:
    """BaseStore 命名空间。

    与 LangMem 的约定一致：`("agent", <id>, "experience")`。
    第三段用 `experience` 而不是 `memory`：这里存的是**程序记忆**（学到的判断），
    「发生过什么」那种情景记忆在 `memory_vectors`。
    """
    return ("agent", str(agent_id), "experience")


#: Store 实例缓存：**一个库文件一个实例**。
#: 每次调用都新建 `SqliteStore` 会让多个实例在同一条连接上交错 `BEGIN`，
#: 报 `cannot start a transaction within a transaction`。
_STORES: dict[str, object] = {}


def _store(conn: sqlite3.Connection):
    """为这个库文件拿一个 Store 实例。

    两件事必须做对，否则会踩事务冲突：

    1. **用独立连接、autocommit 模式**：`SqliteStore` 自己管 `BEGIN` / `COMMIT`，
       和业务连接的隐式事务撞在一起就报错；也不能把业务连接切成 autocommit
       （业务的多语句写入还依赖事务原子性）。
    2. **缓存实例**：同一个库文件只建一次。

    内存库没有文件路径，只能共用调用方那条连接（并把它切到 autocommit）。
    """
    from langgraph.store.sqlite import SqliteStore

    try:
        conn.commit()
        row = conn.execute("PRAGMA database_list").fetchone()
    except sqlite3.Error:  # 只读 / 已关闭：交给 Store 自己报错
        return SqliteStore(conn)

    path = str(row[2]) if row and row[2] else ""
    if not path:
        # 内存库是 per-connection 的，跨连接看不到表，只能共用调用方那条。
        # **不缓存**：`id(conn)` 在连接关闭后会被复用，缓存会指向已关闭的连接。
        # 调用方在同一个操作里只取一次 store 并复用，所以不需要缓存也不会冲突。
        conn.isolation_level = None
        return SqliteStore(conn)

    cached = _STORES.get(path)
    if cached is not None:
        return cached

    store_conn = sqlite3.connect(
        path, isolation_level=None, timeout=30.0, check_same_thread=False
    )
    store = SqliteStore(store_conn)
    _STORES[path] = store
    return store


def _read_items(store, agent_id: str, world_id: str) -> list[dict]:
    record = store.get(namespace(agent_id), str(world_id or ""))
    if record is None:
        return []
    value = getattr(record, "value", None) or {}
    return [dict(item) for item in (value.get("items") or []) if isinstance(item, dict)]


def _write_items(store, agent_id: str, world_id: str, items: list[dict]) -> None:
    store.put(namespace(agent_id), str(world_id or ""), {"items": list(items)})


def _normalize(text: str) -> str:
    return "".join(str(text or "").split())


def is_duplicate(existing: list[str], text: str, ratio: float = DEDUPE_RATIO) -> bool:
    """这条经验是不是已经记过了（措辞略有不同也算）。"""
    current = _normalize(text)
    if not current:
        return True
    for old in existing:
        candidate = _normalize(old)
        if not candidate:
            continue
        if current == candidate:
            return True
        if difflib.SequenceMatcher(None, current, candidate).ratio() >= ratio:
            return True
    return False


def add_experience(
    conn: sqlite3.Connection,
    agent_id: str,
    world_id: str,
    text: str,
    kind: str = "lesson",
    weight: float = 1.0,
    source_session: str = "",
    source_turn: int = 0,
    dedupe: bool = True,
) -> int:
    """记一条经验。返回新行 id；空文本或未知 kind 一律不写（返回 0）。

    不写空经验比写一条空话重要：空话会占额度、还会被当成「有经验」。
    """
    content = str(text or "").strip()
    if not content or not str(agent_id or "").strip():
        return 0
    kind_value = kind if kind in _VALID_KINDS else "lesson"

    if backend() == "store":
        store = _store(conn)
        items = _read_items(store, agent_id, world_id)
        if dedupe and is_duplicate([item.get("text", "") for item in items], content):
            return 0
        items.append(
            {
                "id": len(items) + 1,
                "kind": kind_value,
                "text": content[:200],
                "weight": float(weight),
                "source_session": str(source_session or ""),
                "source_turn": int(source_turn or 0),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        _write_items(store, agent_id, world_id, items)
        return int(items[-1]["id"])

    # 先查再写（langmem store manager 的做法）：重复的教训不该占额度。
    # `dedupe=False` 用于批量导入历史经验——那时要的是「全部留下」，不是「去重」。
    if dedupe:
        existing = [
            row[0]
            for row in conn.execute(
                "SELECT text FROM agent_experience WHERE agent_id = ? AND world_id = ?",
                (str(agent_id), str(world_id or "")),
            )
        ]
        if is_duplicate(existing, content):
            return 0

    cur = conn.execute(
        "INSERT INTO agent_experience "
        "(agent_id, world_id, kind, text, weight, source_session, source_turn, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            str(agent_id),
            str(world_id or ""),
            kind_value,
            content[:200],
            float(weight),
            str(source_session or ""),
            int(source_turn or 0),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def list_experience(
    conn: sqlite3.Connection,
    agent_id: str,
    world_id: str,
    limit: int = 8,
) -> list[dict]:
    """读某个 agent 在某个世界的经验，新的在前。"""
    if backend() == "store":
        items = _read_items(_store(conn), agent_id, world_id)
        return list(reversed(items))[: int(limit)]
    rows = conn.execute(
        "SELECT id, kind, text, weight, source_session, source_turn, created_at "
        "FROM agent_experience WHERE agent_id = ? AND world_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (str(agent_id), str(world_id or ""), int(limit)),
    ).fetchall()
    return [
        {
            "id": row[0],
            "kind": row[1],
            "text": row[2],
            "weight": row[3],
            "source_session": row[4],
            "source_turn": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]


def prune_experience(
    conn: sqlite3.Connection,
    agent_id: str,
    world_id: str,
    keep: int = DEFAULT_KEEP,
) -> int:
    """只保留最近 `keep` 条，删掉更旧的。返回删了多少条。

    为什么按「最近」而不是「权重最低」：经验的价值随时间衰减——
    十局前的教训，不如上一局刚吃过的亏管用。
    """
    if backend() == "store":
        store = _store(conn)
        items = _read_items(store, agent_id, world_id)
        keep_n = max(0, int(keep))
        if len(items) <= keep_n:
            return 0
        removed = len(items) - keep_n
        # items 是「旧 → 新」，保留尾部的 keep_n 条
        _write_items(
            store, agent_id, world_id, items[len(items) - keep_n :] if keep_n else []
        )
        return removed

    keep = max(0, int(keep))
    ids = [
        row[0]
        for row in conn.execute(
            "SELECT id FROM agent_experience WHERE agent_id = ? AND world_id = ? "
            "ORDER BY id DESC",
            (str(agent_id), str(world_id or "")),
        )
    ]
    stale = ids[keep:]
    if not stale:
        return 0
    conn.executemany(
        "DELETE FROM agent_experience WHERE id = ?", [(i,) for i in stale]
    )
    conn.commit()
    return len(stale)


def count_experience(conn: sqlite3.Connection, agent_id: str, world_id: str) -> int:
    if backend() == "store":
        return len(_read_items(_store(conn), agent_id, world_id))
    row = conn.execute(
        "SELECT COUNT(*) FROM agent_experience WHERE agent_id = ? AND world_id = ?",
        (str(agent_id), str(world_id or "")),
    ).fetchone()
    return int(row[0] if row else 0)
