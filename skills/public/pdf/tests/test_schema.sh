#!/bin/bash
# test_schema.sh — Validate .pdf_state.json schema consistency
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="$SKILL_DIR/docs/pdf_state.schema.json"
errors=0

echo "=== Schema Validation Tests ==="

# Test 1: Schema itself is valid JSON
echo "  [1/5] schema is valid JSON..."
if python3 -c "import json; json.load(open('$SCHEMA'))" 2>/dev/null; then
    echo "    PASS"
else
    echo "    FAIL: schema.json is not valid JSON"
    errors=$((errors+1))
fi

# Test 2: Schema has required fields
echo "  [2/5] schema has required top-level fields..."
python3 -c "
import json
s = json.load(open('$SCHEMA'))
required = s.get('required', [])
for f in ['stage', 'round', 'task_slug', 'stages', 'schema_version']:
    if f in required:
        print(f'    PASS: {f} is required')
    else:
        print(f'    FAIL: {f} is not in required')
        exit(1)
" || errors=$((errors+1))

# Test 3: Stage enum matches expected values
echo "  [3/5] stage enum values..."
python3 -c "
import json
s = json.load(open('$SCHEMA'))
enum = s['properties']['stage']['enum']
expected = ['plan','do','check','act','decompose','child_exec','meta_check','done']
for e in expected:
    if e not in enum:
        print(f'    FAIL: missing stage enum value: {e}')
        exit(1)
print('    PASS: all 8 stage enum values present')
" || errors=$((errors+1))

# Test 4: remediation_type enum matches fix gate
echo "  [4/5] remediation_type enum..."
python3 -c "
import json
s = json.load(open('$SCHEMA'))
enum = s['properties']['remediation_type']['enum']
expected = ['bug','design_flaw','false_positive','flaky_test']
for e in expected:
    if e not in enum:
        print(f'    FAIL: missing remediation_type enum value: {e}')
        exit(1)
print('    PASS: all 4 remediation_type values present')
" || errors=$((errors+1))

# Test 5: schema_version is present
echo "  [5/5] schema_version field..."
python3 -c "
import json
s = json.load(open('$SCHEMA'))
v = s['properties'].get('schema_version', {})
if v.get('type') == 'integer' and v.get('minimum') == 1:
    print('    PASS: schema_version is integer, minimum=1')
else:
    print(f'    FAIL: schema_version field issue: {v}')
    exit(1)
" || errors=$((errors+1))

if [ $errors -gt 0 ]; then
    echo ""
    echo "FAILED: $errors test(s) failed"
    exit 1
else
    echo ""
    echo "OK: all schema tests passed"
fi
