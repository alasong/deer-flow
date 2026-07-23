"""Tests for autonomous mode: log_decision tool, prompt switching, decision extraction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.tools.builtins.log_decision_tool import log_decision_tool
from deerflow.runtime.runs.worker import _extract_decision_entries, _summarize_tool_call


class TestLogDecisionTool:
    """Unit tests for the log_decision tool itself."""

    def test_basic_call(self):
        result = log_decision_tool.invoke({
            "decision_type": "approach_choice",
            "summary": "Chose PostgreSQL over MySQL",
            "reasoning": "Better JSON support for flexible schema",
        })
        assert "[approach_choice] Chose PostgreSQL over MySQL" in result
        assert "Better JSON support" in result

    def test_with_alternatives(self):
        result = log_decision_tool.invoke({
            "decision_type": "tradeoff",
            "summary": "Picked performance optimization",
            "reasoning": "N+1 query was the bottleneck",
            "alternatives": ["readability refactor", "add caching layer"],
        })
        assert "[tradeoff]" in result
        assert "readability refactor" in result
        assert "add caching layer" in result

    def test_risk_assessment(self):
        result = log_decision_tool.invoke({
            "decision_type": "risk_assessment",
            "summary": "In-place migration safe",
            "reasoning": "Table has < 1000 rows, lock is < 100ms",
        })
        assert "[risk_assessment]" in result
        assert "In-place migration safe" in result


class TestPromptTemplateSwitch:
    """Verify apply_prompt_template selects the right system section."""

    def test_interactive_mode_has_clarification_system(self):
        prompt = apply_prompt_template()
        assert "<clarification_system>" in prompt
        assert "<autonomous_decision_system>" not in prompt

    def test_autonomous_mode_has_autonomous_system(self):
        prompt = apply_prompt_template(autonomous_mode=True)
        assert "<autonomous_decision_system>" in prompt
        assert "<clarification_system>" not in prompt

    def test_autonomous_mode_mentions_log_decision(self):
        prompt = apply_prompt_template(autonomous_mode=True)
        assert "log_decision" in prompt

    def test_interactive_mode_mentions_ask_clarification(self):
        prompt = apply_prompt_template()
        assert "ask_clarification" in prompt

    def test_default_is_interactive(self):
        prompt = apply_prompt_template()
        assert "<clarification_system>" in prompt


class TestDecisionExtraction:
    """Verify _extract_decision_entries captures log_decision tool calls."""

    def _make_ai_message(self, tool_calls_data: list[dict]) -> SimpleNamespace:
        """Create a fake AI message with tool_calls matching the AIMessage interface."""
        msg = SimpleNamespace()
        msg.tool_calls = tool_calls_data
        return msg

    def test_log_decision_extracted(self):
        tool_call = {
            "name": "log_decision",
            "id": "call_001",
            "args": {
                "decision_type": "approach_choice",
                "summary": "Chose Redis over Memcached",
                "reasoning": "Better data structure support",
            },
        }
        msg = self._make_ai_message([tool_call])
        entries = _extract_decision_entries([msg], run_id="run_001")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["decision_type"] == "tool:log_decision"
        assert "[approach_choice]" in entry["summary"]
        assert "Redis" in entry["summary"]

    def test_log_decision_with_other_tools(self):
        """log_decision entries coexist with existing significant tool calls."""
        tool_calls = [
            {
                "name": "task",
                "id": "call_001",
                "args": {"description": "Analyze data"},
            },
            {
                "name": "log_decision",
                "id": "call_002",
                "args": {
                    "decision_type": "route_selection",
                    "summary": "Used subagents",
                    "reasoning": "Data volume too large for single pass",
                },
            },
        ]
        msg = self._make_ai_message(tool_calls)
        entries = _extract_decision_entries([msg], run_id="run_001")
        assert len(entries) == 2
        types = {e["decision_type"] for e in entries}
        assert "tool:task" in types
        assert "tool:log_decision" in types


class TestSummarizeToolCall:
    """Verify _summarize_tool_call handles log_decision."""

    def test_log_decision_summary(self):
        summary = _summarize_tool_call("log_decision", {
            "decision_type": "approach_choice",
            "summary": "Chose X over Y",
        })
        assert "[approach_choice]" in summary
        assert "Chose X over Y" in summary
