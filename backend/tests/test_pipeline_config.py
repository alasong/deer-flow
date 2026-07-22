"""Tests for pipeline config model and YAML parsing."""

from __future__ import annotations

from deerflow.pipeline.config import (
    OutputContractEntry,
    PipelineConfig,
    PipelineDefinition,
    PipelineStepDef,
)


def _make_minimal_config() -> PipelineConfig:
    """Build a minimal PipelineConfig with one definition."""
    return PipelineConfig(
        enabled=True,
        definitions=[
            PipelineDefinition(
                id="dev-flow",
                description="Design → Code → Review pipeline",
                steps=[
                    PipelineStepDef(id="design", agent="lead-design"),
                    PipelineStepDef(
                        id="code",
                        agent="lead-code",
                        depends_on=["design"],
                        output_contract=[
                            OutputContractEntry(path="src/", type="dir"),
                        ],
                    ),
                ],
            ),
        ],
    )


class TestOutputContractEntry:
    def test_defaults(self):
        entry = OutputContractEntry(path="out.md")
        assert entry.path == "out.md"
        assert entry.type == ""
        assert entry.summary == ""

    def test_all_fields(self):
        entry = OutputContractEntry(path="report.json", type="json", summary="Final report")
        assert entry.path == "report.json"
        assert entry.type == "json"
        assert entry.summary == "Final report"


class TestPipelineStepDef:
    def test_minimal(self):
        step = PipelineStepDef(id="design", agent="lead-design")
        assert step.id == "design"
        assert step.agent == "lead-design"
        assert step.depends_on == []
        assert step.condition is None
        assert step.input is None
        assert step.output_contract == []
        assert step.middleware_profile is None

    def test_with_dependencies(self):
        step = PipelineStepDef(
            id="code",
            agent="lead-code",
            depends_on=["design"],
            condition="design.completed == true",
            input="workspace/design/",
            output_contract=[OutputContractEntry(path="src/", type="dir")],
            middleware_profile="code",
        )
        assert step.depends_on == ["design"]
        assert step.condition == "design.completed == true"
        assert step.input == "workspace/design/"
        assert len(step.output_contract) == 1
        assert step.output_contract[0].path == "src/"
        assert step.middleware_profile == "code"

    def test_or_dependency_list(self):
        step = PipelineStepDef(id="test", agent="lead-test", depends_on=["code", "fix"])
        assert step.depends_on == ["code", "fix"]


class TestPipelineDefinition:
    def test_minimal(self):
        pd = PipelineDefinition(id="simple")
        assert pd.id == "simple"
        assert pd.description == ""
        assert pd.steps == []

    def test_with_steps(self):
        pd = PipelineDefinition(
            id="dev-flow",
            description="Development pipeline",
            steps=[
                PipelineStepDef(id="design", agent="lead-design"),
                PipelineStepDef(id="code", agent="lead-code", depends_on=["design"]),
            ],
        )
        assert len(pd.steps) == 2
        assert pd.steps[0].id == "design"
        assert pd.steps[1].depends_on == ["design"]

    def test_round_trip_via_model_dump(self):
        pd = _make_minimal_config()
        data = pd.model_dump()
        restored = PipelineConfig.model_validate(data)
        assert restored.enabled == pd.enabled
        assert len(restored.definitions) == len(pd.definitions)
        assert restored.definitions[0].id == pd.definitions[0].id
        assert restored.definitions[0].steps[0].id == pd.definitions[0].steps[0].id
        assert restored.definitions[0].steps[1].depends_on == ["design"]


class TestPipelineConfig:
    def test_defaults(self):
        cfg = PipelineConfig()
        assert cfg.enabled is False
        assert cfg.definitions == []
        assert cfg.middleware_profiles == {}

    def test_enabled_with_definitions(self):
        cfg = _make_minimal_config()
        assert cfg.enabled is True
        assert len(cfg.definitions) == 1

    def test_middleware_profiles(self):
        cfg = PipelineConfig(
            enabled=True,
            middleware_profiles={
                "review": ["InputSanitization", "ToolResultSanitization"],
                "code": ["InputSanitization", "Sandbox"],
            },
        )
        assert "review" in cfg.middleware_profiles
        assert cfg.middleware_profiles["review"] == ["InputSanitization", "ToolResultSanitization"]
        assert cfg.middleware_profiles["code"] == ["InputSanitization", "Sandbox"]

    def test_app_config_integration(self):
        """Verify PipelineConfig integrates with AppConfig without error."""
        from deerflow.config.app_config import AppConfig

        cfg = AppConfig.model_validate({
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "pipelines": {
                "enabled": True,
                "definitions": [
                    {
                        "id": "dev-flow",
                        "steps": [
                            {"id": "design", "agent": "lead-design"},
                        ],
                    },
                ],
            },
        })
        assert cfg.pipelines.enabled is True
        assert len(cfg.pipelines.definitions) == 1
        assert cfg.pipelines.definitions[0].id == "dev-flow"
        assert cfg.pipelines.definitions[0].steps[0].agent == "lead-design"

    def test_app_config_defaults_when_absent(self):
        """When pipelines section is absent, AppConfig uses defaults."""
        from deerflow.config.app_config import AppConfig

        cfg = AppConfig.model_validate({
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        })
        assert cfg.pipelines.enabled is False
        assert cfg.pipelines.definitions == []

    def test_app_config_null_section(self):
        """When pipelines section is explicitly null, default applies."""
        from deerflow.config.app_config import AppConfig

        cfg = AppConfig.model_validate({
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "pipelines": None,
        })
        assert cfg.pipelines.enabled is False
