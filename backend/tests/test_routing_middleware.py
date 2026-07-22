"""Tests for RoutingMiddleware."""
from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.skills.router.middleware import RoutingMiddleware


def _make_state(messages, **overrides):
    state = {"messages": messages}
    state.update(overrides)
    return state


def _make_runtime():
    runtime = SimpleNamespace()
    runtime.context = {"thread_id": "test-tid-1"}
    return runtime


class TestRoutingMiddleware:
    """Tests for RoutingMiddleware."""

    def test_no_user_message_returns_none(self):
        """No human message → no guidance injected."""
        state = _make_state([])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is None

    def test_injects_guidance_for_tech_analysis(self):
        """Tech + analysis → pdf (analysis channel)."""
        state = _make_state([HumanMessage(content="分析一下 AI 芯片的市场格局")])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is not None
        messages = result.get("messages", [])
        assert len(messages) == 1
        msg = messages[0]
        assert isinstance(msg, SystemMessage)
        content = msg.content
        assert "<skill_routing>" in content
        assert "pdf" in content
        assert "analysis" in content
        assert "domain" in content
        assert msg.additional_kwargs.get("hide_from_ui") is True

    def test_injects_guidance_for_simple_query(self):
        """Simple query → direct action."""
        state = _make_state([HumanMessage(content="今天天气怎么样")])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is not None
        messages = result.get("messages", [])
        assert len(messages) == 1
        content = messages[0].content
        assert "<skill_routing>" in content
        assert "direct" in content
        assert "low" in content

    def test_injects_guidance_for_high_complexity_implement(self):
        """High complexity implementation → catch-all (pdf/standard) when domain is general."""
        state = _make_state([HumanMessage(content="实现一个分布式缓存系统，包括一致性哈希和故障转移机制")])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is not None
        messages = result.get("messages", [])
        assert len(messages) == 1
        content = messages[0].content
        assert "pdf" in content
        assert "high" in content

    def test_custom_classifier(self):
        """Custom classifier is used when provided."""
        from deerflow.skills.router.classifier import SkillClassifier

        class CustomClassifier(SkillClassifier):
            def classify(self, text):
                from deerflow.skills.router import RoutingContext

                return RoutingContext(domain="custom", complexity="low", task_type="query")

        mw = RoutingMiddleware(classifier=CustomClassifier())
        state = _make_state([HumanMessage(content="hello")])
        runtime = _make_runtime()
        result = mw.before_agent(state, runtime)
        assert result is not None
        content = result["messages"][0].content
        assert "custom" in content

    def test_async_before_agent(self):
        """Async path delegates to sync."""
        state = _make_state([HumanMessage(content="分析一下")])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        import asyncio

        result = asyncio.run(mw.abefore_agent(state, runtime))
        assert result is not None
        assert "pdf" in result["messages"][0].content

    def test_latest_user_message_only(self):
        """Only the latest human message is classified."""
        state = _make_state([
            HumanMessage(content="旧消息"),
            SystemMessage(content="系统消息"),
            HumanMessage(content="分析一下 AI 芯片趋势"),
        ])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is not None
        # Should classify "分析一下 AI 芯片趋势" (tech/analysis) not "旧消息"
        content = result["messages"][0].content
        assert "tech" in content
        assert "high" in content

    def test_non_string_content_returns_none(self):
        """Non-string message content is skipped gracefully."""
        state = _make_state([HumanMessage(content=[])])  # empty list content
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is None

    def test_wont_crash_on_empty_messages_list(self):
        """Empty messages list returns None, no crash."""
        state = _make_state([])
        runtime = _make_runtime()
        mw = RoutingMiddleware()
        result = mw.before_agent(state, runtime)
        assert result is None

    def test_routing_mode_defaults_to_advisory(self):
        """routing_mode parameter defaults to advisory when not specified."""
        mw = RoutingMiddleware()
        assert mw.routing_mode == "advisory"

    def test_auto_activate_injects_skill_context(self):
        """auto_activate mode with skill route injects skill_context in state update."""
        state = _make_state([HumanMessage(content="分析一下 AI 芯片的市场格局")])
        runtime = _make_runtime()
        mw = RoutingMiddleware(routing_mode="auto_activate")
        result = mw.before_agent(state, runtime)
        assert result is not None
        assert "skill_context" in result
        assert len(result["skill_context"]) == 1
        sc = result["skill_context"][0]
        assert sc["name"] == "pdf"
        assert "path" in sc
        assert "Auto-activated" in sc["description"]
        assert isinstance(sc["loaded_at"], int)
        # Should still have messages (guidance)
        assert "messages" in result
        assert len(result["messages"]) == 1

    def test_auto_activate_action_route_no_skill_context(self):
        """auto_activate mode with action route doesn't inject skill_context."""
        state = _make_state([HumanMessage(content="今天天气怎么样")])
        runtime = _make_runtime()
        mw = RoutingMiddleware(routing_mode="auto_activate")
        result = mw.before_agent(state, runtime)
        assert result is not None
        assert "messages" in result
        assert "skill_context" not in result

    def test_enforce_mode_accepted(self):
        """enforce mode is accepted without error (stub level)."""
        state = _make_state([HumanMessage(content="分析一下 AI 芯片趋势")])
        runtime = _make_runtime()
        mw = RoutingMiddleware(routing_mode="enforce")
        result = mw.before_agent(state, runtime)
        assert result is not None
        assert "messages" in result

    def test_route_level_mode_override(self):
        """Route-level mode field overrides middleware default."""
        # Middleware defaults to advisory, but tech/analysis route has mode: auto_activate
        state = _make_state([HumanMessage(content="分析一下 AI 芯片的市场格局")])
        runtime = _make_runtime()
        mw = RoutingMiddleware()  # default advisory
        result = mw.before_agent(state, runtime)
        assert result is not None
        # Route has mode: auto_activate, so skill_context is injected despite advisory default
        assert "skill_context" in result
        assert result["skill_context"][0]["name"] == "pdf"
