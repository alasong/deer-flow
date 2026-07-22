from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any
import json
import os
from datetime import datetime


class Manifest(BaseModel):
    pipeline_id: str
    step_id: str
    agent: str
    status: str  # "completed" | "failed" | "skipped"
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    next_steps_suggestion: list[str] = Field(default_factory=list)
    token_used: int = 0
    completed_at: str | None = None


class ManifestManager:
    """读写 workspace/.pipeline-manifest.json"""

    def __init__(self, workspace_path: str):
        self._workspace_path = workspace_path

    def manifest_path(self, step_id: str) -> str:
        """每个 step 的 manifest 文件路径"""
        return os.path.join(self._workspace_path, f".pipeline-manifest-{step_id}.json")

    def write_manifest(
        self,
        pipeline_id: str,
        step_id: str,
        agent: str,
        status: str,
        outputs: list[dict],
        decisions: list[dict],
        next_steps: list[str] | None = None,
        token_used: int = 0,
    ) -> str:
        """写入 step 的 manifest JSON 文件，返回文件路径。"""
        manifest = Manifest(
            pipeline_id=pipeline_id,
            step_id=step_id,
            agent=agent,
            status=status,
            outputs=outputs,
            decisions=decisions,
            next_steps_suggestion=next_steps or [],
            token_used=token_used,
            completed_at=datetime.now().isoformat(),
        )
        path = self.manifest_path(step_id)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(manifest.model_dump_json(indent=2))
        return path

    def read_manifest(self, path: str) -> Manifest:
        """从 JSON 文件读取并解析 Manifest。"""
        with open(path) as f:
            return Manifest.model_validate_json(f.read())

    @staticmethod
    def collect_outputs(workspace_path: str, output_contract: list[dict]) -> list[dict]:
        """校验 workspace 中是否产生了 output_contract 约定的文件。

        返回匹配到的 outputs 列表，每项含 path, type, summary（来自 contract）。
        不存在的文件不会包含在返回值中（但不会抛异常）。
        """
        matched = []
        for contract_entry in output_contract:
            filepath = contract_entry.get("path", "")
            full_path = (
                os.path.join(workspace_path, filepath)
                if not os.path.isabs(filepath)
                else filepath
            )
            exists = os.path.exists(full_path)
            matched.append(
                {
                    "path": filepath,
                    "type": contract_entry.get("type", ""),
                    "summary": contract_entry.get("summary", ""),
                    "exists": exists,
                }
            )
        return matched
