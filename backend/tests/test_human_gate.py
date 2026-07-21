"""Tests for HumanGate model and HumanGateStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deerflow.tasks.gate import (
    GateStatus,
    HumanGate,
    transition_approve,
    transition_reject,
)
from deerflow.tasks.gate_store import HumanGateStore, serialize_gates, deserialize_gates


class TestHumanGate:
    def test_create_gate(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0, description="Review required")
        assert gate.status == GateStatus.pending
        assert not gate.is_resolved
        assert gate.created_at

    def test_approved_gate(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        gate = transition_approve(gate, approved_by="human", human_input="Looks good")
        assert gate.status == GateStatus.approved
        assert gate.is_resolved
        assert gate.resolved_at
        assert gate.approved_by == "human"
        assert gate.human_input == "Looks good"

    def test_rejected_gate(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        gate = transition_reject(gate, approved_by="human", human_input="Not ready")
        assert gate.status == GateStatus.rejected
        assert gate.is_resolved
        assert gate.human_input == "Not ready"

    def test_cannot_approve_already_approved(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        transition_approve(gate)
        with pytest.raises(ValueError):
            transition_approve(gate)

    def test_cannot_reject_already_rejected(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        transition_reject(gate)
        with pytest.raises(ValueError):
            transition_reject(gate)

    def test_to_dict(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=1, description="Check")
        d = gate.to_dict()
        assert d["gate_id"] == "g1"
        assert d["task_id"] == "t1"
        assert d["step_index"] == 1
        assert d["status"] == "pending"

    def test_to_dict_resolved(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        transition_approve(gate, approved_by="admin", human_input="ok")
        d = gate.to_dict()
        assert d["status"] == "approved"
        assert d["approved_by"] == "admin"
        assert d["human_input"] == "ok"
        assert "resolved_at" in d

    def test_from_dict(self):
        gate = HumanGate.from_dict(
            {
                "gate_id": "g1",
                "task_id": "t1",
                "step_index": 0,
                "description": "Review",
                "status": "approved",
            }
        )
        assert gate.gate_id == "g1"
        assert gate.status == GateStatus.approved

    def test_status_coercion(self):
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0, status="pending")
        assert isinstance(gate.status, GateStatus)
        assert gate.status == GateStatus.pending


class TestHumanGateStore:
    def test_create_and_get(self):
        store = HumanGateStore()
        gate = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        store.create(gate)
        assert store.get("g1") is gate

    def test_get_not_found(self):
        store = HumanGateStore()
        assert store.get("nonexistent") is None

    def test_list_all(self):
        store = HumanGateStore()
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        store.create(HumanGate(gate_id="g2", task_id="t2", step_index=0))
        assert len(store.list()) == 2

    def test_list_by_status(self):
        store = HumanGateStore()
        g1 = HumanGate(gate_id="g1", task_id="t1", step_index=0)
        g2 = HumanGate(gate_id="g2", task_id="t2", step_index=0)
        store.create(g1)
        store.create(g2)
        transition_approve(g2)
        pending = store.list(status=GateStatus.pending)
        assert len(pending) == 1
        assert pending[0].gate_id == "g1"

    def test_list_by_task_id(self):
        store = HumanGateStore()
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        store.create(HumanGate(gate_id="g2", task_id="t1", step_index=1))
        store.create(HumanGate(gate_id="g3", task_id="t2", step_index=0))
        assert len(store.list(task_id="t1")) == 2
        assert len(store.list(task_id="t2")) == 1

    def test_approve(self):
        store = HumanGateStore()
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate = store.approve("g1", "admin", "ok")
        assert gate.status == GateStatus.approved
        assert store.get("g1").status == GateStatus.approved

    def test_approve_not_found(self):
        store = HumanGateStore()
        with pytest.raises(KeyError):
            store.approve("nonexistent")

    def test_reject(self):
        store = HumanGateStore()
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        gate = store.reject("g1", "admin", "no")
        assert gate.status == GateStatus.rejected

    def test_find_pending_by_task(self):
        store = HumanGateStore()
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        store.create(HumanGate(gate_id="g2", task_id="t1", step_index=1))
        store.approve("g1")
        pending = store.find_pending_by_task("t1")
        assert len(pending) == 1
        assert pending[0].gate_id == "g2"

    def test_file_persistence(self, tmp_path: Path):
        file_path = tmp_path / "gates.json"
        store = HumanGateStore(file_path=file_path)
        store.create(HumanGate(gate_id="g1", task_id="t1", step_index=0))
        store.create(HumanGate(gate_id="g2", task_id="t2", step_index=1, description="Check"))

        # New store from same file should load gates
        store2 = HumanGateStore(file_path=file_path)
        assert store2.get("g1") is not None
        assert store2.get("g2") is not None
        assert store2.get("g2").description == "Check"

    def test_serialize_deserialize(self):
        gates = [
            HumanGate(gate_id="g1", task_id="t1", step_index=0),
            HumanGate(gate_id="g2", task_id="t2", step_index=1),
        ]
        data = serialize_gates(gates)
        assert data["count"] == 2
        restored = deserialize_gates(data)
        assert len(restored) == 2
        assert restored[0].gate_id == "g1"

    def test_empty_store_list(self):
        store = HumanGateStore()
        assert store.list() == []
