"""Tests for agent model and registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deerflow.agents.model import Agent, AgentAccessLevel, AgentStatus
from deerflow.agents.registry import (
    AgentRegistry,
    agent_matches_capability,
    deserialize_registry,
    serialize_registry,
)


class TestAgentModel:
    def test_create_defaults(self):
        agent = Agent(agent_id="agent.test", name="Test Agent")
        assert agent.status == AgentStatus.idle
        assert agent.access_level == AgentAccessLevel.exec
        assert agent.capabilities == []
        assert agent.created_at
        assert agent.updated_at

    def test_roundtrip_dict(self):
        agent = Agent(
            agent_id="agent.test",
            name="Test Agent",
            capabilities=["dev.code", "review.code"],
            status=AgentStatus.active,
            access_level=AgentAccessLevel.mutate,
            skills=["pdf", "pff"],
            metadata={"key": "value"},
        )
        d = agent.to_dict()
        restored = Agent.from_dict(d)
        assert restored.agent_id == agent.agent_id
        assert restored.name == agent.name
        assert restored.capabilities == agent.capabilities
        assert restored.status == agent.status
        assert restored.access_level == agent.access_level
        assert restored.skills == agent.skills
        assert restored.metadata == agent.metadata

    def test_from_dict_minimal(self):
        agent = Agent.from_dict({"agent_id": "agent.x", "name": "X"})
        assert agent.agent_id == "agent.x"
        assert agent.status == AgentStatus.idle

    def test_auto_timestamps(self):
        agent = Agent(agent_id="agent.ts", name="TS")
        assert agent.created_at
        assert agent.updated_at


class TestCapabilityMatching:
    def test_prefix_match(self):
        agent = Agent(
            agent_id="agent.dev",
            name="Dev",
            capabilities=["dev.code", "dev.ops"],
        )
        assert agent_matches_capability(agent, "dev.code")
        assert agent_matches_capability(agent, "dev")
        assert not agent_matches_capability(agent, "review")

    def test_empty_capabilities(self):
        agent = Agent(agent_id="agent.empty", name="Empty")
        assert not agent_matches_capability(agent, "anything")


class TestSerialize:
    def test_roundtrip(self):
        agents = [
            Agent(agent_id="a.1", name="A1", capabilities=["x"]),
            Agent(agent_id="a.2", name="A2", capabilities=["y"]),
        ]
        data = serialize_registry(agents)
        assert data["count"] == 2
        restored = deserialize_registry(data)
        assert len(restored) == 2
        assert restored[0].agent_id == "a.1"
        assert restored[1].name == "A2"

    def test_empty_list(self):
        assert deserialize_registry({"agents": [], "count": 0}) == []


class TestAgentRegistry:
    def test_register_and_get(self):
        reg = AgentRegistry()
        agent = Agent(agent_id="agent.foo", name="Foo")
        reg.register(agent)
        assert reg.get("agent.foo") is agent
        assert reg.get("nonexistent") is None

    def test_unregister(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="agent.foo", name="Foo"))
        assert reg.unregister("agent.foo")
        assert reg.get("agent.foo") is None

    def test_unregister_nonexistent(self):
        reg = AgentRegistry()
        assert not reg.unregister("nope")

    def test_list_with_status_filter(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="a.1", name="A1", status=AgentStatus.idle))
        reg.register(Agent(agent_id="a.2", name="A2", status=AgentStatus.active))
        assert len(reg.list()) == 2
        assert len(reg.list(status=AgentStatus.idle)) == 1
        assert len(reg.list(status=AgentStatus.active)) == 1
        assert len(reg.list(status=AgentStatus.paused)) == 0

    def test_update_status(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="agent.foo", name="Foo"))
        updated = reg.update_status("agent.foo", AgentStatus.active)
        assert updated is not None
        assert updated.status == AgentStatus.active
        assert reg.get("agent.foo").status == AgentStatus.active

    def test_update_status_nonexistent(self):
        reg = AgentRegistry()
        assert reg.update_status("nope", AgentStatus.active) is None

    def test_find_by_capability(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="a.dev", name="Dev", capabilities=["dev.code"]))
        reg.register(Agent(agent_id="a.review", name="Review", capabilities=["review.code"]))
        reg.register(Agent(agent_id="a.both", name="Both", capabilities=["dev.code", "review.code"]))
        assert len(reg.find_by_capability("dev")) == 2
        assert len(reg.find_by_capability("review")) == 2
        assert len(reg.find_by_capability("devops")) == 0

    def test_find_by_capability_status_filter(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="a.active", name="Active", capabilities=["dev"], status=AgentStatus.active))
        reg.register(Agent(agent_id="a.idle", name="Idle", capabilities=["dev"], status=AgentStatus.idle))
        assert len(reg.find_by_capability("dev", status=AgentStatus.idle)) == 1
        assert len(reg.find_by_capability("dev")) == 1  # default filter: idle

    def test_file_persistence(self, tmp_path: Path):
        file_path = tmp_path / "agents.json"
        reg = AgentRegistry(file_path=file_path)
        agent = Agent(agent_id="agent.persist", name="Persist")
        reg.register(agent)

        # New registry loads from same file
        reg2 = AgentRegistry(file_path=file_path)
        loaded = reg2.get("agent.persist")
        assert loaded is not None
        assert loaded.name == "Persist"

    def test_no_file_doesnt_crash(self):
        reg = AgentRegistry()
        reg.register(Agent(agent_id="a.1", name="A1"))
        assert reg.get("a.1").name == "A1"

    def test_load_missing_file(self, tmp_path: Path):
        reg = AgentRegistry(file_path=tmp_path / "nonexistent.json")
        assert reg.list() == []
