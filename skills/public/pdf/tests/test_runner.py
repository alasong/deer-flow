"""Tests for pipeline runner -- goal_doer node completion path.

Tests that the goal_doer node type in node_complete() correctly:
1. Marks nodes as done (standard path, not manual_checkpoint)
2. Validates declared artifacts exist
3. Rejects completion when artifacts are missing
4. Triggers pipeline_tick correctly after completion
"""

from tools.engine import runner


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_goal_doer_bp(artifacts=None, optional=False, extra_nodes=None):
    """Create a minimal blueprint with a goal_doer node."""
    node = {"ref": "G1_goal", "type": "goal_doer", "deps": [], "retry": 2}
    if artifacts:
        node["artifacts"] = artifacts
    if optional:
        node["optional"] = True

    nodes = [node]
    if extra_nodes:
        nodes.extend(extra_nodes)

    return {
        "stages": {
            "do": {
                "nodes": nodes,
                "transitions": [
                    {"event": "done", "target": "__exit__", "max_loops": 1},
                ],
            }
        }
    }


def _make_dag_progress(stage, refs, status="pending"):
    """Create dag_progress dict for given refs."""
    return {stage: {ref: status for ref in refs}}


def _make_state(stage="do", refs=None, project_root=None):
    """Create a minimal pipeline state dict."""
    refs = refs or ["G1_goal"]
    state = {
        "stage": stage,
        "dag_progress": _make_dag_progress(stage, refs),
        "auto_mode": True,
    }
    if project_root is not None:
        state["project_root"] = project_root
    return state


# ── Tests ───────────────────────────────────────────────────────────────

class TestGoalDoerCompletion:
    """Verify goal_doer node completion path in node_complete()."""

    def test_marks_node_done(self):
        """goal_doer node is marked as done after node_complete()."""
        bp = _make_goal_doer_bp()
        state = _make_state()

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        # Must be marked done
        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        # pipeline_tick fires -- single node => stage_done
        assert result["action"] == "stage_done"

    def test_artifact_check_passes_when_exists(self, tmp_path):
        """goal_doer completes when declared artifact exists on disk."""
        bp = _make_goal_doer_bp(artifacts=["goal_output.md"])
        state = _make_state(project_root=str(tmp_path))

        # Create the artifact
        (tmp_path / "goal_output.md").write_text("goal met")

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert result["action"] == "stage_done"

    def test_artifact_check_fails_when_missing(self):
        """goal_doer returns artifact_missing when declared artifact missing."""
        bp = _make_goal_doer_bp(artifacts=["missing_task.md"])
        state = _make_state(project_root="/nonexistent_test_dir_xyz")

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert result["action"] == "artifact_missing"
        assert result["node"] == "G1_goal"
        assert result["artifact"] == "missing_task.md"
        assert result["stage"] == "do"
        # Node must NOT be marked done
        assert state["dag_progress"]["do"]["G1_goal"] == "pending"

    def test_pipeline_tick_advances_to_next_node(self):
        """After goal_doer completes, pipeline tick shows next node ready."""
        bp = _make_goal_doer_bp(extra_nodes=[
            {"ref": "G2_followup", "type": "llm_spawn",
             "deps": ["G1_goal"], "retry": 2},
        ])
        state = _make_state(refs=["G1_goal", "G2_followup"])

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert result["action"] == "nodes_ready"
        assert len(result["nodes"]) == 1
        assert result["nodes"][0][0] == "G2_followup"

    def test_not_checkpoint_blocked(self):
        """goal_doer is NOT treated as manual_checkpoint."""
        bp = _make_goal_doer_bp()
        state = _make_state()

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        # Must be done, not checkpoint_blocked
        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert "checkpoint" not in result.get("action", "")

    def test_artifact_with_glob_pattern(self, tmp_path):
        """goal_doer artifact check passes with glob pattern matching file."""
        bp = _make_goal_doer_bp(artifacts=["goal_*.md"])
        state = _make_state(project_root=str(tmp_path))

        # Create a file matching the glob
        (tmp_path / "goal_result.md").write_text("goal met via glob")

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert result["action"] == "stage_done"

    def test_artifact_check_with_glob_no_match(self):
        """goal_doer returns artifact_missing when no glob match."""
        bp = _make_goal_doer_bp(artifacts=["goal_*.md"])
        state = _make_state(project_root="/nonexistent_test_dir_xyz")

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert result["action"] == "artifact_missing"
        assert "goal_*.md" in result.get("artifact", "")
        assert state["dag_progress"]["do"]["G1_goal"] == "pending"

    def test_artifact_in_fat_pdf_dir(self, tmp_path):
        """goal_doer finds artifact in .fat/pdf/ subdirectory."""
        bp = _make_goal_doer_bp(artifacts=["goal_output.md"])
        state = _make_state(project_root=str(tmp_path))

        # Create artifact in .fat/pdf/ (the fallback search dir)
        fat_dir = tmp_path / ".fat" / "pdf"
        fat_dir.mkdir(parents=True)
        (fat_dir / "goal_output.md").write_text("goal met in fat dir")

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert result["action"] == "stage_done"

    def test_no_artifacts_declared_no_check(self):
        """goal_doer with no artifacts field skips validation."""
        bp = _make_goal_doer_bp()  # no artifacts
        state = _make_state()

        result = runner.node_complete(state, "do", "G1_goal", blueprint=bp)

        assert state["dag_progress"]["do"]["G1_goal"] == "done"
        assert result["action"] == "stage_done"
