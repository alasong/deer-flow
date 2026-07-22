#!/bin/bash
# run_tests.sh — Run all PDD self-tests
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
errors=0

echo "=============================="
echo " PDD Self-Test Suite"
echo "=============================="
echo ""

for test in "$SCRIPT_DIR"/test_*.sh; do
    name="$(basename "$test")"
    echo "--- $name ---"
    if bash "$test"; then
        echo ""
    else
        echo "  FAILED"
        errors=$((errors+1))
        echo ""
    fi
done

echo "=============================="
if [ $errors -gt 0 ]; then
    echo "FAILED: $errors test suite(s) failed"
    exit 1
else
    echo "OK: all tests passed"
fi
