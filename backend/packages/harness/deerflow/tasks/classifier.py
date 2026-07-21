from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Classification:
    """Result of classifying a task description.

    Attributes:
        skill: The selected skill name (e.g. ``"pdf"``, ``"pff"``).
        channel: The selected channel/blueprint (e.g. ``"analysis"``, ``"full"``).
        confidence: Confidence score between 0 and 1.
        reasoning: Human-readable explanation of the classification.
    """

    skill: str
    channel: str = "standard"
    confidence: float = 0.5
    reasoning: str = ""


_DEFAULT_MAP: list[tuple[str, str, str, list[str]]] = [
    # (skill, channel, description, keywords)
    ("pdf", "analysis", "调研/分析/研究", ["调研", "分析", "研究", "调查", "research", "analysis"]),
    ("pdf", "planning", "规划/设计", ["规划", "设计", "架构", "方案", "plan", "design", "architecture"]),
    ("pdf", "full", "复杂多步骤实现", ["实现", "开发", "构建", "implement", "develop", "build"]),
    ("pdf", "standard", "标准实现", ["修复", "修改", "添加", "fix", "add", "update", "change"]),
    ("pdf", "lite", "简单快速任务", ["简单", "快速", "simple", "quick", "tiny"]),
]


def classify_task(description: str, keyword_map: list[tuple[str, str, str, list[str]]] | None = None) -> Classification:
    """Classify a task description to select skill and channel.

    This is a pure keyword-based classifier. It matches the description
    against keyword lists to determine the best skill+channel combination.
    Returns a default classification if no keywords match.

    Args:
        description: Natural language task description.
        keyword_map: Optional custom keyword map. Each entry is
            ``(skill, channel, description, keywords)``.

    Returns:
        A Classification with skill, channel, and confidence.
    """
    if keyword_map is None:
        keyword_map = _DEFAULT_MAP

    desc_lower = description.lower()
    best: Classification | None = None

    for skill, channel, _desc, keywords in keyword_map:
        matches = sum(1 for kw in keywords if kw.lower() in desc_lower)
        if matches > 0:
            confidence = min(0.5 + matches * 0.15, 0.95)
            candidate = Classification(
                skill=skill,
                channel=channel,
                confidence=confidence,
                reasoning=f"Matched {matches} keyword(s) for {skill}/{channel}",
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate

    if best is None:
        return Classification(
            skill="pdf",
            channel="standard",
            confidence=0.3,
            reasoning="No keywords matched; defaulting to pdf/standard",
        )

    return best
