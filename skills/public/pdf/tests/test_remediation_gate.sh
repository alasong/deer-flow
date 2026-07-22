#!/bin/bash
# test_remediation_gate.sh — Verify fix gate routing logic
# Tests the decision tree described in SKILL.md:
#   Priority: design_flaw > bug > flaky_test > false_positive
#   design_flaw → plan_rem_loop+=1, stage=plan, pause at >=2
#   bug → do_rem_loop+=1, stage=do, pause at >=3
#   flaky_test → retry (max 3), skip counters
#   false_positive → skip, no counter
set -uo pipefail

errors=0
test_count=0

assert_routing() {
    test_count=$((test_count+1))
    local desc="$1"
    local p1_count="$2"
    local types="$3"  # comma-separated: design_flaw,bug,flaky,false_positive
    local exp_stage="$4"
    local exp_counter="$5"  # which counter increments: plan_rem_loop|do_rem_loop|none
    local exp_inc="$6"  # 1 or 0
    local plan_loop="${7:-0}"
    local do_loop="${8:-0}"
    local should_pause="${9:-false}"

    # Simulate priority matching
    local match=""
    if echo "$types" | grep -q "design_flaw"; then
        match="design_flaw"
    elif echo "$types" | grep -q "bug"; then
        match="bug"
    elif echo "$types" | grep -q "flaky"; then
        match="flaky"
    elif echo "$types" | grep -q "false_positive"; then
        match="false_positive"
    fi

    local new_plan=$plan_loop
    local new_do=$do_loop
    local target=""
    local pause=false

    case "$match" in
        design_flaw)
            new_plan=$((plan_loop + 1))
            target="plan"
            [ $new_plan -ge 2 ] && pause=true
            ;;
        bug)
            new_do=$((do_loop + 1))
            target="do"
            [ $new_do -ge 3 ] && pause=true
            ;;
        flaky)
            target="current (retry)"
            pause=false
            ;;
        false_positive)
            target="current (skip)"
            pause=false
            ;;
        *)
            target="act"
            ;;
    esac

    # Check results
    local pass=true
    [ "$target" = "$exp_stage" ] || { echo "    FAIL [$test_count] $desc: expected stage=$exp_stage got=$target"; pass=false; }

    local actual_counter="none"
    [ $new_plan -ne $plan_loop ] && actual_counter="plan_rem_loop"
    [ $new_do -ne $do_loop ] && actual_counter="do_rem_loop"
    [ "$actual_counter" = "$exp_counter" ] || { echo "    FAIL [$test_count] $desc: expected counter=$exp_counter got=$actual_counter (plan=$plan_loop->$new_plan, do=$do_loop->$new_do)"; pass=false; }

    [ "$pause" = "$should_pause" ] || { echo "    FAIL [$test_count] $desc: expected pause=$should_pause got=$pause (plan=$new_plan do=$new_do)"; pass=false; }

    $pass && echo "    PASS [$test_count] $desc"
    $pass || errors=$((errors+1))
}

echo "=== Fix Gate Routing Tests ==="

# Test: P1=0 → Act
assert_routing "no P1 findings" 0 "" "act" "none" 0

# Test: Priority, design_flaw > bug
assert_routing "design_flaw takes priority over bug" 2 "design_flaw,bug" "plan" "plan_rem_loop" 1

# Test: Single bug
assert_routing "single bug" 1 "bug" "do" "do_rem_loop" 1

# Test: Single design_flaw
assert_routing "single design_flaw" 1 "design_flaw" "plan" "plan_rem_loop" 1

# Test: flaky does not consume counters
assert_routing "flaky does not increment counter" 1 "flaky" "current (retry)" "none" 0

# Test: false_positive skip
assert_routing "false_positive skip" 1 "false_positive" "current (skip)" "none" 0

# Test: Pause at plan_rem_loop >= 2
assert_routing "pause at plan_rem_loop>=2" 1 "design_flaw" "plan" "plan_rem_loop" 1 1 0 true

# Test: No pause at plan_rem_loop < 2
assert_routing "no pause at plan_rem_loop=0" 1 "design_flaw" "plan" "plan_rem_loop" 1 0 0 false

# Test: Pause at do_rem_loop >= 3
assert_routing "pause at do_rem_loop>=3" 1 "bug" "do" "do_rem_loop" 1 0 2 true

# Test: No pause at do_rem_loop < 3
assert_routing "no pause at do_rem_loop=1" 1 "bug" "do" "do_rem_loop" 1 0 1 false

# Test: P1=0 even with high counters → Act
assert_routing "P1=0 ignores counters" 0 "" "act" "none" 0 2 3 false

echo ""
if [ $errors -gt 0 ]; then
    echo "FAILED: $errors/$test_count tests failed"
    exit 1
else
    echo "OK: all $test_count tests passed"
fi
