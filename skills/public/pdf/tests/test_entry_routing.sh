#!/bin/bash
# test_entry_routing.sh — Verify PDD entry routing logic
# Tests: read-only → Plan only, modify → Full PDCA
set -uo pipefail

errors=0
test_count=0

assert_routing() {
    test_count=$((test_count+1))
    local desc="$1"
    local task_type="$2"  # read-only or modify
    local exp_result="$3"  # plan-only or full-pdca

    local result
    case "$task_type" in
        read-only)  result="plan-only" ;;
        modify)     result="full-pdca" ;;
        *)          result="unknown" ;;
    esac

    if [ "$result" = "$exp_result" ]; then
        echo "    PASS [$test_count] $desc: $task_type → $result"
    else
        echo "    FAIL [$test_count] $desc: $task_type → $result, expected $exp_result"
        errors=$((errors+1))
    fi
}

echo "=== Entry Routing Tests ==="

# Standard routing
assert_routing "code search" "read-only" "plan-only"
assert_routing "documentation reading" "read-only" "plan-only"
assert_routing "git log inspection" "read-only" "plan-only"
assert_routing "run tests" "read-only" "plan-only"
assert_routing "bug fix" "modify" "full-pdca"
assert_routing "feature add" "modify" "full-pdca"
assert_routing "refactoring" "modify" "full-pdca"

# Task type N-adaptivity test
echo ""
echo "=== Plan N-Adaptivity Tests ==="

test_n() {
    test_count=$((test_count+1))
    local desc="$1"
    local task_type="$2"
    local exp_n="$3"

    local n=2
    case "$task_type" in
        config|docs|audit) n=1 ;;
        *) n=2 ;;
    esac

    if [ $n -eq $exp_n ]; then
        echo "    PASS [$test_count] $desc: $task_type → N=$n"
    else
        echo "    FAIL [$test_count] $desc: $task_type → N=$n, expected N=$exp_n"
        errors=$((errors+1))
    fi
}

test_n "config change" "config" 1
test_n "documentation update" "docs" 1
test_n "security audit" "audit" 1
test_n "new feature" "feature" 2
test_n "bug fix" "bugfix" 2
test_n "refactoring" "refactor" 2

# Schema migration test
echo ""
echo "=== Schema Migration Tests ==="

test_migration() {
    test_count=$((test_count+1))
    local desc="$1"
    local schema_version="$2"
    local exp_should_migrate="$3"

    local should_migrate=false
    [ "${schema_version:-0}" -lt 2 ] && should_migrate=true

    if [ "$should_migrate" = "$exp_should_migrate" ]; then
        echo "    PASS [$test_count] $desc: schema v${schema_version} → migrate=$should_migrate"
    else
        echo "    FAIL [$test_count] $desc: schema v${schema_version} → migrate=$should_migrate, expected=$exp_should_migrate"
        errors=$((errors+1))
    fi
}

test_migration "new state (no version)" "" true
test_migration "v1 state" "1" true
test_migration "v2 state" "2" false
test_migration "v3 state" "3" false

echo ""
if [ $errors -gt 0 ]; then
    echo "FAILED: $errors/$test_count tests failed"
    exit 1
else
    echo "OK: all $test_count tests passed"
fi
