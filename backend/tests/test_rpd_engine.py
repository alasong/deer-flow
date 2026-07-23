"""Tests for RPD engine — node-advance, batch-done, and docstring."""

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from deerflow.rpd import engine, shared


@pytest.fixture(autouse=True)
def _isolate_state_dir():
    """Use a temp dir for state so tests don't clobber each other."""
    with tempfile.TemporaryDirectory() as tmp:
        orig_dir = shared.STATE_DIR
        orig_file = shared.STATE_FILE
        shared.STATE_DIR = tmp
        shared.STATE_FILE = os.path.join(tmp, "rpd_state.json")
        yield
        shared.STATE_DIR = orig_dir
        shared.STATE_FILE = orig_file


@pytest.fixture
def inited_state():
    """Return a state dict with init already called."""
    engine.cmd_init(slug="test", goal="test task")
    return shared.load_state()


# ── node-advance ───────────────────────────────────────────────────────


class TestCmdTreeNodeAdvance:
    def test_advance_pending_to_done(self, inited_state):
        root_id = inited_state["root"]["id"]
        result = engine.cmd_tree_node_advance(root_id, action="done")
        assert result["action"] == "node_advanced"
        assert result["node_id"] == root_id
        assert result["result"] == "done"
        state = shared.load_state()
        assert state["root"]["status"] == "done"

    def test_advance_pending_to_fail(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_advance(root_id, action="fail")
        state = shared.load_state()
        assert state["root"]["status"] == "failed"

    def test_advance_pending_to_skip(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_advance(root_id, action="skip")
        state = shared.load_state()
        assert state["root"]["status"] == "skipped"

    def test_advance_with_summary(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_advance(root_id, action="done", result_summary="all good")
        state = shared.load_state()
        dl = state["root"].get("decision_log", [])
        assert len(dl) == 1
        assert dl[0]["summary"] == "all good"
        assert dl[0]["event"] == "advanced"

    def test_advance_non_pending_raises(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_advance(root_id, action="done")
        with pytest.raises(ValueError, match="not pending"):
            engine.cmd_tree_node_advance(root_id, action="done")

    def test_advance_invalid_action(self, inited_state):
        root_id = inited_state["root"]["id"]
        with pytest.raises(ValueError, match="Invalid advance action"):
            engine.cmd_tree_node_advance(root_id, action="invalid")

    def test_advance_progress(self, inited_state):
        """progress tracking on advance should match total nodes."""
        root_id = inited_state["root"]["id"]
        result = engine.cmd_tree_node_advance(root_id, action="done")
        assert result["progress"]["terminal"] == 1
        assert result["progress"]["total"] == 1


# ── batch-done ─────────────────────────────────────────────────────────


class TestCmdTreeBatchDone:
    def test_batch_multiple_nodes(self, inited_state):
        """expand 3 children, batch-done all of them."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "P", "mode": "research", "title": "A", "dependencies": []},
            {"phase": "D", "mode": "design", "title": "B", "dependencies": []},
            {"phase": "D", "mode": "implement", "title": "C", "dependencies": []},
        ])
        state = shared.load_state()
        child_ids = [c["id"] for c in state["root"]["children"]]

        result = engine.cmd_tree_batch_done(child_ids)
        assert result["action"] == "batch_done"
        assert len(result["results"]) == 3
        for r in result["results"]:
            assert r["status"] == "done"

        state = shared.load_state()
        for c in state["root"]["children"]:
            assert c["status"] == "done"

    def test_batch_with_summaries(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "P", "mode": "research", "title": "A"},
            {"phase": "D", "mode": "design", "title": "B"},
        ])
        state = shared.load_state()
        child_ids = [c["id"] for c in state["root"]["children"]]
        summaries = {child_ids[0]: "done A", child_ids[1]: "done B"}

        engine.cmd_tree_batch_done(child_ids, summaries=summaries)
        state = shared.load_state()
        for c in state["root"]["children"]:
            dl = c.get("decision_log", [])
            assert len(dl) == 1
            assert dl[0]["summary"] == summaries[c["id"]]

    def test_batch_partial_failure(self, inited_state):
        """batch should skip already-done nodes without raising."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "P", "mode": "research", "title": "A"},
            {"phase": "D", "mode": "design", "title": "B"},
        ])
        state = shared.load_state()
        child_ids = [c["id"] for c in state["root"]["children"]]

        # mark first node done manually
        engine.cmd_tree_node_done(child_ids[0])
        result = engine.cmd_tree_batch_done(child_ids)
        # first should report error (not pending)
        assert "error" in result["results"][0]
        assert "not pending" in result["results"][0]["error"]
        assert result["results"][1]["status"] == "done"

    def test_batch_progress(self, inited_state):
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "P", "mode": "research", "title": "A"},
        ])
        state = shared.load_state()
        result = engine.cmd_tree_batch_done([state["root"]["children"][0]["id"]])
        assert result["progress"]["terminal"] == 1
        assert result["progress"]["total"] == 2  # root + 1 child


