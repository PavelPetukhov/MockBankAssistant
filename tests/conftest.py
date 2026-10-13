"""Shared fixtures: every test gets its own throwaway bank DB (and RAG index dir)."""

import warnings

warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\]")

import pytest  # noqa: E402

import config  # noqa: E402
from mockbank.state import reset_state  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mockbank.db")
    monkeypatch.setattr(config, "MOCKBANK_DB_PATH", db_path)
    monkeypatch.setenv("MOCKBANK_DB_PATH", db_path)  # inherited by MCP server subprocesses
    reset_state()
