# Shared fixtures. Reset spine between tests so registry state never leaks.
import os
from pathlib import Path

import pytest

from transcript_pipeline.runtime import _reset_for_tests, boot


@pytest.fixture(autouse=True)
def _fresh_spine(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_OUT_DIR", str(tmp_path / "out"))
    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def core(_fresh_spine):
    return boot()