# ── rpd_tool docstring compactness ────────────────────────────────────


class TestRpdToolDocstring:
    def test_docstring_under_1200_chars(self):
        """rpd_tool docstring should be compact — under 1200 chars."""
        from deerflow.tools.builtins.rpd_tool import rpd_tool

        assert len(rpd_tool.description) < 1200, (
            f"rpd_tool docstring is {len(rpd_tool.description)} chars — "
            "trim it to keep per-call tool-schema overhead low"
        )


# ── init-and-expand ───────────────────────────────────────────────────


class TestCmdInitAndExpand:
    def test_init_and_expand_creates_state_and_children(self):
        result = engine.cmd_init_and_expand(
            slug="combo",
            goal="combined init+expand",
            children=[
                {"phase": "P", "mode": "research", "title": "R1"},
                {"phase": "D", "mode": "implement", "title": "D1"},
            ],
        )
        assert result["action"] == "initialized_and_expanded"
        assert result["slug"] == "combo"
        assert result["count"] == 2
        assert "root_id" in result
        assert "tick" in result

        # state should be persisted
        state = shared.load_state()
        assert state["slug"] == "combo"
        assert len(state["root"]["children"]) == 2

    def test_init_and_expand_conflict_no_force(self):
        engine.cmd_init(slug="existing", goal="first")
        result = engine.cmd_init_and_expand(
            slug="new", goal="second",
            children=[{"phase": "P", "mode": "research", "title": "X"}],
        )
        assert result["action"] == "conflict"
        assert "Active RPD task exists" in result.get("message", "")

    def test_init_and_expand_force_replaces(self):
        engine.cmd_init(slug="existing", goal="first")
        result = engine.cmd_init_and_expand(
            slug="new", goal="second", force=True,
            children=[{"phase": "P", "mode": "research", "title": "X"}],
        )
        assert result["slug"] == "new"
        state = shared.load_state()
        assert state["slug"] == "new"

    def test_init_and_expand_returns_tick(self):
        """expand should return tick so agent doesn't need a separate tick call."""
        result = engine.cmd_init_and_expand(
            slug="tick-test", goal="test",
            children=[
                {"phase": "P", "mode": "research", "title": "R1", "dependencies": []},
                {"phase": "D", "mode": "implement", "title": "D1", "dependencies": []},
            ],
        )
        tick = result.get("tick", {})
        assert tick.get("action") == "nodes_ready"
        assert tick.get("count") == 2


# ── produced (交付件体系) ──────────────────────────────────────────────


