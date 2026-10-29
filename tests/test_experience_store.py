"""L4：经验迁 LangGraph `BaseStore`（`TRPG_EXPERIENCE_BACKEND=store`）。

两个后端必须**行为一致**——否则切默认那天会静默掉数据。
所以这里的断言不是"Store 能用"，而是"Store 与 SQLite 给出同样的结果"。

存储形态差异（有意为之）：

| 后端 | 形态 |
|---|---|
| `sqlite` | 一行一条经验，`agent_experience` 表 |
| `store` | 一个 world 一条记录（`namespace=("agent", id, "experience")`，`key=world_id`），value 里放 items 列表 |

对外函数签名不变，上层（`reflect` / `pc_system`）完全不感知。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trpg.storage.conn import init_db  # noqa: E402
from trpg.storage.experience import (  # noqa: E402
    BACKEND_ENV,
    DEFAULT_KEEP,
    add_experience,
    backend,
    count_experience,
    list_experience,
    namespace,
    prune_experience,
)


@pytest.fixture
def store_conn(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV, "store")
    connection = sqlite3.connect(":memory:")
    init_db(connection)
    yield connection
    connection.close()


def test_backend_switches_by_env(store_conn):
    assert backend() == "store"


def test_namespace_matches_langmem_convention():
    """命名空间形状要与 LangMem 一致，将来接向量索引不用改 key。"""
    assert namespace("agt_1") == ("agent", "agt_1", "experience")


def test_add_and_list(store_conn):
    add_experience(store_conn, "agt_1", "werewolf", "别第一个跳预言家")

    items = list_experience(store_conn, "agt_1", "werewolf")

    assert len(items) == 1
    assert items[0]["text"] == "别第一个跳预言家"
    assert items[0]["kind"] == "lesson"


def test_isolated_per_agent(store_conn):
    add_experience(store_conn, "agt_1", "werewolf", "我只信自己看到的")
    add_experience(store_conn, "agt_2", "werewolf", "装傻比装聪明活得久")

    assert [i["text"] for i in list_experience(store_conn, "agt_1", "werewolf")] == [
        "我只信自己看到的"
    ]


def test_isolated_per_world(store_conn):
    add_experience(store_conn, "agt_1", "werewolf", "这边的教训")
    add_experience(store_conn, "agt_1", "ironvale", "那边的教训")

    assert count_experience(store_conn, "agt_1", "werewolf") == 1
    assert [i["text"] for i in list_experience(store_conn, "agt_1", "ironvale")] == [
        "那边的教训"
    ]


def test_empty_text_is_not_stored(store_conn):
    assert add_experience(store_conn, "agt_1", "werewolf", "   ") == 0
    assert count_experience(store_conn, "agt_1", "werewolf") == 0


def test_duplicate_is_skipped(store_conn):
    add_experience(store_conn, "agt_1", "werewolf", "别第一个跳预言家")

    assert add_experience(store_conn, "agt_1", "werewolf", "别第一个跳预言家") == 0
    assert count_experience(store_conn, "agt_1", "werewolf") == 1


def test_list_is_newest_first(store_conn):
    add_experience(store_conn, "agt_1", "werewolf", "先写的")
    add_experience(store_conn, "agt_1", "werewolf", "后写的")

    assert [i["text"] for i in list_experience(store_conn, "agt_1", "werewolf")] == [
        "后写的",
        "先写的",
    ]


def test_prune_keeps_most_recent(store_conn):
    for index in range(DEFAULT_KEEP + 2):
        add_experience(store_conn, "agt_1", "werewolf", f"经验 {index}", dedupe=False)

    removed = prune_experience(store_conn, "agt_1", "werewolf", keep=DEFAULT_KEEP)

    assert removed == 2
    assert count_experience(store_conn, "agt_1", "werewolf") == DEFAULT_KEEP
    latest = list_experience(store_conn, "agt_1", "werewolf")
    assert latest[0]["text"] == f"经验 {DEFAULT_KEEP + 1}"
    assert all(i["text"] != "经验 0" for i in latest), "最旧的应当被淘汰"


def test_same_fields_as_sqlite_backend(monkeypatch):
    """两个后端返回的字段集合必须一致，否则切默认会静默少字段。"""
    from trpg.storage.experience import add_experience as add

    sqlite_conn = sqlite3.connect(":memory:")
    init_db(sqlite_conn)
    monkeypatch.delenv(BACKEND_ENV, raising=False)
    add(sqlite_conn, "agt_1", "werewolf", "同一句话")
    sqlite_item = list_experience(sqlite_conn, "agt_1", "werewolf")[0]

    store_conn = sqlite3.connect(":memory:")
    init_db(store_conn)
    monkeypatch.setenv(BACKEND_ENV, "store")
    add(store_conn, "agt_1", "werewolf", "同一句话")
    store_item = list_experience(store_conn, "agt_1", "werewolf")[0]

    assert set(store_item) == set(sqlite_item)
    assert store_item["text"] == sqlite_item["text"]
    assert store_item["kind"] == sqlite_item["kind"]
    sqlite_conn.close()
    store_conn.close()
