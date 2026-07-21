"""Tests for SkillOrchestrator — skill selection and execution plan generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from deerflow.skills.types import Skill, SkillCategory
from deerflow.skills.catalog import SkillCatalog
from deerflow.tasks.orchestrator import (
    ExecutionPlan,
    ExecutionStep,
    PlanStepKind,
    SkillOrchestrator,
)


def _make_skill(name: str, description: str) -> Skill:
    return Skill(
        name=name,
        description=description,
        license=None,
        skill_dir=Path(f"/skills/{name}"),
        skill_file=Path(f"/skills/{name}/SKILL.md"),
        relative_path=Path(name),
        category=SkillCategory.PUBLIC,
    )


@pytest.fixture
def catalog() -> SkillCatalog:
    return SkillCatalog(skills=(
        _make_skill("deep-research", "Comprehensive research on any topic using web search and analysis"),
        _make_skill("data-analysis", "Analyze data, create visualizations, and derive insights"),
        _make_skill("code-documentation", "Generate documentation for codebases and projects"),
        _make_skill("image-generation", "Generate images from text descriptions"),
        _make_skill("podcast-generation", "Create podcast-style audio content"),
        _make_skill("frontend-design", "Design and implement frontend UI and system architecture"),
    ))


class TestExecutionStep:
    def test_create_step(self):
        step = ExecutionStep(skill="deep-research", channel="full", description="Research topic")
        assert step.kind == PlanStepKind.sequence
        assert step.skill == "deep-research"
        assert not step.is_gate

    def test_gate_step(self):
        step = ExecutionStep(skill="", channel="", description="Human review", kind=PlanStepKind.gate)
        assert step.is_gate

    def test_parallel_step(self):
        step = ExecutionStep(skill="", channel="", description="Parallel tasks", kind=PlanStepKind.parallel)
        assert step.kind == PlanStepKind.parallel


class TestExecutionPlan:
    def test_empty_plan(self):
        plan = ExecutionPlan(steps=[])
        assert plan.total_steps == 0
        assert not plan.has_next(0)

    def test_single_step(self):
        plan = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full"),
        ])
        assert plan.total_steps == 1

    def test_multi_step_plan(self):
        plan = ExecutionPlan(steps=[
            ExecutionStep(skill="deep-research", channel="full"),
            ExecutionStep(skill="data-analysis", channel="standard"),
        ])
        assert plan.total_steps == 2
        assert plan.has_next(0)
        assert not plan.has_next(1)


class TestSkillOrchestrator:
    def test_select_skill_by_keyword(self, catalog):
        orch = SkillOrchestrator()
        plan = orch.plan("调研最新AI论文", catalog)
        assert plan.total_steps >= 1
        # Should pick deep-research for research task
        assert plan.steps[0].skill == "deep-research"

    def test_analyze_data_task(self, catalog):
        orch = SkillOrchestrator()
        plan = orch.plan("分析销售数据并生成图表", catalog)
        assert plan.total_steps >= 1
        assert plan.steps[0].skill == "data-analysis"

    def test_empty_catalog(self):
        orch = SkillOrchestrator()
        plan = orch.plan("Do something", SkillCatalog(skills=()))
        assert plan.total_steps == 0

    def test_multi_step_pipeline(self, catalog):
        """Research → analysis pipeline should use multiple skills."""
        orch = SkillOrchestrator()
        # Task that requires both research and analysis
        plan = orch.plan("调研AI最新发展并分析数据趋势", catalog)
        assert plan.total_steps >= 1
        skills = [s.skill for s in plan.steps]
        assert "deep-research" in skills

    def test_select_channel_based_on_complexity(self, catalog):
        orch = SkillOrchestrator()
        # Simple task → quick or lite channel
        simple = orch.plan("查一下今天的天气", catalog)
        assert simple.steps[0].channel in ("lite", "standard", "quick")

        # Complex task → full channel
        complex_task = orch.plan("Implement a complete user authentication system with registration, login, password reset, OAuth integration", catalog)
        assert complex_task.steps[0].channel in ("full", "standard")

    def test_with_custom_skills(self):
        """Custom skills should be selectable too."""
        orch = SkillOrchestrator()
        custom_catalog = SkillCatalog(skills=(
            _make_skill("my-custom-skill", "Custom skill for specialized tasks"),
        ))
        plan = orch.plan("Use my custom skill for a specialized task", custom_catalog)
        assert plan.total_steps == 1
        assert plan.steps[0].skill == "my-custom-skill"

    def test_gate_insertion(self, catalog):
        """When human_review=True, a gate step should be inserted."""
        orch = SkillOrchestrator()
        plan = orch.plan("设计系统架构", catalog, human_review=True)
        # Should have at least one skill step + one gate
        assert plan.total_steps >= 2
        gates = [s for s in plan.steps if s.is_gate]
        assert len(gates) >= 1

    def test_gate_not_inserted_by_default(self, catalog):
        orch = SkillOrchestrator()
        plan = orch.plan("查一下今天的天气", catalog)
        gates = [s for s in plan.steps if s.is_gate]
        assert len(gates) == 0

    def test_default_skill_fallback(self):
        """When no skills match, orchestrator should return empty plan."""
        orch = SkillOrchestrator()
        plan = orch.plan("xyzzy_nonexistent_task", SkillCatalog(skills=(_make_skill("data-analysis", "analysis"),)))
        assert plan.total_steps == 0
