"""Shared pytest fixtures.

Force `backend=fake` for every test so the suite is network-free and
needs no API keys. The workflow-sandbox agent path short-circuits on
`backend == "fake"`: the tool returns stub chunks; the verifier returns a
canned on-topic decision; `test_agent.py` plugs in a
`FakeMessagesListChatModel` queued with the AIMessages it wants to
exercise.
"""

from __future__ import annotations

import importlib
from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def _force_fake_backend(monkeypatch):
    """Replace `settings.llm.backend` with 'fake' in every module that
    imported `from src.config import settings`. Frozen dataclasses can't
    be mutated, so we build a new Settings and patch each reference.
    """
    from src import config as cfg

    fake_llm = replace(cfg.settings.llm, backend="fake")
    fake_settings = replace(cfg.settings, llm=fake_llm)
    monkeypatch.setattr("src.config.settings", fake_settings)

    for modpath in (
        "src.graph.client",
        "src.graph.agent",
        "src.graph.tools",
        "src.graph.verifier",
    ):
        try:
            mod = importlib.import_module(modpath)
        except ImportError:
            continue
        if hasattr(mod, "settings"):
            monkeypatch.setattr(f"{modpath}.settings", fake_settings)

    return fake_settings


@pytest.fixture
def stub_chunks():
    """The 3 hardcoded retrieve() chunks — re-export for direct tests."""
    from src.graph.tools import _STUB_CHUNKS

    return list(_STUB_CHUNKS)


@pytest.fixture
def make_agent_state():
    """Build an AgentState with sane defaults; override via kwargs."""
    from src.graph.state import initial_agent_state

    def _factory(**overrides):
        base = {
            "grade": 7,
            "subject": "islamic_studies",
            "user_query": "What is photosynthesis?",
        }
        base.update(overrides)
        return initial_agent_state(**base)

    return _factory
