"""Pipeline Orchestrator — cross-Lead-Agent DAG execution engine.

Drives a multi-step pipeline by calling ``run_agent()`` for each step,
passing intermediate outputs via workspace manifests.
Supports ``depends_on`` with or-logic and ``condition`` gating.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from deerflow.pipeline.conditions import detect_self_reference, evaluate_condition
from deerflow.pipeline.config import PipelineConfig, PipelineStepDef
from deerflow.pipeline.manifest import ManifestManager
from deerflow.pipeline.models import (
    PipelineRun,
    PipelineStatus,
    PipelineStepRun,
    PipelineStepRunStatus,
    validate_status_transition,
    validate_step_run_transition,
)

logger = logging.getLogger(__name__)


class StepRunner(Protocol):
    """Protocol for executing one pipeline step.

    In production this wraps ``run_agent()``; in tests it is a lightweight
    stub that simulates agent output.
    """

    async def __call__(
        self,
        *,
        step_def: PipelineStepDef,
        graph_input: dict[str, Any] | None,
        agent_name: str,
    ) -> dict[str, Any]:
        ...


class PipelineOrchestrator:
    """Orchestrates multi-step pipelines across Lead Agents.

    Usage::

        orch = PipelineOrchestrator(pipeline_config, workspace_path)
        prun = orch.create_run("dev-flow", thread_id="thr_123", user_input="...")
        prun = await orch.run(prun, step_runner=my_step_runner)
    """

    def __init__(
        self,
        pipeline_config: PipelineConfig,
        workspace_path: str,
    ) -> None:
        self._config = pipeline_config
        self._manifest_manager = ManifestManager(workspace_path)

    # ── Run lifecycle ────────────────────────────────────────────────

    def create_run(
        self,
        definition_id: str,
        thread_id: str,
        user_input: str = "",
    ) -> PipelineRun:
        """Create a new pipeline run from a named definition.

        Returns a ``PipelineRun`` in ``active`` status with all steps
        initialised to ``pending``. Does **not** begin execution.
        """
        definition = self._get_definition(definition_id)
        now = datetime.now(timezone.utc).isoformat()
        steps = [
            PipelineStepRun(
                id=s.id,
                step_id=s.id,
                agent_name=s.agent,
                status=PipelineStepRunStatus.pending,
            )
            for s in definition.steps
        ]
        return PipelineRun(
            id=self._generate_run_id(),
            definition_id=definition_id,
            thread_id=thread_id,
            status=PipelineStatus.active,
            current_step_index=0,
            steps=steps,
            created_at=now,
        )

    async def run(
        self,
        prun: PipelineRun,
        *,
        step_runner: Callable[..., Awaitable[dict[str, Any]]],  # noqa: PYI055
    ) -> PipelineRun:
        """Execute *prun* by running each pending step in topological order.

        Steps whose dependencies are not yet met are skipped until their
        upstream completes. Or-logic: when ``depends_on`` has multiple
        entries, ANY one satisfied is sufficient.  Steps whose ``condition``
        evaluates to ``False`` are marked ``skipped``.
        """
        if prun.status != PipelineStatus.active:
            logger.warning("run %s is not active (status=%s)", prun.id, prun.status)
            return prun

        definition = self._get_definition(prun.definition_id)
        step_defs_by_id = {s.id: s for s in definition.steps}

        # Track completed *and* skipped steps — either satisfies a dependency.
        done: set[str] = set()
        for step_run in prun.steps:
            if step_run.status in (
                PipelineStepRunStatus.completed,
                PipelineStepRunStatus.skipped,
            ):
                done.add(step_run.step_id)

        while True:
            ready = self._find_next_ready_step(prun, step_defs_by_id, done)
            if ready is None:
                break

            step_run, step_def = ready
            idx = next(i for i, s in enumerate(prun.steps) if s.step_id == step_run.step_id)
            prun.current_step_index = idx

            # Evaluate condition (if any) before running.
            if step_def.condition:
                if detect_self_reference(step_def.condition, step_def.id):
                    logger.warning(
                        "step %s condition %r is a self-reference — skipping",
                        step_def.id, step_def.condition,
                    )
                    step_run.status = PipelineStepRunStatus.skipped
                    done.add(step_def.id)
                    continue

                upstream = self._collect_upstream_manifests(step_def, prun)
                if not evaluate_condition(step_def.condition, upstream):
                    logger.info(
                        "step %s condition %r not met — skipping",
                        step_def.id, step_def.condition,
                    )
                    step_run.status = PipelineStepRunStatus.skipped
                    done.add(step_def.id)
                    continue

            step_run.status = PipelineStepRunStatus.running

            try:
                graph_input = self._build_graph_input(step_def, prun)

                outputs = await step_runner(
                    step_def=step_def,
                    graph_input=graph_input,
                    agent_name=step_def.agent,
                )

                contract = [
                    {"path": e.path, "type": e.type, "summary": e.summary}
                    for e in step_def.output_contract
                ]
                collected = self._manifest_manager.collect_outputs("", contract)
                manifest_path = self._manifest_manager.write_manifest(
                    pipeline_id=prun.id,
                    step_id=step_def.id,
                    agent=step_def.agent,
                    status="completed",
                    outputs=collected,
                    decisions=outputs.get("decisions", []),
                    next_steps=outputs.get("next_steps_suggestion"),
                    token_used=outputs.get("token_used", 0),
                )
                step_run.manifest_path = manifest_path
                step_run.status = PipelineStepRunStatus.completed
                done.add(step_def.id)

                logger.info(
                    "pipeline step %s/%s completed (manifest=%s)",
                    prun.id, step_def.id, manifest_path,
                )
            except Exception:
                logger.exception("pipeline step %s/%s failed", prun.id, step_def.id)
                step_run.status = PipelineStepRunStatus.failed
                prun.status = PipelineStatus.failed
                break

        if prun.status == PipelineStatus.active:
            prun.status = PipelineStatus.completed

        return prun

    async def resume_run(
        self,
        prun: PipelineRun,
        *,
        step_runner: Callable[..., Awaitable[dict[str, Any]]],  # noqa: PYI055
    ) -> PipelineRun:
        """Resume *prun* from the last incomplete step.

        Completed steps are left untouched; failed steps are retried.
        """
        for step_run in prun.steps:
            if step_run.status == PipelineStepRunStatus.failed:
                step_run.status = PipelineStepRunStatus.pending

        if prun.status == PipelineStatus.failed:
            prun.status = PipelineStatus.active

        return await self.run(prun, step_runner=step_runner)

    # ── Internal helpers ─────────────────────────────────────────────

    def _get_definition(self, definition_id: str) -> Any:
        for d in self._config.definitions:
            if d.id == definition_id:
                return d
        msg = f"Pipeline definition '{definition_id}' not found"
        raise ValueError(msg)

    def _find_next_ready_step(
        self,
        prun: PipelineRun,
        step_defs_by_id: dict[str, PipelineStepDef],
        done: set[str],
    ) -> tuple[PipelineStepRun, PipelineStepDef] | None:
        """Find the next step whose dependencies are satisfied.

        Or-logic: when ``depends_on`` lists multiple step ids, ANY one
        being done is sufficient.
        """
        for step_run in prun.steps:
            if step_run.status not in (
                PipelineStepRunStatus.pending,
                PipelineStepRunStatus.failed,
            ):
                continue
            step_def = step_defs_by_id.get(step_run.step_id)
            if step_def is None:
                continue
            if not step_def.depends_on:
                # No deps → always ready.
                return step_run, step_def
            # Or-logic: at least one dep must be done.
            if any(dep in done for dep in step_def.depends_on):
                return step_run, step_def
        return None

    def _collect_upstream_manifests(
        self,
        step_def: PipelineStepDef,
        prun: PipelineRun,
    ) -> dict[str, Any]:
        """Collect upstream manifests for condition evaluation."""
        manifests: dict[str, Any] = {}
        for dep_id in step_def.depends_on:
            dep_run = next((s for s in prun.steps if s.step_id == dep_id), None)
            if dep_run is not None and dep_run.manifest_path:
                try:
                    manifest = self._manifest_manager.read_manifest(dep_run.manifest_path)
                    manifests[dep_id] = manifest.model_dump()
                except Exception:
                    manifests[dep_id] = {"status": "unknown", "error": "read failed"}
            elif dep_run is not None:
                manifests[dep_id] = {"status": dep_run.status}
        return manifests

    def _build_graph_input(
        self,
        step_def: PipelineStepDef,
        prun: PipelineRun,
    ) -> dict[str, Any] | None:
        """Build the graph input for a step from completed upstream steps."""
        if not step_def.depends_on:
            return None

        manifests = {}
        for dep_id in step_def.depends_on:
            dep_run = next((s for s in prun.steps if s.step_id == dep_id), None)
            if dep_run is not None and dep_run.manifest_path:
                try:
                    manifest = self._manifest_manager.read_manifest(dep_run.manifest_path)
                    manifests[dep_id] = manifest.model_dump()
                except Exception:
                    manifests[dep_id] = {"status": "unknown", "error": "manifest read failed"}

        return {"upstream_manifests": manifests}

    @staticmethod
    def _generate_run_id() -> str:
        import uuid
        return f"pl_{uuid.uuid4().hex[:12]}"
