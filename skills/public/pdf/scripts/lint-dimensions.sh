#!/bin/bash
# lint-dimensions.sh — Verify dimension tables are consistent across PDD skill files
#
# Checks:
# 1. dimensions.yaml exists and is valid YAML (basic syntax)
# 2. All dimensions from dimensions.yaml appear in SKILL.md dimension tables
# 3. All dimensions from dimensions.yaml appear in risk-profile.md dimension tables
# 4. No extra dimensions in SKILL.md/risk-profile.md not in dimensions.yaml

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YAML="$SKILL_DIR/docs/dimensions.yaml"
SKILL="$SKILL_DIR/SKILL.md"
RISK="$SKILL_DIR/docs/risk-profile.md"

errors=0

# Check dimensions.yaml exists
if [ ! -f "$YAML" ]; then
  echo "ERROR: $YAML not found"
  exit 1
fi

# Check YAML has dimensions key
if ! grep -q '^dimensions:' "$YAML"; then
  echo "ERROR: $YAML missing 'dimensions:' key"
  exit 1
fi

# Extract dimension names from YAML
YAML_DIMS=$(grep '^  - name: ' "$YAML" | sed 's/  - name: //')

if [ -z "$YAML_DIMS" ]; then
  echo "ERROR: no dimensions found in $YAML"
  exit 1
fi

echo "Dimensions from $YAML:"
echo "$YAML_DIMS" | sed 's/^/  /'
echo ""

# Forward check: YAML -> SKILL.md / risk-profile.md
for dim in $YAML_DIMS; do
  if ! grep -qi "[|│].*$dim" "$SKILL" 2>/dev/null; then
    echo "ERROR: dimension '$dim' found in dimensions.yaml but NOT in SKILL.md"
    errors=$((errors+1))
  fi
  if ! grep -qi "|.*$dim" "$RISK" 2>/dev/null; then
    echo "ERROR: dimension '$dim' found in dimensions.yaml but NOT in risk-profile.md"
    errors=$((errors+1))
  fi
done

# Reverse check: extract dimension names from markdown table rows in SKILL.md
# SKILL.md uses plain text: | correctness | ... |
while IFS= read -r line; do
  case "$line" in
    \|\ ---*) continue ;;
    \|\ 维度*) continue ;;
    \|\ 阶段*) continue ;;
    \|\ N*) continue ;;
    \|\ M*) continue ;;
    \|\ 能力*) continue ;;
    \|\ 等级*) continue ;;
    \|\ 场景*) continue ;;
    \|\ 通道*) continue ;;
    \|\ 条件*) continue ;;
    \|\ lite*) continue ;;
    \|\ standard*) continue ;;
    \|\ full*) continue ;;
  esac
  # Extract first column from | col1 | col2 | ... |
  first_col=$(echo "$line" | sed -n 's/^| *\([a-z_][a-z_]*\) *|.*/\1/p')
  [ -z "$first_col" ] && continue
  # Skip P0.54 factor names (not review dimensions)
  case "$first_col" in
    security_audit|api_compatibility|performance_sensitive|data_integrity|compliance) continue ;;
  esac
  if ! echo "$YAML_DIMS" | grep -qi "^$first_col$"; then
    echo "ERROR: dimension '$first_col' found in SKILL.md but NOT in dimensions.yaml"
    errors=$((errors+1))
  fi
done < <(grep -E '^\| [a-z_]' "$SKILL" 2>/dev/null || true)

# Reverse check: risk-profile.md uses bold: | **correctness** | ... |
while IFS= read -r line; do
  first_col=$(echo "$line" | sed -n 's/^| \*\*\([a-z_][a-z_]*\)\*\*.*/\1/p')
  [ -z "$first_col" ] && continue
  if ! echo "$YAML_DIMS" | grep -qi "^$first_col$"; then
    echo "ERROR: dimension '$first_col' found in risk-profile.md but NOT in dimensions.yaml"
    errors=$((errors+1))
  fi
done < <(grep -E '^\| \*\*[a-z_]' "$RISK" 2>/dev/null || true)

if [ $errors -gt 0 ]; then
  echo ""
  echo "FAILED: $errors inconsistency(s) found"
  echo "Fix: update dimensions.yaml first, then sync SKILL.md and risk-profile.md"
  exit 1
else
  echo "OK: all dimensions consistent across dimensions.yaml, SKILL.md, and risk-profile.md"
fi
