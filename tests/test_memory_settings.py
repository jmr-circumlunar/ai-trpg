"""记忆参数外置：YAML 改了要真的生效。"""

from pathlib import Path

from trpg.memory import MemorySettings

ROOT = Path(__file__).resolve().parents[1]


def test_load_defaults_from_repo_config():
    s = MemorySettings.load(ROOT)
    assert s.recent_turns >= 1
    assert s.max_steps >= 1
    assert s.top_k >= 1


def test_load_custom_file(tmp_path):
    cfg = tmp_path / "memory.yaml"
    cfg.write_text(
        "short_term:\n  recent_turns: 5\n  summary_every: 2\n"
        "recall:\n  top_k: 8\n  rrf_k: 10\n"
        "agent:\n  max_steps: 1\n",
        encoding="utf-8",
    )
    s = MemorySettings.load(tmp_path, cfg)
    assert s.recent_turns == 5
    assert s.summary_every == 2
    assert s.top_k == 8
    assert s.rrf_k == 10
    assert s.max_steps == 1


def test_unknown_keys_ignored(tmp_path):
    cfg = tmp_path / "memory.yaml"
    cfg.write_text("recall:\n  top_k: 3\n  nonsense: 99\n", encoding="utf-8")
    s = MemorySettings.load(tmp_path, cfg)
    assert s.top_k == 3


def test_missing_file_falls_back(tmp_path):
    s = MemorySettings.load(tmp_path)
    assert s.recent_turns == 3  # 默认值
