"""Tests for the Pipeline data models.

Covers PipelineRun / PipelineStepRun status transitions,
default field values, and enum members.
"""

from __future__ import annotations

import pytest

from deerflow.pipeline.models import (
    PipelineRun,
    PipelineStatus,
    PipelineStepRun,
    PipelineStepRunStatus,
    validate_status_transition,
    validate_step_run_transition,
)


class TestPipelineStatusEnum:
    """PipelineStatus enum member values."""

    def test_values(self):
        assert PipelineStatus.active.value == "active"
        assert PipelineStatus.completed.value == "completed"
        assert PipelineStatus.failed.value == "failed"
        assert PipelineStatus.cancelled.value == "cancelled"

    def test_members_count(self):
        assert len(PipelineStatus) == 4


class TestPipelineStepRunStatusEnum:
    """PipelineStepRunStatus enum member values."""

    def test_values(self):
        assert PipelineStepRunStatus.pending.value == "pending"
        assert PipelineStepRunStatus.running.value == "running"
        assert PipelineStepRunStatus.completed.value == "completed"
        assert PipelineStepRunStatus.failed.value == "failed"
        assert PipelineStepRunStatus.skipped.value == "skipped"

    def test_members_count(self):
        assert len(PipelineStepRunStatus) == 5


class TestPipelineStepRun:
    """PipelineStepRun model defaults and transitions."""

    def test_default_status_is_pending(self):
        step = PipelineStepRun(
            id="s1", step_id="step_a", agent_name="agent_a"
        )
        assert step.status == PipelineStepRunStatus.pending

    def test_default_manifest_path_is_none(self):
        step = PipelineStepRun(
            id="s1", step_id="step_a", agent_name="agent_a"
        )
        assert step.manifest_path is None

    def test_assign_running_then_completed(self):
        step = PipelineStepRun(
            id="s1", step_id="step_a", agent_name="agent_a"
        )
        step.status = PipelineStepRunStatus.running
        assert step.status == PipelineStepRunStatus.running
        step.status = PipelineStepRunStatus.completed
        assert step.status == PipelineStepRunStatus.completed

    def test_assign_failed(self):
        step = PipelineStepRun(
            id="s1", step_id="step_a", agent_name="agent_a"
        )
        step.status = PipelineStepRunStatus.running
        step.status = PipelineStepRunStatus.failed
        assert step.status == PipelineStepRunStatus.failed

    def test_assign_skipped(self):
        step = PipelineStepRun(
            id="s1", step_id="step_a", agent_name="agent_a"
        )
        step.status = PipelineStepRunStatus.skipped
        assert step.status == PipelineStepRunStatus.skipped


class TestValidateStepRunTransition:
    """validate_step_run_transition — allowed / disallowed moves."""

    def test_pending_to_running(self):
        assert validate_step_run_transition(
            PipelineStepRunStatus.pending, PipelineStepRunStatus.running
        ) is True

    def test_pending_to_skipped(self):
        assert validate_step_run_transition(
            PipelineStepRunStatus.pending, PipelineStepRunStatus.skipped
        ) is True

    def test_pending_to_completed_invalid(self):
        assert validate_step_run_transition(
            PipelineStepRunStatus.pending, PipelineStepRunStatus.completed
        ) is False

    def test_running_to_completed(self):
        assert validate_step_run_transition(
            PipelineStepRunStatus.running, PipelineStepRunStatus.completed
        ) is True

    def test_running_to_failed(self):
        assert validate_step_run_transition(
            PipelineStepRunStatus.running, PipelineStepRunStatus.failed
        ) is True

    def test_completed_is_terminal(self):
        for target in PipelineStepRunStatus:
            assert (
                validate_step_run_transition(
                    PipelineStepRunStatus.completed, target
                )
                is False
            )

    def test_failed_is_terminal(self):
        for target in PipelineStepRunStatus:
            assert (
                validate_step_run_transition(
                    PipelineStepRunStatus.failed, target
                )
                is False
            )

    def test_skipped_is_terminal(self):
        for target in PipelineStepRunStatus:
            assert (
                validate_step_run_transition(
                    PipelineStepRunStatus.skipped, target
                )
                is False
            )


class TestPipelineRun:
    """PipelineRun model defaults and state transitions."""

    def test_default_status_is_active(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        assert run.status == PipelineStatus.active

    def test_default_current_step_index_is_zero(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        assert run.current_step_index == 0

    def test_with_multiple_steps(self):
        steps = [
            PipelineStepRun(
                id="s1", step_id="step_a", agent_name="agent_a"
            ),
            PipelineStepRun(
                id="s2", step_id="step_b", agent_name="agent_b"
            ),
        ]
        run = PipelineRun(
            id="run_1",
            definition_id="def_1",
            thread_id="thread_1",
            steps=steps,
        )
        assert len(run.steps) == 2
        assert run.steps[0].id == "s1"
        assert run.steps[1].id == "s2"

    def test_status_activated(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        run.status = PipelineStatus.active
        assert run.status == PipelineStatus.active

    def test_status_completed(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        run.status = PipelineStatus.completed
        assert run.status == PipelineStatus.completed

    def test_status_failed(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        run.status = PipelineStatus.failed
        assert run.status == PipelineStatus.failed

    def test_status_cancelled(self):
        run = PipelineRun(
            id="run_1", definition_id="def_1", thread_id="thread_1"
        )
        run.status = PipelineStatus.cancelled
        assert run.status == PipelineStatus.cancelled


class TestValidateStatusTransition:
    """validate_status_transition — allowed / disallowed moves."""

    def test_active_to_completed(self):
        assert (
            validate_status_transition(
                PipelineStatus.active, PipelineStatus.completed
            )
            is True
        )

    def test_active_to_failed(self):
        assert (
            validate_status_transition(
                PipelineStatus.active, PipelineStatus.failed
            )
            is True
        )

    def test_active_to_cancelled(self):
        assert (
            validate_status_transition(
                PipelineStatus.active, PipelineStatus.cancelled
            )
            is True
        )

    def test_completed_to_active_invalid(self):
        assert (
            validate_status_transition(
                PipelineStatus.completed, PipelineStatus.active
            )
            is False
        )

    def test_failed_to_active_invalid(self):
        assert (
            validate_status_transition(
                PipelineStatus.failed, PipelineStatus.active
            )
            is False
        )

    def test_cancelled_to_active_invalid(self):
        assert (
            validate_status_transition(
                PipelineStatus.cancelled, PipelineStatus.active
            )
            is False
        )

    def test_completed_to_failed_invalid(self):
        assert (
            validate_status_transition(
                PipelineStatus.completed, PipelineStatus.failed
            )
            is False
        )

    def test_failed_to_completed_invalid(self):
        assert (
            validate_status_transition(
                PipelineStatus.failed, PipelineStatus.completed
            )
            is False
        )
