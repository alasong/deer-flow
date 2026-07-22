"""Skill Classifier — extracts routing signals from free text.

Pure functions, no IO, no external dependencies. Designed to be testable without
mock infrastructure.

The classification logic is extracted from the PDF engine's channel auto-detection
(``skills/public/pdf/tools/pdf_engine_channel.py``) so it can be reused across
skills and workflows. The original used ``_OUTPUT_KIND_KEYWORDS`` and
``_auto_classify_output_kind`` to map task text to channel types; this module
extends the same approach into a general-purpose classifier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


# ── Domain keyword maps ──────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "tech": [
        "AI",
        "人工智能",
        "芯片",
        "chip",
        "software",
        "code",
        "代码",
        "algorithm",
        "算法",
        "programming",
        "编程",
        "backend",
        "frontend",
        "api",
        "database",
        "数据库",
        "data",
        "数据",
        "cloud",
        "云",
        "devops",
        "architecture",
        "架构",
        "server",
        "服务器",
        "network",
        "网络",
        "python",
        "docker",
        "kubernetes",
        "k8s",
        "machine learning",
        "deep learning",
        "neural",
        "神经网络",
        "model",
        "模型",
        "training",
        "训练",
        "inference",
        "推理",
        "langchain",
        "langgraph",
        "agent",
        "llm",
        "gpt",
    ],
    "science": [
        "research",
        "研究",
        "science",
        "科学",
        "physics",
        "物理",
        "chemistry",
        "化学",
        "biology",
        "生物",
        "experiment",
        "实验",
        "theory",
        "理论",
        "laboratory",
        "lab",
        "实验室",
        "mathematics",
        "数学",
        "equation",
        "公式",
        "statistics",
        "统计",
    ],
    "business": [
        "business",
        "商业",
        "market",
        "市场",
        "strategy",
        "战略",
        "revenue",
        "收入",
        "profit",
        "利润",
        "customer",
        "客户",
        "product",
        "产品",
        "sales",
        "销售",
        "marketing",
        "营销",
        "investment",
        "投资",
        "finance",
        "金融",
        "startup",
        "创业",
        "growth",
        "增长",
    ],
}

# ── Task type keywords (extracted from PDF engine's _OUTPUT_KIND_KEYWORDS) ───

_TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "analysis": [
        "分析",
        "研究",
        "audit",
        "review",
        "investigate",
        "analyze",
        "research",
        "评估",
        "评估报告",
        "diagnose",
        "诊断",
    ],
    "planning": [
        "规划",
        "计划",
        "需求",
        "roadmap",
        "planning",
        "requirement",
        "sprint",
        "backlog",
        "milestone",
        "路线图",
        "迭代",
        "排期",
    ],
    "implementation": [
        "实现",
        "开发",
        "implement",
        "build",
        "create",
        "写",
        "编写",
        "code",
        "编码",
        "开发",
        "修复",
        "fix",
        "bug",
        "feature",
        "功能",
    ],
    "research": [
        "调研",
        "调查",
        "研究",
        "survey",
        "literature",
        "文献",
        "对比",
        "比较",
        "comparison",
        "comparative",
        "最新",
        "趋势",
        "trend",
        "state of the art",
        "sota",
    ],
    "query": [],  # no explicit keywords; used as fallback
}


@dataclass
class RoutingContext:
    """Classification result — signals for route table matching.

    Attributes:
        domain: High-level domain (tech, science, business, general).
        complexity: Task complexity (low, medium, high).
        task_type: Type of task (analysis, planning, implementation, research, query).
        keywords: List of keyword patterns that matched during classification.
    """

    domain: str = "general"
    complexity: str = "medium"
    task_type: str = "query"
    keywords: list[str] = field(default_factory=list)


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    """Count how many keywords appear in the text (case-insensitive)."""
    lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in lower)


def _match_domain(text_lower: str, ctx_keywords: list[str]) -> str:
    """Determine the domain by counting keyword matches per domain category.

    Returns "general" when no domain has >= 1 keyword hit.
    """
    best_domain = "general"
    best_count = 0
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        count = 0
        for kw in keywords:
            if kw.lower() in text_lower:
                count += 1
                matched = kw
                if matched not in ctx_keywords:
                    ctx_keywords.append(matched)
        if count > best_count:
            best_count = count
            best_domain = domain
    return best_domain


def _match_task_type(text_lower: str, ctx_keywords: list[str]) -> str:
    """Determine the task type by counting keyword matches per category.

    Uses the task-type keyword maps derived from the PDF engine's
    ``_OUTPUT_KIND_KEYWORDS``. Returns "query" when no category has hits.
    """
    best_type = "query"
    best_count = 0
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        if not keywords:
            continue
        count = 0
        for kw in keywords:
            if kw.lower() in text_lower:
                count += 1
                matched = kw
                if matched not in ctx_keywords:
                    ctx_keywords.append(matched)
        if count > best_count:
            best_count = count
            best_type = task_type
    return best_type


def _classify_complexity(
    text_lower: str,
    domain: str,
    task_type: str,
    keyword_count: int,
) -> str:
    """Classify task complexity based on signals.

    Rules:
    - high: ≥ 3 domain/task-type keyword hits, or domain is tech/science
      with task_type analysis/research/implementation.
    - low: ≤ 1 keyword hit, short text (< 20 chars), no complex task type.
    - medium: everything else.
    """
    # Low complexity signals
    if keyword_count <= 1 and len(text_lower) < 20 and task_type == "query":
        return "low"

    # High complexity signals
    if keyword_count >= 3:
        return "high"
    if domain in ("tech", "science") and task_type in ("analysis", "implementation", "research"):
        return "high"
    if task_type == "implementation":
        return "high"

    # Medium — default
    return "medium"


class SkillClassifier:
    """Classifies free-text input into a RoutingContext.

    Pure function — no IO, no state mutation across calls. Thread-safe.

    Usage::

        classifier = SkillClassifier()
        ctx = classifier.classify("分析一下 AI 芯片")
        # ctx.domain == "tech"
        # ctx.complexity == "high"
        # ctx.task_type == "analysis"
        # ctx.keywords == ["AI", "芯片", "分析"]
    """

    def classify(self, text: str) -> RoutingContext:
        """Classify a free-text input into routing signals.

        Args:
            text: Free-form user input (e.g. task description or query).

        Returns:
            A RoutingContext with domain, complexity, task_type, and keywords.
        """
        text_lower = text.lower()
        keywords: list[str] = []

        domain = _match_domain(text_lower, keywords)
        task_type = _match_task_type(text_lower, keywords)
        complexity = _classify_complexity(text_lower, domain, task_type, len(keywords))

        return RoutingContext(
            domain=domain,
            complexity=complexity,
            task_type=task_type,
            keywords=keywords,
        )
