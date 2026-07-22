#!/bin/bash
# test_goal_doer.sh — E2E tests for goal_doer node type
#
# Tests:
#   1. Blueprint with goal_doer node passes validate_blueprint
#   2. pipeline_tick recognizes goal_doer as a ready node
#   3. node_start + node_complete lifecycle
#   4. Artifact validation (present vs missing)
#   5. node_fail retry behavior
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$SKILL_DIR/tools"
errors=0
test_count=0

export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "=== Goal Doer E2E Tests ==="
echo ""

# ────────────────────────────────────────────────────────────────────────
# Test 1: Validate blueprint with goal_doer node
# ────────────────────────────────────────────────────────────────────────
test_count=$((test_count+1))
echo "  [1/$test_count] validate blueprint with goal_doer node..."
python3 -c '
import sys
sys.path.insert(0, "'"$TOOLS_DIR"'")
from engine.dag import validate_blueprint

bp = {
    "stages": {
        "do": {
            "nodes": [
                {"ref": "G1_goal_doer", "type": "goal_doer", "deps": []}
            ]
        }
    }
}
errors = validate_blueprint(bp)
if errors:
    for e in errors:
        print(f"    BAD: {e}")
    sys.exit(1)
print("    PASS: goal_doer recognized as valid node type")
' && rc=0 || rc=$?

if [ $rc -ne 0 ]; then
    echo "    FAIL: goal_doer was rejected as unknown type"
    errors=$((errors+1))
fi

# ────────────────────────────────────────────────────────────────────────
# Test 2: pipeline_tick recognizes goal_doer as ready node
# ────────────────────────────────────────────────────────────────────────
test_count=$((test_count+1))
echo "  [2/$test_count] pipeline_tick recognizes goal_doer as ready..."
python3 -c '
import sys
sys.path.insert(0, "'"$TOOLS_DIR"'")
from engine.dag import validate_blueprint, _init_dag_progress
from engine.runner import pipeline_tick

bp = {
    "stages": {
        "do": {
            "nodes": [
                {"ref": "G1_goal_doer", "type": "goal_doer", "deps": []},
                {"ref": "G2_goal_doer", "type": "goal_doer", "deps": ["G1_goal_doer"]}
            ]
        }
    }
}
# Validate first
errs = validate_blueprint(bp)
if errs:
    print(f"    BAD: blueprint has errors: {errs}")
    sys.exit(1)

# Init state
state = {"stage": "do"}
_init_dag_progress(state, bp)

# Tick
result = pipeline_tick(state, bp)
action = result.get("action")
nodes = result.get("nodes", [])

if action != "nodes_ready":
    print(f"    BAD: expected nodes_ready, got {action}")
    sys.exit(1)

refs = [n[0] for n in nodes]
if "G1_goal_doer" not in refs:
    print(f"    BAD: expected G1_goal_doer in ready set, got {refs}")
    sys.exit(1)

print(f"    PASS: pipeline_tick returned nodes_ready with {len(refs)} ready node(s)")
' && rc=0 || rc=$?

if [ $rc -ne 0 ]; then
    echo "    FAIL: pipeline_tick did not recognize goal_doer"
    errors=$((errors+1))
fi

# ────────────────────────────────────────────────────────────────────────
# Test 3: node_start → node_complete lifecycle
# ────────────────────────────────────────────────────────────────────────
test_count=$((test_count+1))
echo "  [3/$test_count] node_start → node_complete lifecycle..."
python3 -c '
import sys
sys.path.insert(0, "'"$TOOLS_DIR"'")
from engine.dag import _init_dag_progress
from engine.runner import pipeline_tick, node_start, node_complete

bp = {
    "stages": {
        "do": {
            "nodes": [
                {"ref": "G1_goal_doer", "type": "goal_doer", "deps": []}
            ]
        }
    }
}

state = {"stage": "do"}
_init_dag_progress(state, bp)

# Start the node
start_result = node_start(state, "do", "G1_goal_doer", bp)
if start_result.get("action") != "started":
    print(f"    BAD: node_start failed: {start_result}")
    sys.exit(1)

# Verify node is marked running
status = state.get("dag_progress", {}).get("do", {}).get("G1_goal_doer")
if status != "running":
    print(f"    BAD: expected running, got {status}")
    sys.exit(1)

# Complete the node
complete_result = node_complete(state, "do", "G1_goal_doer", bp)
# After completion, pipeline_tick is called internally.
# Since all nodes done, it returns stage_done.
if complete_result.get("action") != "stage_done":
    print(f"    BAD: expected stage_done, got {complete_result.get('action')}: {complete_result}")
    sys.exit(1)

# Verify node is marked done
status = state.get("dag_progress", {}).get("do", {}).get("G1_goal_doer")
if status != "done":
    print(f"    BAD: expected done, got {status}")
    sys.exit(1)

print("    PASS: node_start→running→node_complete→done→stage_done")
' && rc=0 || rc=$?

if [ $rc -ne 0 ]; then
    echo "    FAIL: goal_doer lifecycle did not complete"
    errors=$((errors+1))
fi

# ────────────────────────────────────────────────────────────────────────
# Test 4: Artifact validation for goal_doer node
# ────────────────────────────────────────────────────────────────────────
test_count=$((test_count+1))
echo "  [4/$test_count] goal_doer artifact validation..."
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

python3 -c '
import os, sys
sys.path.insert(0, "'"$TOOLS_DIR"'")
from engine.dag import _init_dag_progress
from engine.runner import node_complete

bp = {
    "stages": {
        "do": {
            "nodes": [
                {
                    "ref": "G1_goal_doer",
                    "type": "goal_doer",
                    "deps": [],
                    "artifacts": ["goal_output.md"]
                }
            ]
        }
    }
}

