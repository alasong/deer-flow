"""Tests for the Metaso web search tool."""

import json
import os
from unittest.mock import patch

import pytest

from deerflow.community.metaso.tools import METASO_BASE_URL, web_search_tool


@pytest.fixture(autouse=True)
def _mock_http():
    """Mock httpx.Client so no test makes a real HTTP request."""
    with patch("deerflow.community.metaso.tools.httpx.Client") as mock:
        yield mock


@pytest.fixture(autouse=True)
def _mock_tool_config():
    """Return None from get_tool_config so config doesn't override test params."""
    with patch("deerflow.community.metaso.tools.get_app_config") as mock_cfg:
        mock_cfg.return_value.get_tool_config.return_value = None
        yield


def _invoke(query: str, max_results: int = 5, api_key: str = "mk-test-key"):
    """Helper: invoke the tool with a test API key."""
    with patch.dict(os.environ, {"METASO_API_KEY": api_key}):
        return json.loads(web_search_tool.invoke({"query": query, "max_results": max_results}))


def _setup_mock(mock_http, references: list | None = None, text: str = ""):
    """Helper: configure the httpx mock response."""
    mock_instance = mock_http.return_value.__enter__.return_value
    mock_instance.post.return_value.status_code = 200
    mock_instance.post.return_value.json.return_value = {
        "errCode": 0,
        "data": {
            "resultId": "abc123",
            "references": references or [],
            "text": text,
        },
    }


def test_returns_unavailable_when_no_api_key(_mock_http):
    data = _invoke("test query", api_key="")
    assert "error" in data
    assert "METASO_API_KEY" in data["error"]


def test_handles_http_error(_mock_http):
    mock_instance = _mock_http.return_value.__enter__.return_value
    mock_instance.post.side_effect = Exception("Connection error")

    data = _invoke("test query")
    assert "error" in data
    assert "Connection error" in data["error"]


def test_handles_api_error_response(_mock_http):
    mock_instance = _mock_http.return_value.__enter__.return_value
    mock_instance.post.return_value.status_code = 200
    mock_instance.post.return_value.json.return_value = {
        "errCode": 40001,
        "errMsg": "Invalid API key",
    }

    data = _invoke("test query")
    assert "error" in data
    assert "Invalid API key" in data["error"]


def test_parses_response_with_references_and_answer(_mock_http):
    refs = [
        {"title": "Result One", "link": "https://example.com/1", "date": "2025年01月01日"},
        {"title": "Result Two", "link": "https://example.com/2", "date": "2025年02月01日"},
    ]
    _setup_mock(_mock_http, refs, text="Summary: result one and result two are examples.")

    data = _invoke("test query")
    assert data["query"] == "test query"
    assert data["total_results"] == 2
    assert len(data["results"]) == 2
    assert data["results"][0]["title"] == "Result One"
    assert data["results"][0]["url"] == "https://example.com/1"
    assert data["results"][0]["date"] == "2025年01月01日"
    assert data["results"][1]["title"] == "Result Two"
    assert data["answer"] == "Summary: result one and result two are examples."


def test_respects_max_results_param(_mock_http):
    refs = [{"title": f"Result {i}", "link": f"https://example.com/{i}"} for i in range(10)]
    _setup_mock(_mock_http, refs)

    data = _invoke("test", max_results=3)
    assert data["total_results"] == 3
    assert len(data["results"]) == 3


def test_omits_answer_when_not_present(_mock_http):
    refs = [{"title": "No Answer", "link": "https://example.com/no-answer"}]
    _setup_mock(_mock_http, refs)

    data = _invoke("no answer test")
    assert data["total_results"] == 1
    assert "answer" not in data


def test_base_url_constant():
    assert METASO_BASE_URL == "https://metaso.cn"
