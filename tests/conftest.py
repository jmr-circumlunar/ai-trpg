import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.models import CharacterCard, GameState  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from trpg.testing import make_card, make_state  # noqa: E402


@pytest.fixture(scope="session")
def rules() -> Rules:
    return Rules(ROOT)


@pytest.fixture
def card() -> CharacterCard:
    return make_card()


@pytest.fixture
def state(card: CharacterCard) -> GameState:
    return make_state(card)
