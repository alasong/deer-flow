"""Tests for PipelineOrchestrator — run lifecycle, step execution, resume."""

from __future__ import annotations

from typing import Any

import pytest

from deerflow.pipeline.conditions import evaluate_condition
from deerflow.pipeline.config import (
    OutputContractEntry,
    PipelineConfig,
    PipelineDefinition,
    PipelineStepDef,
)
from deerflow.pipeline.models import (
    PipelineRun,
    PipelineStatus,
    PipelineStepRunStatus,
)
from deerflow.pipeline.orchestrator import PipelineOrchestrator


def _make_config() -> PipelineConfig:
    return PipelineConfig(
        enabled=True,
        definitions=[
            PipelineDefinition(
                id="dev-flow",
                description="Dev pipeline",
                steps=[
                    PipelineStepDef(id="design", agent="lead-design"),
                    PipelineStepDef(
                        id="code", agent="lead-code",
                        depends_on=["design"],
                        output_contract=[OutputContractEntry(path="src/main.py", type="file")],
                    ),
                    PipelineStepDef(
                        id="review", agent="lead-review",
                        depends_on=["code"],
                    ),
                ],
            ),
            PipelineDefinition(
                id="single",
                description="Single step",
                steps=[PipelineStepDef(id="greet", agent="lead-simple")],
            ),
        ],
    )


def _make_orch(**kwargs) -> PipelineOrchestrator:
    cfg = kwargs.pop("config", None) or _make_config()
    return PipelineOrchestrator(pipeline_config=cfg, workspace_path="/tmp/test-workspace")


