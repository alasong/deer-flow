from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

from deerflow.tasks.gate import GateStatus, HumanGate, transition_approve, transition_reject

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure data-shaping
# ---------------------------------------------------------------------------


def serialize_gates(gates: list[HumanGate]) -> dict[str, Any]:
    return {
        "gates": [g.to_dict() for g in gates],
        "count": len(gates),
    }


def deserialize_gates(data: dict[str, Any]) -> list[HumanGate]:
    return [HumanGate.from_dict(g) for g in data.get("gates", [])]


# ---------------------------------------------------------------------------
# Gate Store
# ---------------------------------------------------------------------------


class HumanGateStore:
    """Thread-safe in-memory gate store with optional file persistence."""

    def __init__(self, file_path: str | Path | None = None) -> None:
        self._gates: dict[str, HumanGate] = {}
        self._lock = Lock()
        self._file_path = Path(file_path) if file_path else None
        if self._file_path:
            self._load()

    def create(self, gate: HumanGate) -> HumanGate:
        with self._lock:
            self._gates[gate.gate_id] = gate
            self._save()
        return gate

    def get(self, gate_id: str) -> HumanGate | None:
        with self._lock:
            return self._gates.get(gate_id)

    def list(self, status: GateStatus | None = None, task_id: str | None = None) -> list[HumanGate]:
        with self._lock:
            gates = list(self._gates.values())
        if status is not None:
            gates = [g for g in gates if g.status == status]
        if task_id is not None:
            gates = [g for g in gates if g.task_id == task_id]
        return sorted(gates, key=lambda g: g.created_at, reverse=True)

    def approve(self, gate_id: str, approved_by: str = "", human_input: str = "") -> HumanGate:
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                raise KeyError(f"Gate {gate_id!r} not found")
            gate = transition_approve(gate, approved_by, human_input)
            self._gates[gate_id] = gate
            self._save()
            return gate

    def reject(self, gate_id: str, approved_by: str = "", human_input: str = "") -> HumanGate:
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                raise KeyError(f"Gate {gate_id!r} not found")
            gate = transition_reject(gate, approved_by, human_input)
            self._gates[gate_id] = gate
            self._save()
            return gate

    def find_pending_by_task(self, task_id: str) -> list[HumanGate]:
        return self.list(status=GateStatus.pending, task_id=task_id)

    def _load(self) -> None:
        if not self._file_path or not self._file_path.exists():
            return
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            gates = deserialize_gates(data)
            self._gates = {g.gate_id: g for g in gates}
            logger.info("Loaded %d gates from %s", len(gates), self._file_path)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load gates: %s", exc)

    def _save(self) -> None:
        if not self._file_path:
            return
        data = serialize_gates(list(self._gates.values()))
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
