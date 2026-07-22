"""Tests for ContextOffloadMiddleware."""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from deerflow.agents.middlewares.context_offload_middleware import ContextOffloadMiddleware
from deerflow.config.offload_config import OffloadConfig


def _make_state(messages, **overrides):
    """Build a minimal thread state dict."""
    state = {
        "messages": messages,
        "goal": None,
        "delegations": [],
        "skill_context": [],
    }
    state.update(overrides)
    return state


def _make_runtime(thread_id="test-tid-1"):
    runtime = SimpleNamespace()
    runtime.context = {"thread_id": thread_id}
    return runtime


class TestContextOffloadMiddleware:
    """Tests for ContextOffloadMiddleware."""

    def test_offload_triggers_at_threshold(self, monkeypatch):
        """Token count >= threshold saves offload file and trims messages."""
        messages = [HumanMessage(content=f"msg_{i:04d}") for i in range(20)]
        state = _make_state(messages)
        runtime = _make_runtime()

        mw = ContextOffloadMiddleware(offload_threshold=100, messages_to_keep=5)
        monkeypatch.setattr(mw, "_count_tokens", lambda msgs: 150)
        monkeypatch.setattr(mw, "_write_offload", lambda dump, tid: f"/tmp/offloads/{tid}/off.json")

        result = mw.before_model(state, runtime)

        assert result is not None
        assert "offload_path" in result
        assert "offload_summary" in result
        assert isinstance(result["offload_summary"], str)
        assert len(result["offload_summary"]) > 0

        # messages should contain RemoveMessage + the kept tail
        assert "messages" in result
        # First message should be RemoveMessage
        assert isinstance(result["messages"][0], RemoveMessage)
        # Count real messages
        real_msgs = [m for m in result["messages"] if not isinstance(m, RemoveMessage)]
        assert len(real_msgs) == 5  # messages_to_keep
        # The kept messages should be the last 5 from input
        for i in range(5):
            assert real_msgs[i].content == messages[-5 + i].content

    def test_offload_below_threshold(self, monkeypatch):
        """Token count below threshold returns None."""
        messages = [HumanMessage(content="hello")]
        state = _make_state(messages)
        runtime = _make_runtime()

        mw = ContextOffloadMiddleware(offload_threshold=100)
        monkeypatch.setattr(mw, "_count_tokens", lambda msgs: 10)

        result = mw.before_model(state, runtime)
        assert result is None

    def test_offload_content(self, tmp_path, monkeypatch):
        """Offload dump contains messages, goal, delegations, timestamp."""
        messages = [HumanMessage(content="Q?"), AIMessage(content="A!")]
        goal = {"objective": "test objective", "status": "active"}
        delegations = [{"id": "d1", "description": "task1", "status": "completed"}]
        state = _make_state(messages, goal=goal, delegations=delegations)
        runtime = _make_runtime(thread_id="cid")

        mw = ContextOffloadMiddleware(offload_threshold=50)
        monkeypatch.setattr(mw, "_count_tokens", lambda msgs: 100)

        written = {"thread_id": None, "dump": None}

        def fake_write(dump, thread_id):
            written["thread_id"] = thread_id
            written["dump"] = dump
            dest = tmp_path / thread_id / "off.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "w") as f:
                json.dump(dump, f, indent=2, default=str)
            return str(dest)

        monkeypatch.setattr(mw, "_write_offload", fake_write)

        mw.before_model(state, runtime)

        assert written["thread_id"] == "cid"
        assert written["dump"] is not None
        dump = written["dump"]
        assert "messages" in dump
        assert len(dump["messages"]) >= 2
        assert "goal" in dump
        assert dump["goal"]["objective"] == "test objective"
        assert "timestamp" in dump
        assert isinstance(dump["timestamp"], (int, float))

    def test_config(self):
        """Threshold and messages_to_keep are configurable."""
        mw1 = ContextOffloadMiddleware(offload_threshold=50_000, messages_to_keep=30)
        assert mw1.threshold == 50_000
        assert mw1.messages_to_keep == 30

        mw_default = ContextOffloadMiddleware()
        assert mw_default.threshold == ContextOffloadMiddleware.DEFAULT_OFFLOAD_THRESHOLD
        assert mw_default.threshold == 150_000
        assert mw_default.messages_to_keep == 10

    def test_from_config(self):
        """from_config classmethod creates middleware from OffloadConfig."""
        config = OffloadConfig(threshold=99_000, messages_to_keep=25, offload_dir="/tmp/offloads")
        mw = ContextOffloadMiddleware.from_config(config)
        assert mw.threshold == 99_000
        assert mw.messages_to_keep == 25
        assert mw.offload_dir == "/tmp/offloads"
