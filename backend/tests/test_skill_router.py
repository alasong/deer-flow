"""Tests for Skill Router — classifier + route engine.

Follows TDD: tests written first, then implementation.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from deerflow.skills.router.classifier import RoutingContext, SkillClassifier
from deerflow.skills.router.engine import RouteResult, RouterEngine


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_routes_yaml(path: Path, content: str) -> str:
    """Write YAML content to a file and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


_ROUTES_SAMPLE = """
routes:
  - match: { domain: "tech", complexity: "high", task_type: "analysis" }
    skill: "pdf"
    channel: "analysis"
  - match: { domain: "tech", complexity: "high", task_type: "implementation" }
    skill: "pdf"
    channel: "full"
  - match: { task_type: "research" }
    skill: "deep-research"
  - match: { task_type: "query", complexity: "low" }
    action: "direct"
"""


# ── Classifier Tests ─────────────────────────────────────────────────────────


class TestClassifier:
    """SkillClassifier: extracts domain/complexity/task_type/keywords from text."""

    def test_classifier_extracts_domain(self):
        """'分析一下 AI 芯片' -> domain=tech, complexity=high, keywords contains AI/芯片."""
        classifier = SkillClassifier()
        ctx = classifier.classify("分析一下 AI 芯片")
        assert ctx.domain == "tech", f"Expected tech, got {ctx.domain}"
        assert ctx.complexity == "high", f"Expected high, got {ctx.complexity}"
        assert "AI" in ctx.keywords, f"Expected AI in keywords, got {ctx.keywords}"
        assert "芯片" in ctx.keywords, f"Expected 芯片 in keywords, got {ctx.keywords}"

    def test_classifier_low_complexity(self):
        """'今天天气怎么样' -> domain=general, complexity=low."""
        classifier = SkillClassifier()
        ctx = classifier.classify("今天天气怎么样")
        assert ctx.domain == "general", f"Expected general, got {ctx.domain}"
        assert ctx.complexity == "low", f"Expected low, got {ctx.complexity}"

    def test_classifier_task_type_analysis(self):
        """分析类文本应识别为 analysis task_type."""
        classifier = SkillClassifier()
        ctx = classifier.classify("研究一下深度学习的架构设计")
        assert ctx.task_type == "analysis", f"Expected analysis, got {ctx.task_type}"

    def test_classifier_task_type_research(self):
        """调研类文本应识别为 research task_type."""
        classifier = SkillClassifier()
        ctx = classifier.classify("调研目前主流的向量数据库方案")
        assert ctx.task_type == "research", f"Expected research, got {ctx.task_type}"

    def test_classifier_empty_text(self):
        """空文本应返回默认值."""
        classifier = SkillClassifier()
        ctx = classifier.classify("")
        assert ctx.domain == "general"
        assert ctx.complexity == "low"

    def test_classifier_short_greeting(self):
        """简短日常问候应识别为 general/query/low."""
        classifier = SkillClassifier()
        ctx = classifier.classify("你好")
        assert ctx.domain == "general"
        assert ctx.complexity == "low"
        assert ctx.task_type == "query"


# ── Router Engine Tests ─────────────────────────────────────────────────────


