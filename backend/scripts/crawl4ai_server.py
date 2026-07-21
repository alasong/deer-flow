"""Crawl4AI local server — wraps AsyncWebCrawler as a FastAPI service.

Used by DeerFlow's web_fetch tool (crawl4ai community provider).
Started as a background service by ``scripts/serve.sh`` (``make dev``).

API
---

``GET /health`` → ``{"status": "ok"}``

``POST /md`` → ``{"success": true, "markdown": "..."}``
    Request body: ``{"url": "...", "f": "fit|raw|bm25|llm"}``
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("crawl4ai-server")

_crawler: AsyncWebCrawler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _crawler
    from crawl4ai import AsyncWebCrawler

    logger.info("Starting crawl4ai browser...")
    _crawler = AsyncWebCrawler()
    await _crawler.__aenter__()
    logger.info("crawl4ai browser ready")
    yield
    if _crawler is not None:
        await _crawler.__aexit__(None, None, None)
    logger.info("crawl4ai browser shut down")


app = FastAPI(title="crawl4ai-server", version="1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/md")
async def fetch_markdown(request: Request) -> JSONResponse:
    global _crawler
    if _crawler is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "crawler not initialized"},
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "invalid JSON body"},
        )

    url = body.get("url", "")
    if not url:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "missing 'url' in request body"},
        )

    filter_mode = body.get("f", "fit")
    try:
        result = await _crawler.arun(
            url=url,
            word_count_threshold=10,
            extraction_strategy=None,
            chunking_strategy=None,
            markdown_generator=None,
            css_selector=None,
            bypass_cache=True,
            verbose=False,
        )
    except Exception as exc:
        logger.error("crawl failed for %s: %s", url, exc)
        return JSONResponse(
            content={"success": False, "markdown": "", "error": str(exc)},
        )

    if not result.success:
        return JSONResponse(
            content={"success": False, "markdown": "", "error": result.error_message or "crawl failed"},
        )

    markdown = ""
    if filter_mode == "raw":
        markdown = result.markdown or ""
    elif filter_mode == "fit":
        markdown = result.fit_markdown or result.markdown or ""
    else:
        markdown = result.markdown or ""

    return JSONResponse(content={"success": True, "markdown": markdown})


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=11235, log_level="info")


if __name__ == "__main__":
    main()
