"""
Web Search Tool - Search the web using Metaso API (秘塔AI搜索).
Requires METASO_API_KEY environment variable.
"""

import json
import logging
import os

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

METASO_BASE_URL = "https://metaso.cn"
DEFAULT_MAX_RESULTS = 5


@tool("web_search", parse_docstring=True)
def web_search_tool(
    query: str,
    max_results: int = 5,
) -> str:
    """Search the web for information using Metaso AI Search. Use this tool to find current information, news, articles, and facts from the internet.

    Args:
        query: Search keywords describing what you want to find. Be specific for better results.
        max_results: Maximum number of results to return. Default is 5.
    """
    api_key = os.environ.get("METASO_API_KEY")
    if not api_key:
        return json.dumps(
            {"error": "METASO_API_KEY not configured. Set METASO_API_KEY env var to use this tool.", "query": query},
            ensure_ascii=False,
        )

    config = get_app_config().get_tool_config("web_search")
    if config is not None:
        max_results = config.model_extra.get("max_results", max_results)

    try:
        return _search_metaso(query, max_results, api_key)
    except Exception as e:
        logger.exception(f"Metaso search failed: {e}")
        return json.dumps({"error": f"Metaso search failed: {e}", "query": query}, ensure_ascii=False)


def _search_metaso(query: str, max_results: int, api_key: str) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "question": query,
        "stream": False,
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(
            f"{METASO_BASE_URL}/api/open/search/v2",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

    if body.get("errCode") not in (0, None, ""):
        return json.dumps(
            {"error": f"Metaso API error: {body.get('errMsg', body.get('errCode', 'unknown'))}", "query": query},
            ensure_ascii=False,
        )

    data = body.get("data", {})
    references = data.get("references") or []
    answer_text = data.get("text", "")

    results = []
    for ref in references[:max_results]:
        entry = {
            "title": ref.get("title", ""),
            "url": ref.get("link", ""),
        }
        if ref.get("date"):
            entry["date"] = ref["date"]
        results.append(entry)

    output = {
        "query": query,
        "total_results": len(results),
        "results": results,
    }
    if answer_text:
        output["answer"] = answer_text

    return json.dumps(output, indent=2, ensure_ascii=False)