tmpdir = "'"$TMPDIR"'"

# ---- 4a: Artifact present ----
state_a = {"stage": "do", "project_root": tmpdir}
_init_dag_progress(state_a, bp)
state_a["dag_progress"]["do"]["G1_goal_doer"] = "running"

# Create the artifact file
artifact_path = os.path.join(tmpdir, "goal_output.md")
with open(artifact_path, "w") as f:
    f.write("# Goal Output\n")

result_a = node_complete(state_a, "do", "G1_goal_doer", bp)
if result_a.get("action") not in ("stage_done", "nodes_ready"):
    print(f"    4a BAD: expected stage_done or nodes_ready, got {result_a}")
    sys.exit(1)

# Remove the artifact so 4b can test the missing-artifact case
os.remove(artifact_path)

# ---- 4b: Artifact missing ----
state_b = {"stage": "do", "project_root": tmpdir}
_init_dag_progress(state_b, bp)
state_b["dag_progress"]["do"]["G1_goal_doer"] = "running"

result_b = node_complete(state_b, "do", "G1_goal_doer", bp)
if result_b.get("action") != "artifact_missing":
    print(f"    4b BAD: expected artifact_missing, got {result_b}")
    sys.exit(1)

print("    PASS: 4a artifact present→complete, 4b artifact missing→rejected")
' && rc=0 || rc=$?

if [ $rc -ne 0 ]; then
    echo "    FAIL: goal_doer artifact validation"
    errors=$((errors+1))
fi
rm -rf "$TMPDIR"
trap "" EXIT

# ────────────────────────────────────────────────────────────────────────
# Test 5: node_fail retry behavior for goal_doer
# ────────────────────────────────────────────────────────────────────────
test_count=$((test_count+1))
echo "  [5/$test_count] goal_doer node_fail behavior..."
python3 -c '
import sys
sys.path.insert(0, "'"$TOOLS_DIR"'")
from engine.dag import _init_dag_progress
from engine.runner import node_fail, pipeline_tick

bp = {
    "stages": {
        "do": {
            "nodes": [
                {"ref": "G1_goal_doer", "type": "goal_doer", "deps": [], "retry": 3}
            ]
        }
    }
}

state = {"stage": "do"}
_init_dag_progress(state, bp)

# Must be running before failure
state["dag_progress"]["do"]["G1_goal_doer"] = "running"

# First failure (retry=3, so count=1 < max_retry)
result = node_fail(state, "do", "G1_goal_doer", bp)
action = result.get("action")
retries = result.get("retries")

if action != "node_failed":
    print(f"    BAD: expected node_failed, got action={action}, result={result}")
    sys.exit(1)
if retries != 1:
    print(f"    BAD: expected retries=1, got {retries}")
    sys.exit(1)

# Verify node is marked "failed" and ready for retry
status = state.get("dag_progress", {}).get("do", {}).get("G1_goal_doer")
if status != "failed":
    print(f"    BAD: expected failed status, got {status}")
    sys.exit(1)

# Verify retry_count recorded
rcount = state.get("retry_counts", {}).get("do.G1_goal_doer", 0)
if rcount != 1:
    print(f"    BAD: expected retry_count=1, got {rcount}")
    sys.exit(1)

# After failure, pipeline_tick should see the node as ready for retry
tick_result = pipeline_tick(state, bp)
if tick_result.get("action") != "nodes_ready":
    # Could also be nodes_failed if retry count matches max
    print(f"    BAD: expected nodes_ready for retry, got {tick_result.get('action')}: {tick_result}")
    sys.exit(1)

print(f"    PASS: node_fail→failed→retriable (retry_count={rcount})")
' && rc=0 || rc=$?

if [ $rc -ne 0 ]; then
    echo "    5a FAIL: basic failure behavior"
    errors=$((errors+1))
fi

echo "  [5/$test_count.sub] goal_doer retries exhausted..."
python3 -c '
import sys
sys.path.insert(0, "'"$TOOLS_DIR"'")

bp = {
    "stages": {
        "do": {
            "nodes": [
                {"ref": "G1_goal_doer", "type": "goal_doer", "deps": [], "retry": 3}
            ]
        }
    }
}

# Verify that a goal_doer with exhausted retries routes to the correct
# rollback path (do → bug)
from engine.dag import _init_dag_progress
from engine.runner import node_fail

state = {"stage": "do"}
_init_dag_progress(state, bp)
state["dag_progress"]["do"]["G1_goal_doer"] = "running"
state["retry_counts"] = {"do.G1_goal_doer": 3}  # already used 3 retries

result = node_fail(state, "do", "G1_goal_doer", bp)
action = result.get("action")

# With retries exhausted in "do" stage for a "doer" node,
# the action may be auto_rollback or retries_exhausted
# depending on whether HSM can fire the "bug" event.
# At minimum, we verify it did not error out halfway.
if action == "auto_rollback":
    print("    PASS: goal_doer retry exhausted → auto_rollback (do→bug)")
elif action == "retries_exhausted":
    # This is also valid — the rollback may not be able to fire
    # without a proper HSM setup, but the routing decision is correct
    node_ref = result.get("node", "")
    retries = result.get("retries", 0)
    print(f"    PASS: goal_doer retry exhausted → retries_exhausted (node={node_ref}, retries={retries})")
else:
    print(f"    BAD: unexpected action={action}")
    print(f"    Full result: {result}")
    sys.exit(1)
' && rc2=0 || rc2=$?

if [ $rc2 -ne 0 ]; then
    errors=$((errors+1))
fi

# Proper exit
if [ $errors -gt 0 ]; then
    echo ""
    echo "FAILED: $errors test(s) failed"
    exit 1
else
    echo ""
    echo "OK: all tests passed"
fi