class TestRouterEngine:
    """RouterEngine: match RoutingContext against route table."""

    def test_route_table_match(self, tmp_path: Path):
        """路由表应正确匹配 skill+channel."""
        engine = RouterEngine()
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", _ROUTES_SAMPLE)
        engine.load_routes(file_path)

        ctx = RoutingContext(domain="tech", complexity="high", task_type="analysis", keywords=["AI"])
        result = engine.route(ctx)
        assert result.skill == "pdf", f"Expected pdf, got {result.skill}"
        assert result.channel == "analysis", f"Expected analysis, got {result.channel}"

    def test_route_table_match_research(self, tmp_path: Path):
        """research 任务应匹配 deep-research skill."""
        engine = RouterEngine()
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", _ROUTES_SAMPLE)
        engine.load_routes(file_path)

        ctx = RoutingContext(domain="science", complexity="high", task_type="research")
        result = engine.route(ctx)
        assert result.skill == "deep-research"

    def test_route_table_direct_action(self, tmp_path: Path):
        """low-complexity query 应返回 direct action."""
        engine = RouterEngine()
        yaml_text = """
routes:
  - match: { task_type: "query", complexity: "low" }
    action: "direct"
"""
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", yaml_text)
        engine.load_routes(file_path)

        ctx = RoutingContext(domain="general", complexity="low", task_type="query")
        result = engine.route(ctx)
        assert result.skill is None
        assert result.action == "direct", f"Expected direct action, got {result.action}"

    def test_route_table_no_match(self, tmp_path: Path):
        """无匹配时应返回 candidates 候选列表."""
        engine = RouterEngine()
        yaml_text = """
routes:
  - match: { domain: "tech", complexity: "high" }
    skill: "pdf"
    channel: "full"
"""
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", yaml_text)
        engine.load_routes(file_path)

        ctx = RoutingContext(domain="business", complexity="low", task_type="query")
        result = engine.route(ctx)
        assert result.skill is None, f"Expected no match, got skill={result.skill}"
        assert len(result.candidates) > 0, "Should return at least one candidate on no-match"

    def test_route_table_yaml_loading(self):
        """从默认 routes.yaml 文件正确加载路由表."""
        routes_path = (
            Path(__file__).resolve().parents[1]
            / "packages"
            / "harness"
            / "deerflow"
            / "skills"
            / "router"
            / "routes.yaml"
        )
        engine = RouterEngine()
        engine.load_routes(str(routes_path))
        assert len(engine.routes) > 0, f"Expected routes, got {len(engine.routes)}"

        # Verify expected route entries exist
        route_skills = set()
        for r in engine.routes:
            if "skill" in r:
                route_skills.add(r["skill"])
        assert "pdf" in route_skills, f"Expected pdf in routes, got {route_skills}"

    def test_route_priority_order(self, tmp_path: Path):
        """路由应按声明顺序匹配（优先匹配第一条）. """
        engine = RouterEngine()
        yaml_text = """
routes:
  - match: { domain: "tech" }
    skill: "pdf"
    channel: "full"
  - match: { domain: "tech", complexity: "high", task_type: "analysis" }
    skill: "deep-research"
"""
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", yaml_text)
        engine.load_routes(file_path)

        ctx = RoutingContext(domain="tech", complexity="high", task_type="analysis")
        result = engine.route(ctx)
        # First matching rule should win (domain: tech)
        assert result.skill == "pdf", f"Expected pdf (first match), got {result.skill}"
        assert result.channel == "full"


# ── Integration Tests ───────────────────────────────────────────────────────


class TestIntegration:
    """Integration: classifier + engine work together."""

    def test_classify_then_route_tech_analysis(self, tmp_path: Path):
        """分类+路由：tech 分析任务 -> pdf/analysis."""
        classifier = SkillClassifier()
        engine = RouterEngine()
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", _ROUTES_SAMPLE)
        engine.load_routes(file_path)

        ctx = classifier.classify("分析一下 AI 芯片的设计架构")
        result = engine.route(ctx)

        assert result.skill == "pdf", f"Expected pdf, got {result.skill}"
        assert result.channel == "analysis", f"Expected analysis, got {result.channel}"

    def test_classify_then_route_simple_query(self, tmp_path: Path):
        """分类+路由：简单天气查询 -> direct."""
        classifier = SkillClassifier()
        engine = RouterEngine()
        yaml_text = """
routes:
  - match: { task_type: "query", complexity: "low" }
    action: "direct"
"""
        file_path = _write_routes_yaml(tmp_path / "routes.yaml", yaml_text)
        engine.load_routes(file_path)

        ctx = classifier.classify("今天天气怎么样")
        result = engine.route(ctx)

        assert result.skill is None
        assert result.action == "direct"
