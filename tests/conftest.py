import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trpg.models import CharacterCard, GameState  # noqa: E402
from trpg.rules.loader import Rules  # noqa: E402
from support.builders import make_card, make_state  # noqa: E402


@pytest.fixture(scope="session")
def rules() -> Rules:
    return Rules(ROOT)


@pytest.fixture
def card() -> CharacterCard:
    return make_card()


@pytest.fixture
def state(card: CharacterCard) -> GameState:
    return make_state(card)


@pytest.fixture(autouse=True)
def _disable_auth_for_tests():
    """默认关掉登录：功能测试不该被认证拦住。

    认证本身由 tests/test_auth.py 显式打开来测。
    """
    from trpg.api.app import app as fastapi_app

    previous = fastapi_app.state.auth_required
    fastapi_app.state.auth_required = False
    yield
    fastapi_app.state.auth_required = previous
