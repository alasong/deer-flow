"""Tests for ConditionEvaluator — expression parsing, comparison, cycle guard."""

from __future__ import annotations

import pytest

from deerflow.pipeline.conditions import (
    _parse_literal,
    _resolve_field,
    detect_self_reference,
    evaluate_condition,
)


def _manifest(outputs: dict | None = None, **extra) -> dict:
    return {"status": "completed", "outputs": outputs or {}, **extra}


class TestEvaluateCondition:
    def test_always_returns_true(self):
        assert evaluate_condition("always", {}) is True

    def test_never_returns_false(self):
        assert evaluate_condition("never", {}) is False

    def test_whitespace_only_is_true(self):
        assert evaluate_condition("  ", {}) is True
        assert evaluate_condition("", {}) is True

    def test_eq_true(self):
        manifests = {"review": _manifest({"has_issues": True})}
        assert evaluate_condition("review.has_issues == true", manifests) is True

    def test_eq_false(self):
        manifests = {"review": _manifest({"has_issues": False})}
        assert evaluate_condition("review.has_issues == true", manifests) is False

    def test_not_eq(self):
        manifests = {"review": _manifest({"has_issues": True})}
        assert evaluate_condition("review.has_issues != false", manifests) is True
        assert evaluate_condition("review.has_issues != true", manifests) is False

    def test_numeric_gt(self):
        manifests = {"code": _manifest({"coverage": 85})}
        assert evaluate_condition("code.coverage > 80", manifests) is True
        assert evaluate_condition("code.coverage > 90", manifests) is False

    def test_numeric_lt(self):
        manifests = {"code": _manifest({"coverage": 75})}
        assert evaluate_condition("code.coverage < 80", manifests) is True
        assert evaluate_condition("code.coverage < 70", manifests) is False

    def test_numeric_gte(self):
        manifests = {"code": _manifest({"coverage": 80})}
        assert evaluate_condition("code.coverage >= 80", manifests) is True
        assert evaluate_condition("code.coverage >= 81", manifests) is False

    def test_numeric_lte(self):
        manifests = {"code": _manifest({"coverage": 80})}
        assert evaluate_condition("code.coverage <= 80", manifests) is True
        assert evaluate_condition("code.coverage <= 79", manifests) is False

    def test_nested_field_path(self):
        manifests = {"review": _manifest({"findings": {"count": 3}})}
        assert evaluate_condition("review.findings.count > 0", manifests) is True
        assert evaluate_condition("review.findings.count == 3", manifests) is True

    def test_string_equality(self):
        manifests = {"review": _manifest({"result": "approved"})}
        assert evaluate_condition('review.result == "approved"', manifests) is True
        assert evaluate_condition('review.result == "rejected"', manifests) is False

    def test_string_single_quotes(self):
        manifests = {"review": _manifest({"result": "approved"})}
        assert evaluate_condition("review.result == 'approved'", manifests) is True

    def test_missing_manifest_returns_false(self):
        assert evaluate_condition("missing.field == true", {}) is False

    def test_none_value(self):
        manifests = {"step": _manifest({"result": None})}
        assert evaluate_condition("step.result == null", manifests) is True

    def test_unrecognised_syntax_returns_false(self):
        assert evaluate_condition("some weird syntax", {}) is False

    def test_extra_whitespace(self):
        manifests = {"review": _manifest({"has_issues": True})}
        assert evaluate_condition("  review.has_issues == true  ", manifests) is True


class TestDetectSelfReference:
    def test_detects_self_reference(self):
        assert detect_self_reference("my_step.done == true", "my_step") is True

    def test_different_step_is_not_self(self):
        assert detect_self_reference("other.done == true", "my_step") is False

    def test_always_is_not_self(self):
        assert detect_self_reference("always", "my_step") is False

    def test_empty_is_not_self(self):
        assert detect_self_reference("", "my_step") is False


class TestResolveField:
    def test_top_level_field(self):
        assert _resolve_field({"a": 1}, "a") == 1

    def test_nested_field(self):
        assert _resolve_field({"a": {"b": 2}}, "a.b") == 2

    def test_deep_nesting(self):
        assert _resolve_field({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_missing_field(self):
        assert _resolve_field({"a": 1}, "b") is None

    def test_partial_missing(self):
        assert _resolve_field({"a": {"b": 2}}, "a.x") is None

    def test_list_index(self):
        assert _resolve_field({"items": [10, 20, 30]}, "items.1") == 20

    def test_list_index_out_of_range(self):
        assert _resolve_field({"items": [10]}, "items.5") is None


class TestParseLiteral:
    def test_true(self):
        assert _parse_literal("true") is True

    def test_false(self):
        assert _parse_literal("false") is False

    def test_none(self):
        assert _parse_literal("null") is None
        assert _parse_literal("none") is None

    def test_integer(self):
        assert _parse_literal("42") == 42

    def test_float(self):
        assert _parse_literal("3.14") == 3.14

    def test_quoted_string(self):
        assert _parse_literal('"hello"') == "hello"
        assert _parse_literal("'world'") == "world"

    def test_unquoted_string(self):
        assert _parse_literal("hello") == "hello"
