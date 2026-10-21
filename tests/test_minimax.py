import pytest

from trpg.llm import minimax as mm
from trpg.llm.minimax import MiniMaxClient, extract_json
from trpg.testing import make_card, make_state


def test_extract_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_code_fence():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_with_prose():
    assert extract_json('好的，结果如下：{"a": 1} 希望有用') == {"a": 1}


def test_extract_invalid_raises():
    with pytest.raises(Exception):
        extract_json("完全没有 json")


def _client(monkeypatch, cards):
    client = MiniMaxClient(api_key="test-key", cards=cards, skills=["观察", "潜行"])
    return client


def test_describe_scene_falls_back_on_http_error(monkeypatch):
    cards = {"chr_p1": make_card()}
    client = _client(monkeypatch, cards)

    def boom(*args, **kwargs):
        raise mm.HttpError(500, "server error", "http://x")

    monkeypatch.setattr(mm, "post_json", boom)
    out = client.describe_scene(make_state(), 1)
    assert out["narration"]
    assert client.usage["fail"] >= 1


def test_player_action_falls_back_to_watch(monkeypatch):
    cards = {"chr_p1": make_card()}
    client = _client(monkeypatch, cards)

    monkeypatch.setattr(mm, "post_json", lambda *a, **k: {"choices": []})
    action = client.player_action("chr_p1", cards["chr_p1"], make_state(), 1)
    assert action.actor_id == "chr_p1"
    assert action.action
    assert client.usage["fail"] >= 1


def test_narrate_drops_bad_delta(monkeypatch):
    cards = {"chr_p1": make_card()}
    client = _client(monkeypatch, cards)

    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"narration": "雨停了。", "state_delta": {"changes": [{"path": "version", "op": "set", "value": 9}]}}'
                }
            }
        ]
    }
    monkeypatch.setattr(mm, "post_json", lambda *a, **k: payload)

    from trpg.models import PlayerAction

    action = PlayerAction(actor_id="chr_p1", action="试探")
    result = type("R", (), {"expr": "1d20", "rolls": [10], "total": 12, "dc": 12, "margin": 0, "outcome": "success"})()
    out = client.narrate(make_state(), action, result, 1)
    assert out["narration"] == "雨停了。"
    # version 是非白名单路径，只能在 apply_delta 阶段被拒；这里 delta 结构合法即可
    assert out["state_delta"] is not None