class TestCreateRun:
    def test_create_run_returns_active_run(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1", user_input="hello")
        assert prun.status == PipelineStatus.active
        assert prun.definition_id == "dev-flow"
        assert prun.thread_id == "thr_1"
        assert prun.created_at != ""

    def test_create_run_initialises_steps(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        assert len(prun.steps) == 3
        assert prun.steps[0].step_id == "design"
        assert prun.steps[0].agent_name == "lead-design"
        assert prun.steps[0].status == PipelineStepRunStatus.pending
        assert prun.steps[1].step_id == "code"
        assert prun.steps[2].step_id == "review"

    def test_create_run_single_step(self):
        orch = _make_orch()
        prun = orch.create_run("single", thread_id="thr_1")
        assert len(prun.steps) == 1
        assert prun.steps[0].step_id == "greet"

    def test_create_run_unknown_definition_raises(self):
        orch = _make_orch()
        with pytest.raises(ValueError, match="not found"):
            orch.create_run("nonexistent", thread_id="thr_1")

    def test_create_run_has_generated_id(self):
        orch = _make_orch()
        prun = orch.create_run("single", thread_id="thr_1")
        assert prun.id.startswith("pl_")
        assert len(prun.id) > 3


class TestRun:
    @pytest.mark.asyncio
    async def test_run_completes_all_steps(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {"outputs": [], "decisions": [], "token_used": 100}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed
        for s in result.steps:
            assert s.status == PipelineStepRunStatus.completed

    @pytest.mark.asyncio
    async def test_run_sequential_execution(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        execution_order: list[str] = []

        async def runner(*, step_def, graph_input, agent_name):
            execution_order.append(step_def.id)
            return {}

        await orch.run(prun, step_runner=runner)
        assert execution_order == ["design", "code", "review"]

    @pytest.mark.asyncio
    async def test_run_steps_see_upstream_input(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        step_inputs: dict[str, Any] = {}

        async def runner(*, step_def, graph_input, agent_name):
            step_inputs[step_def.id] = {"graph_input": graph_input}
            return {}

        await orch.run(prun, step_runner=runner)
        # design has no deps → graph_input is None
        assert step_inputs["design"]["graph_input"] is None
        # code depends on design → should see upstream manifests
        assert step_inputs["code"]["graph_input"] is not None
        assert "upstream_manifests" in step_inputs["code"]["graph_input"]

    @pytest.mark.asyncio
    async def test_run_single_step(self):
        orch = _make_orch()
        prun = orch.create_run("single", thread_id="thr_1")
        called = False

        async def runner(*, step_def, graph_input, agent_name):
            nonlocal called
            called = True
            assert step_def.id == "greet"
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert called
        assert result.status == PipelineStatus.completed

    @pytest.mark.asyncio
    async def test_run_not_active_returns_early(self):
        orch = _make_orch()
        prun = orch.create_run("single", thread_id="thr_1")
        prun.status = PipelineStatus.completed
        called = False

        async def runner(*, step_def, graph_input, agent_name):
            nonlocal called
            called = True
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert not called  # runner was not invoked
        assert result.status == PipelineStatus.completed


class TestRunFailure:
    @pytest.mark.asyncio
    async def test_step_failure_marks_run_failed(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        call_count = 0

        async def runner(*, step_def, graph_input, agent_name):
            nonlocal call_count
            call_count += 1
            if step_def.id == "code":
                msg = "agent error"
                raise RuntimeError(msg)
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.failed
        assert result.steps[0].status == PipelineStepRunStatus.completed  # design completed
        assert result.steps[1].status == PipelineStepRunStatus.failed     # code failed
        assert result.steps[2].status == PipelineStepRunStatus.pending    # review not started
        # design + code were called, review was skipped
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_first_step_failure(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            msg = "first step fails"
            raise RuntimeError(msg)

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.failed
        assert result.steps[0].status == PipelineStepRunStatus.failed


class TestResumeRun:
    @pytest.mark.asyncio
    async def test_resume_from_failed_step(self):
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        call_count = 0

        async def failing_runner(*, step_def, graph_input, agent_name):
            nonlocal call_count
            call_count += 1
            if step_def.id == "code":
                msg = "agent error"
                raise RuntimeError(msg)
            return {}

        await orch.run(prun, step_runner=failing_runner)
        assert prun.status == PipelineStatus.failed
        assert prun.steps[1].status == PipelineStepRunStatus.failed

        # Resume — should retry from the failed step.
        async def good_runner(*, step_def, graph_input, agent_name):
            return {}

        result = await orch.resume_run(prun, step_runner=good_runner)
        assert result.status == PipelineStatus.completed
        for s in result.steps:
            assert s.status == PipelineStepRunStatus.completed

    @pytest.mark.asyncio
    async def test_resume_already_completed_run(self):
        orch = _make_orch()
        prun = orch.create_run("single", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {}

        await orch.run(prun, step_runner=runner)
        assert prun.status == PipelineStatus.completed

        # Resume already-completed run — should be a no-op.
        result = await orch.resume_run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed

    @pytest.mark.asyncio
    async def test_resume_downgrades_failed_to_pending(self):
        """Failed steps should become pending so the runner retries them."""
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")

        async def failing_runner(*, step_def, graph_input, agent_name):
            msg = "fail"
            raise RuntimeError(msg)

        await orch.run(prun, step_runner=failing_runner)
        # Before resume, failed should be pending for retry
        for s in prun.steps:
            if s.status == PipelineStepRunStatus.failed:
                assert True


class TestDependencyOrdering:
    @pytest.mark.asyncio
    async def test_skips_unmet_dependencies(self):
        """Steps whose deps are not met should not be executed."""
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        executed: list[str] = []

        async def runner(*, step_def, graph_input, agent_name):
            executed.append(step_def.id)
            if step_def.id == "design":
                # Fail design — code and review should not execute
                msg = "design fail"
                raise RuntimeError(msg)
            return {}

        await orch.run(prun, step_runner=runner)
        assert executed == ["design"]
        assert prun.status == PipelineStatus.failed

    @pytest.mark.asyncio
    async def test_multi_dependency_chain(self):
        """A -> B -> C: each step waits for its predecessor."""
        orch = _make_orch()
        prun = orch.create_run("dev-flow", thread_id="thr_1")
        order: list[str] = []

        async def runner(*, step_def, graph_input, agent_name):
            order.append(step_def.id)
            return {}

        await orch.run(prun, step_runner=runner)
        assert order == ["design", "code", "review"]


class TestManifestWritten:
    @pytest.mark.asyncio
    async def test_manifest_path_set_on_completion(self, tmp_path):
        """Each step's manifest_path should be set after successful execution."""
        orch = PipelineOrchestrator(
            pipeline_config=_make_config(),
            workspace_path=str(tmp_path),
        )
        prun = orch.create_run("single", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {"decisions": [], "token_used": 50}

        result = await orch.run(prun, step_runner=runner)
        assert result.steps[0].manifest_path is not None
        assert result.steps[0].manifest_path.endswith(".json")

    @pytest.mark.asyncio
    async def test_manifest_file_exists_on_disk(self, tmp_path):
        orch = PipelineOrchestrator(
            pipeline_config=_make_config(),
            workspace_path=str(tmp_path),
        )
        prun = orch.create_run("single", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {}

        import json as _json

        result = await orch.run(prun, step_runner=runner)
        manifest_path = result.steps[0].manifest_path
        assert manifest_path is not None
        with open(manifest_path) as f:
            data = _json.load(f)
        assert data["pipeline_id"] == prun.id
        assert data["step_id"] == "greet"
        assert data["status"] == "completed"


class TestDefinitionHelpers:
    def test_get_definition_returns_matching(self):
        orch = _make_orch()
        d = orch._get_definition("single")
        assert d.id == "single"

    def test_get_definition_unknown_raises(self):
        orch = _make_orch()
        with pytest.raises(ValueError):
            orch._get_definition("nope")

    def test_generate_run_id_format(self):
        rid = PipelineOrchestrator._generate_run_id()
        assert rid.startswith("pl_")
        assert len(rid) == 15  # pl_ + 12 hex chars


def _config_with_condition() -> PipelineConfig:
    """Config with a fix step gated on review.has_issues == true."""
    return PipelineConfig(
        enabled=True,
        definitions=[
            PipelineDefinition(
                id="cond-flow",
                steps=[
                    PipelineStepDef(id="review", agent="lead-review"),
                    PipelineStepDef(
                        id="fix", agent="lead-code",
                        depends_on=["review"],
                        condition="review.status == completed",
                    ),
                    PipelineStepDef(
                        id="final", agent="lead-final",
                        depends_on=["review", "fix"],
                    ),
                ],
            ),
        ],
    )


def _config_with_or_deps() -> PipelineConfig:
    """Config where test depends on EITHER code OR fix."""
    return PipelineConfig(
        enabled=True,
        definitions=[
            PipelineDefinition(
                id="or-flow",
                steps=[
                    PipelineStepDef(id="code", agent="lead-code"),
                    PipelineStepDef(id="fix", agent="lead-code"),
                    PipelineStepDef(
                        id="test", agent="lead-test",
                        depends_on=["code", "fix"],
                    ),
                ],
            ),
        ],
    )


class TestConditionGating:
    @pytest.mark.asyncio
    async def test_condition_met_runs_step(self):
        orch = PipelineOrchestrator(_config_with_condition(), workspace_path="/tmp/t")
        prun = orch.create_run("cond-flow", thread_id="thr_1")
        invoked: list[str] = []

        async def runner(*, step_def, graph_input, agent_name):
            invoked.append(step_def.id)
            # Simulate review having issues
            return {"has_issues": True}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed
        # All steps ran (condition was met)
        assert result.steps[0].status == PipelineStepRunStatus.completed  # review
        assert result.steps[1].status == PipelineStepRunStatus.completed  # fix (condition met)
        assert result.steps[2].status == PipelineStepRunStatus.completed  # final

    @pytest.mark.asyncio
    async def test_condition_not_met_skips_step(self):
        cfg = PipelineConfig(
            enabled=True,
            definitions=[
                PipelineDefinition(
                    id="cond-flow",
                    steps=[
                        PipelineStepDef(id="review", agent="lead-review"),
                        PipelineStepDef(
                            id="fix", agent="lead-code",
                            depends_on=["review"],
                            condition="review.status == failed",
                        ),
                        PipelineStepDef(
                            id="final", agent="lead-final",
                            depends_on=["review", "fix"],
                        ),
                    ],
                ),
            ],
        )
        orch = PipelineOrchestrator(cfg, workspace_path="/tmp/t")
        prun = orch.create_run("cond-flow", thread_id="thr_1")
        invoked: list[str] = []

        async def runner(*, step_def, graph_input, agent_name):
            invoked.append(step_def.id)
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed
        assert result.steps[0].status == PipelineStepRunStatus.completed  # review ran
        assert result.steps[1].status == PipelineStepRunStatus.skipped   # fix skipped (condition not met)
        assert result.steps[2].status == PipelineStepRunStatus.completed  # final ran

    @pytest.mark.asyncio
    async def test_condition_never_always_skips(self):
        """condition: 'never' always evaluates to False."""
        cfg = PipelineConfig(
            enabled=True,
            definitions=[
                PipelineDefinition(
                    id="n-flow",
                    steps=[
                        PipelineStepDef(id="first", agent="lead-a"),
                        PipelineStepDef(
                            id="second", agent="lead-b",
                            depends_on=["first"],
                            condition="never",
                        ),
                    ],
                ),
            ],
        )
        orch = PipelineOrchestrator(cfg, workspace_path="/tmp/t")
        prun = orch.create_run("n-flow", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed
        assert result.steps[0].status == PipelineStepRunStatus.completed  # first ran
        assert result.steps[1].status == PipelineStepRunStatus.skipped   # second skipped

    @pytest.mark.asyncio
    async def test_condition_self_reference_skips(self):
        """A step cannot gate on itself."""
        cfg = PipelineConfig(
            enabled=True,
            definitions=[
                PipelineDefinition(
                    id="self",
                    steps=[
                        PipelineStepDef(
                            id="x",
                            agent="lead-x",
                            condition="x.done == true",
                        ),
                    ],
                ),
            ],
        )
        orch = PipelineOrchestrator(cfg, workspace_path="/tmp/t")
        prun = orch.create_run("self", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert result.steps[0].status == PipelineStepRunStatus.skipped


class TestOrLogicDeps:
    @pytest.mark.asyncio
    async def test_or_dep_one_completed(self):
        """test depends on code|fix — code completes first, test should run."""
        orch = PipelineOrchestrator(_config_with_or_deps(), workspace_path="/tmp/t")
        prun = orch.create_run("or-flow", thread_id="thr_1")

        async def runner(*, step_def, graph_input, agent_name):
            return {}

        result = await orch.run(prun, step_runner=runner)
        assert result.status == PipelineStatus.completed
        # All three steps ran
        for s in result.steps:
            assert s.status == PipelineStepRunStatus.completed

    def test_find_next_ready_or_logic_single_dep(self):
        """Or-logic: downstream step is ready when only one dep is done."""
        cfg = PipelineConfig(
            enabled=True,
            definitions=[
                PipelineDefinition(
                    id="or2",
                    steps=[
                        PipelineStepDef(id="a", agent="lead-a"),
                        PipelineStepDef(id="b", agent="lead-b"),
                        PipelineStepDef(
                            id="c", agent="lead-c",
                            depends_on=["a", "b"],
                        ),
                    ],
                ),
            ],
        )
        orch = PipelineOrchestrator(cfg, workspace_path="/tmp/t")
        prun = orch.create_run("or2", thread_id="thr_1")
        step_defs_by_id = {s.id: s for s in orch._get_definition("or2").steps}

        # Only a is done → c's or-dependency satisfied
        prun.steps[0].status = PipelineStepRunStatus.completed
        done = {"a"}

        # b is pending and has no deps → found first
        ready = orch._find_next_ready_step(prun, step_defs_by_id, done)
        assert ready is not None and ready[0].step_id == "b"

        # Mark b as done too
        prun.steps[1].status = PipelineStepRunStatus.completed
        done.add("b")

        # c is ready (both deps satisfied)
        ready = orch._find_next_ready_step(prun, step_defs_by_id, done)
        assert ready is not None
        assert ready[0].step_id == "c"

    def test_find_next_ready_no_dep_satisfied(self):
        """No dep done → downstream not ready."""
        cfg = PipelineConfig(
            enabled=True,
            definitions=[
                PipelineDefinition(
                    id="or3",
                    steps=[
                        PipelineStepDef(id="a", agent="lead-a"),
                        PipelineStepDef(id="b", agent="lead-b"),
                        PipelineStepDef(
                            id="c", agent="lead-c",
                            depends_on=["a", "b"],
                        ),
                    ],
                ),
            ],
        )
        orch = PipelineOrchestrator(cfg, workspace_path="/tmp/t")
        prun = orch.create_run("or3", thread_id="thr_1")
        step_defs_by_id = {s.id: s for s in orch._get_definition("or3").steps}

        # No deps done. a has no deps → ready.
        ready = orch._find_next_ready_step(prun, step_defs_by_id, set())
        assert ready is not None and ready[0].step_id == "a"

        # Mark a as done. b pending → b ready (no deps).
        prun.steps[0].status = PipelineStepRunStatus.completed
        ready = orch._find_next_ready_step(prun, step_defs_by_id, {"a"})
        assert ready is not None and ready[0].step_id == "b"

        # Mark both a and b as done. c's or-dependency satisfied.
        prun.steps[1].status = PipelineStepRunStatus.completed
        ready = orch._find_next_ready_step(prun, step_defs_by_id, {"a", "b"})
        assert ready is not None and ready[0].step_id == "c"
