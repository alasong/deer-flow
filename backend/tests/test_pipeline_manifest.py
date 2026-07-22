"""Tests for Pipeline Manifest module."""

from __future__ import annotations

import json
import os

import pytest

from deerflow.pipeline.manifest import Manifest, ManifestManager


class TestManifestModel:
    """Manifest Pydantic model unit tests."""

    def test_default_values(self):
        """创建 Manifest 实例，验证字段默认值。"""
        m = Manifest(pipeline_id="p1", step_id="s1", agent="a1", status="completed")
        assert m.pipeline_id == "p1"
        assert m.step_id == "s1"
        assert m.agent == "a1"
        assert m.status == "completed"
        assert m.outputs == []
        assert m.decisions == []
        assert m.next_steps_suggestion == []
        assert m.token_used == 0
        assert m.completed_at is None

    def test_serialization_roundtrip(self):
        """model_dump_json -> model_validate_json 往返。"""
        m = Manifest(
            pipeline_id="p1",
            step_id="s1",
            agent="a1",
            status="completed",
            outputs=[{"file": "out.txt"}],
            decisions=[{"choice": "approved"}],
            next_steps_suggestion=["run_test", "deploy"],
            token_used=1024,
            completed_at="2026-07-22T12:00:00",
        )
        raw = m.model_dump_json()
        restored = Manifest.model_validate_json(raw)
        assert restored.model_dump() == m.model_dump()
        assert restored.token_used == 1024


class TestManifestManagerWriteRead:
    """ManifestManager 写/读文件测试。"""

    def test_write_manifest(self, tmp_path):
        """写入 manifest JSON 文件并验证文件存在。"""
        manager = ManifestManager(str(tmp_path))
        path = manager.write_manifest(
            pipeline_id="p1",
            step_id="s1",
            agent="a1",
            status="completed",
            outputs=[{"file": "result.json"}],
            decisions=[{"action": "approved"}],
            next_steps=["deploy"],
            token_used=500,
        )
        assert os.path.exists(path)
        assert path == os.path.join(str(tmp_path), ".pipeline-manifest-s1.json")

    def test_read_manifest(self, tmp_path):
        """读取刚写入的 manifest，验证字段值正确。"""
        manager = ManifestManager(str(tmp_path))
        manager.write_manifest(
            pipeline_id="p1",
            step_id="step_x",
            agent="agent_z",
            status="completed",
            outputs=[{"file": "data.json"}],
            decisions=[{"choice": "yes"}],
            next_steps=["validate"],
            token_used=300,
        )
        path = manager.manifest_path("step_x")
        manifest = manager.read_manifest(path)
        assert manifest.pipeline_id == "p1"
        assert manifest.step_id == "step_x"
        assert manifest.agent == "agent_z"
        assert manifest.status == "completed"
        assert manifest.outputs == [{"file": "data.json"}]
        assert manifest.decisions == [{"choice": "yes"}]
        assert manifest.next_steps_suggestion == ["validate"]
        assert manifest.token_used == 300
        assert manifest.completed_at is not None

    def test_read_manifest_file_content(self, tmp_path):
        """直接读取 JSON 文件验证 content 正确性。"""
        manager = ManifestManager(str(tmp_path))
        manager.write_manifest(
            pipeline_id="p1",
            step_id="s1",
            agent="a1",
            status="completed",
            outputs=[{"file": "out.txt"}],
            decisions=[],
        )
        path = manager.manifest_path("s1")
        with open(path) as f:
            data = json.load(f)
        assert data["pipeline_id"] == "p1"
        assert data["step_id"] == "s1"
        assert data["agent"] == "a1"
        assert data["status"] == "completed"
        assert data["outputs"] == [{"file": "out.txt"}]
        assert data["token_used"] == 0

    def test_manifest_path(self, tmp_path):
        """manifest_path 返回正确的路径格式。"""
        manager = ManifestManager(str(tmp_path))
        path = manager.manifest_path("my_step")
        expected = os.path.join(str(tmp_path), ".pipeline-manifest-my_step.json")
        assert path == expected


class TestCollectOutputs:
    """collect_outputs 静态方法测试。"""

    def test_matched_files(self, tmp_path):
        """collect_outputs 找到匹配的文件。"""
        # 创建匹配的文件
        (tmp_path / "output.json").write_text("{}")
        (tmp_path / "report.md").write_text("# Report")
        contract = [
            {"path": "output.json", "type": "json", "summary": "output data"},
            {"path": "report.md", "type": "markdown", "summary": "report summary"},
        ]
        result = ManifestManager.collect_outputs(str(tmp_path), contract)
        assert len(result) == 2
        for entry in result:
            assert entry["exists"] is True

    def test_missing_file_marked_false(self, tmp_path):
        """不存在的文件 marked exists=False。"""
        contract = [
            {"path": "nonexistent.json", "type": "json", "summary": "missing"},
        ]
        result = ManifestManager.collect_outputs(str(tmp_path), contract)
        assert len(result) == 1
        assert result[0]["path"] == "nonexistent.json"
        assert result[0]["exists"] is False

    def test_empty_contract(self, tmp_path):
        """空 contract 返回空列表。"""
        result = ManifestManager.collect_outputs(str(tmp_path), [])
        assert result == []

    def test_absolute_path_handling(self, tmp_path):
        """绝对路径不拼接 workspace_path。"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        contract = [
            {"path": str(f), "type": "text", "summary": "test"},
        ]
        result = ManifestManager.collect_outputs(str(tmp_path), contract)
        assert len(result) == 1
        assert result[0]["exists"] is True

    def test_partial_match(self, tmp_path):
        """部分文件存在，部分不存在。"""
        (tmp_path / "exists.txt").write_text("yes")
        contract = [
            {"path": "exists.txt", "type": "text", "summary": "exists"},
            {"path": "missing.txt", "type": "text", "summary": "missing"},
            {"path": "also_missing.txt", "type": "text", "summary": "also missing"},
        ]
        result = ManifestManager.collect_outputs(str(tmp_path), contract)
        assert result[0]["exists"] is True
        assert result[1]["exists"] is False
        assert result[2]["exists"] is False