class TestNodeProduced:
    def test_make_node_has_produced(self):
        """Every node should have a 'produced' field with default empty structure."""
        node = shared.make_node(phase="D", mode="implement", title="test")
        assert "produced" in node
        assert node["produced"]["files"] == []
        assert node["produced"]["spec_delta"]["added"] == []
        assert node["produced"]["spec_delta"]["modified"] == []
        assert node["produced"]["spec_delta"]["removed"] == []
        assert node["produced"]["decisions"] == []

    def test_node_done_stores_produced(self, inited_state):
        """node-done should store produced payload on the node."""
        root_id = inited_state["root"]["id"]
        produced = {
            "files": [{"path": "src/search.py", "role": "source"}],
            "spec_delta": {
                "added": [{"file": "src/search.py", "summary": "全文搜索接口"}],
                "modified": [],
                "removed": [],
            },
            "decisions": [{"what": "选择 ES", "why": "已有集群"}],
        }
        engine.cmd_tree_node_done(root_id, produced=produced)
        state = shared.load_state()
        assert state["root"]["produced"] == produced

    def test_node_done_without_produced_keeps_default(self, inited_state):
        """node-done without produced should not clear default."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_done(root_id)
        state = shared.load_state()
        assert state["root"]["produced"]["files"] == []

    def test_node_advance_does_not_set_produced(self, inited_state):
        """node-advance should not set produced (keeps default)."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_advance(root_id, action="done")
        state = shared.load_state()
        assert state["root"]["produced"]["files"] == []

    def test_batch_done_stores_produced(self, inited_state):
        """batch-done with produced_map should store per-node."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "D", "title": "A"},
            {"phase": "D", "title": "B"},
        ])
        state = shared.load_state()
        child_ids = [c["id"] for c in state["root"]["children"]]
        produced_map = {
            child_ids[0]: {"files": [{"path": "a.py", "role": "source"}],
                           "spec_delta": {"added": [], "modified": [], "removed": []},
                           "decisions": []},
            child_ids[1]: {"files": [{"path": "b.py", "role": "source"}],
                           "spec_delta": {"added": [], "modified": [], "removed": []},
                           "decisions": []},
        }
        engine.cmd_tree_batch_done(child_ids, produced_map=produced_map)
        state = shared.load_state()
        for c in state["root"]["children"]:
            assert c["produced"]["files"] == produced_map[c["id"]]["files"]

    def test_aggregate_produced(self):
        """_aggregate_produced merges produced from multiple children."""
        children = [
            {"produced": {"files": [{"path": "a.py", "role": "source"}],
                          "spec_delta": {"added": [{"file": "a.py", "summary": "impl A"}],
                                         "modified": [], "removed": []},
                          "decisions": [{"what": "choose X"}]}},
            {"produced": {"files": [{"path": "b.py", "role": "source"}],
                          "spec_delta": {"added": [{"file": "b.py", "summary": "impl B"}],
                                         "modified": [], "removed": []},
                          "decisions": [{"what": "choose Y"}]}},
        ]
        result = shared._aggregate_produced(children)
        assert len(result["files"]) == 2
        assert len(result["spec_added"]) == 2
        assert len(result["decisions"]) == 2

    def test_node_done_root_updates_task_status(self, inited_state):
        """node-done on root should set task status to done."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_node_done(root_id)
        state = shared.load_state()
        assert state["status"] == "done"

    def test_children_done_aggregates_in_tick(self, inited_state):
        """tick children_done should include aggregated produced from children."""
        root_id = inited_state["root"]["id"]
        engine.cmd_tree_expand(root_id, [
            {"phase": "D", "title": "A"},
            {"phase": "D", "title": "B"},
        ])
        state = shared.load_state()
        child_ids = [c["id"] for c in state["root"]["children"]]

        # Mark children done with produced data
        p1 = {"files": [{"path": "a.py", "role": "source"}],
              "spec_delta": {"added": [{"file": "a.py", "summary": "A"}],
                             "modified": [], "removed": []},
              "decisions": [{"what": "choose A"}]}
        p2 = {"files": [{"path": "b.py", "role": "source"}],
              "spec_delta": {"added": [{"file": "b.py", "summary": "B"}],
                             "modified": [], "removed": []},
              "decisions": [{"what": "choose B"}]}
        engine.cmd_tree_node_done(child_ids[0], produced=p1)
        engine.cmd_tree_node_done(child_ids[1], produced=p2)

        # Tick should report children_done with aggregated produced
        tick = engine.cmd_tree_tick()
        assert tick["action"] == "nodes_ready"
        children_done = [n for n in tick["nodes"] if n["action"] == "children_done"]
        assert len(children_done) == 1
        agg = children_done[0].get("aggregated", {})
        assert len(agg.get("files", [])) == 2
        assert len(agg.get("spec_added", [])) == 2
        assert len(agg.get("decisions", [])) == 2
