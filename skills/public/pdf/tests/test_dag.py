"""Tests for dag.py — goal_doer node type.

Tests that goal_doer is a valid node type in the blueprint schema,
that its deps resolve correctly, and that it is recognized as ready
in pipeline tick (compute_ready_nodes).
"""
import os
import sys

# Add tools/engine to sys.path so we can import dag module
_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.path.dirname(_TEST_DIR)
sys.path.insert(0, os.path.join(_SKILL_DIR, "tools", "engine"))

from dag import validate_blueprint, _compute_ready_nodes


def test_goal_doer_validate():
    """Blueprint with goal_doer node passes validation."""
    blueprint = {
        "stages": {
            "plan": {
                "nodes": [
                    {"ref": "G1_goal", "type": "goal_doer", "deps": [], "retry": 2},
                ]
            }
        }
    }
    errors = validate_blueprint(blueprint)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_goal_doer_deps():
    """goal_doer node deps resolve correctly."""
    blueprint = {
        "stages": {
            "plan": {
                "nodes": [
                    {"ref": "P0_precheck", "type": "engine_exec", "deps": [], "retry": 2},
                    {"ref": "G1_goal", "type": "goal_doer", "deps": ["P0_precheck"], "retry": 1},
                    {"ref": "P2_review", "type": "llm_spawn", "deps": ["G1_goal"], "retry": 2},
                ]
            }
        }
    }
    errors = validate_blueprint(blueprint)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_goal_doer_ready():
    """goal_doer is recognized as ready when deps are met."""
    blueprint = {
        "stages": {
            "plan": {
                "nodes": [
                    {"ref": "P0_precheck", "type": "engine_exec", "deps": [], "retry": 2},
                    {"ref": "G1_goal", "type": "goal_doer", "deps": ["P0_precheck"], "retry": 1},
                ]
            }
        }
    }
    state = {
        "stage": "plan",
        "dag_progress": {
            "plan": {
                "P0_precheck": "done",
            }
        },
    }
    ready = _compute_ready_nodes(state, blueprint, "plan")
    ready_refs = [r[0] for r in ready]
    assert "G1_goal" in ready_refs, (
        f"Expected G1_goal in ready nodes, got: {ready_refs}"
    )
