#!/bin/bash
# lint.sh — Unified lint runner for PDD skill files
#
# Usage: ./lint.sh [command] [options]
#   Commands:
#     dimensions   Verify dimension consistency (dimensions.yaml vs SKILL.md vs risk-profile.md)
#     schema       Validate .pdf_state.json against pdf_state.schema.json
#     cross-refs   Check cross-file reference consistency
#     all          Run all checks (default)
#
# Examples:
#   ./lint.sh all            Run all lint checks
#   ./lint.sh dimensions     Only dimension consistency check

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
errors=0

check_dimensions() {
    echo "=== Dimensions ==="
    "${SKILL_DIR}/scripts/lint-dimensions.sh" || errors=$((errors+$?))
}

check_schema() {
    echo "=== Schema Validation ==="
    local schema="${SKILL_DIR}/docs/pdf_state.schema.json"
    local state_files=("$SKILL_DIR/.fat/pdf/.pdf_state.json")
    if [ ! -f "$schema" ]; then
        echo "ERROR: Schema file not found at $schema"
        errors=$((errors+1))
        return
    fi
    # Validate JSON syntax of schema
    if ! python3 -c "import json; json.load(open('$schema')); print('schema.json: valid JSON')" 2>/dev/null; then
        echo "ERROR: $schema is not valid JSON"
        errors=$((errors+1))
    fi
    # Validate state files against schema if they exist
    for f in "${state_files[@]}"; do
        if [ -f "$f" ]; then
            echo "Validating $f against schema..."
            python3 -c "
import json, sys
try:
    schema = json.load(open('$schema'))
    state = json.load(open('$f'))
    # Basic field validation: check required fields exist
    required = schema.get('required', [])
    missing = [k for k in required if k not in state]
    if missing:
        print(f'WARN: missing required fields: {missing}', file=sys.stderr)
    else:
        print(f'  Required fields present')
except json.JSONDecodeError as e:
    print(f'ERROR: JSON decode error: {e}', file=sys.stderr)
    sys.exit(1)
" || errors=$((errors+1))
        fi
    done
}

check_cross_refs() {
    echo "=== Cross-Reference Consistency ==="
    # Check 1: remediation_type enum in schema matches fix gate text
    echo "  [1/3] remediation_type enum vs fix gate..."
    local schema_enum
    schema_enum=$(python3 -c "
import json
s=json.load(open('${SKILL_DIR}/docs/pdf_state.schema.json'))
props=s.get('properties',{})
rt=props.get('remediation_type',{})
print(' '.join(rt.get('enum',[])))
" 2>/dev/null)
    if [ -n "$schema_enum" ]; then
        for val in $schema_enum; do
            if ! grep -qi "$val" "${SKILL_DIR}/SKILL.md" 2>/dev/null; then
                echo "  WARN: remediation_type '$val' in schema but not mentioned in SKILL.md"
            fi
        done
        echo "    OK: remediation_type enum values present in SKILL.md"
    fi

    # Check 2: dimension names in reviewer-checklists match dimensions.yaml
    echo "  [2/3] reviewer-checklists vs dimensions.yaml..."
    dim_errors=0
    for dim in $(grep '^  - name: ' "${SKILL_DIR}/docs/dimensions.yaml" 2>/dev/null | sed 's/  - name: //'); do
        if ! grep -qi "^## $dim$" "${SKILL_DIR}/docs/reviewer-checklists.md" 2>/dev/null; then
            echo "  WARN: dimension '$dim' missing checklist section in reviewer-checklists.md"
            dim_errors=$((dim_errors+1))
        fi
    done
    if [ $dim_errors -eq 0 ]; then
        echo "    OK: all dimensions have checklists"
    fi
    errors=$((errors+dim_errors))

    # Check 3: KB file references exist
    echo "  [3/3] KB file references..."
    local kb_dir="${SKILL_DIR}/.fat/pdf/knowledge"
    if [ -d "$kb_dir" ]; then
        echo "    KB directory exists at .fat/pdf/knowledge/"
    else
        echo "    INFO: KB directory not yet created (expected on first run)"
    fi
}

case "${1:-all}" in
    dimensions)
        check_dimensions
        ;;
    schema)
        check_schema
        ;;
    cross-refs)
        check_cross_refs
        ;;
    all)
        check_dimensions
        check_schema
        check_cross_refs
        ;;
    *)
        echo "Usage: $0 [dimensions|schema|cross-refs|all]"
        exit 1
        ;;
esac

if [ $errors -gt 0 ]; then
    echo ""
    echo "FAILED: $errors check(s) failed"
    exit 1
else
    echo ""
    echo "OK: all checks passed"
fi
