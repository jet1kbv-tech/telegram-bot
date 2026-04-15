from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.app import build_app


def test_build_app_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST_TOKEN")

    app = build_app()

    assert app is not None
