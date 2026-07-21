"""Tests for Jina AI web_fetch tool - API key handling."""

import os
from unittest.mock import MagicMock, patch

import pytest

from deerflow.community.jina_ai.jina_client import JinaClient


class TestJinaClientNoApiKey:
    """Tests for JinaClient.crawl() when JINA_API_KEY is not configured."""

    async def test_crawl_returns_unavailable_when_no_api_key(self):
        """crawl() should return UNAVAILABLE message without making HTTP requests when JINA_API_KEY is unset."""
        client = JinaClient()
        with patch.dict(os.environ, {}, clear=True):
            result = await client.crawl("https://example.com")

        assert result.startswith("UNAVAILABLE:")
        assert "JINA_API_KEY" in result
        assert "not configured" in result

    async def test_crawl_still_works_when_api_key_is_set(self):
        """crawl() should NOT short-circuit when JINA_API_KEY is set (no UNAVAILABLE prefix)."""
        client = JinaClient()
        with patch.dict(os.environ, {"JINA_API_KEY": "test-key-123"}):
            result = await client.crawl("https://example.com")

        # With a real API key set, the crawl should proceed to make an HTTP request.
        # Since we haven't mocked httpx, the request will actually fail, but it should NOT
        # return "UNAVAILABLE" — it should return something else (e.g. a network error or a 401).
        assert not result.startswith("UNAVAILABLE:")


class TestWebFetchToolNoApiKey:
    """Tests for web_fetch_tool behavior when JINA_API_KEY is not configured."""

    @pytest.fixture(autouse=True)
    def _mock_app_config(self):
        """Mock get_app_config to avoid env var resolution in config loading."""
        with patch("deerflow.community.jina_ai.tools.get_app_config") as mock:
            mock_config = MagicMock()
            mock_config.get_tool_config.return_value = None
            mock.return_value = mock_config
            yield

    async def test_tool_returns_unavailable_when_no_api_key(self):
        """web_fetch_tool should surface the UNAVAILABLE message from JinaClient."""
        from deerflow.community.jina_ai.tools import web_fetch_tool

        with patch.dict(os.environ, {}, clear=True):
            result = await web_fetch_tool.ainvoke({"url": "https://example.com"})

        assert result.startswith("UNAVAILABLE:")
        assert "JINA_API_KEY" in result

    async def test_tool_does_not_extract_readability_on_unavailable(self):
        """web_fetch_tool should return the UNAVAILABLE message directly, not attempt readability extraction."""
        from deerflow.community.jina_ai.tools import web_fetch_tool

        with patch.dict(os.environ, {}, clear=True):
            result = await web_fetch_tool.ainvoke({"url": "https://example.com"})

        # The result should be the raw UNAVAILABLE string, not a markdown article
        assert result == "UNAVAILABLE: JINA_API_KEY not configured. Set JINA_API_KEY env var to use this tool."
