from typing import Literal

from langchain.tools import tool


@tool("log_decision", parse_docstring=True)
def log_decision_tool(
    decision_type: Literal[
        "approach_choice",
        "risk_assessment",
        "tradeoff",
        "route_selection",
    ],
    summary: str,
    reasoning: str,
    alternatives: list[str] | None = None,
) -> str:
    """Record an autonomous decision for asynchronous human review.

    Use this tool when you need to make a decision autonomously and want
    to log your reasoning so the human can review it later. Unlike
    ``ask_clarification``, this tool does NOT interrupt execution — it
    simply records the decision for post-hoc review.

    When to use log_decision:
    - You chose one approach over others (e.g., which library, which algorithm)
    - You assessed a risk and decided it was acceptable
    - You made a tradeoff (e.g., performance vs readability)
    - You selected a route or direction for execution

    Best practices:
    - Be specific in your summary — the human should understand what was decided
    - Explain your reasoning clearly so the decision is reviewable
    - List alternatives you considered and why you rejected them
    - Log early for significant decisions, not for every minor step

    Args:
        decision_type: Category of the decision being made.
        summary: A brief one-line summary of what was decided.
        reasoning: Detailed explanation of the reasoning behind the decision.
        alternatives: Optional list of alternatives that were considered and rejected.
    """
    parts = [f"[{decision_type}] {summary}", f"Reasoning: {reasoning}"]
    if alternatives:
        parts.append(f"Alternatives considered: {'; '.join(alternatives)}")
    return "\n".join(parts)
