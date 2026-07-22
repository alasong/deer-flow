"""Pipeline configuration model and YAML parsing.

Parses the ``pipelines`` section of ``config.yaml`` into validated Pydantic
models consumed by ``PipelineOrchestrator``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutputContractEntry(BaseModel):
    """Expected output file from a pipeline step."""

    path: str
    type: str = ""
    summary: str = ""


class PipelineStepDef(BaseModel):
    """Definition of a single step within a pipeline."""

    id: str
    agent: str
    depends_on: list[str] = Field(default_factory=list)
    condition: str | None = None
    input: str | None = None
    output_contract: list[OutputContractEntry] = Field(default_factory=list)
    middleware_profile: str | None = None


class PipelineDefinition(BaseModel):
    """A named pipeline template."""

    id: str
    description: str = ""
    steps: list[PipelineStepDef] = Field(default_factory=list)


class PipelineConfig(BaseModel):
    """Top-level ``pipelines`` config section."""

    enabled: bool = False
    definitions: list[PipelineDefinition] = Field(default_factory=list)
    middleware_profiles: dict[str, list[str]] = Field(default_factory=dict)
