import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.graph_builder import graph_payload
from app.suggestions import selected_title_suggested_asks, suggested_asks

router = APIRouter()
logger = logging.getLogger("taste_graph.graph")


def _graph_cache_signature() -> tuple[tuple[str, float, int], ...]:
    settings = get_settings()
    base = settings.sqlite_path
    related = [base, Path(f"{base}-wal"), Path(f"{base}-shm")]
    signature: list[tuple[str, float, int]] = []
    for path in related:
        if path.exists():
            stat = path.stat()
            signature.append((str(path), stat.st_mtime, stat.st_size))
    return tuple(signature)


@router.get("/graph", response_class=HTMLResponse)
def graph_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse("graph.html", {"request": request})


@router.get("/api/graph")
def api_graph(request: Request) -> dict:
    started = time.perf_counter()
    signature = _graph_cache_signature()
    cache = getattr(request.app.state, "graph_cache", None)
    if cache and cache.get("signature") == signature:
        payload = cache["payload"]
        total_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "graph_api cache=hit nodes=%s edges=%s total_ms=%.2f",
            len(payload.get("nodes", [])),
            len(payload.get("edges", [])),
            total_ms,
        )
        return payload

    query_started = time.perf_counter()
    payload = graph_payload()
    payload.setdefault("meta", {})["cached"] = False
    query_ms = (time.perf_counter() - query_started) * 1000
    request.app.state.graph_cache = {
        "signature": signature,
        "payload": payload,
        "cached_at": time.time(),
    }
    total_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "graph_api cache=miss nodes=%s edges=%s graph_api_query_ms=%.2f graph_api_total_ms=%.2f",
        len(payload.get("nodes", [])),
        len(payload.get("edges", [])),
        query_ms,
        total_ms,
    )
    return payload


@router.get("/api/suggested-asks")
def api_suggested_asks(selected_title_id: Optional[int] = Query(default=None)) -> dict:
    if isinstance(selected_title_id, int) and selected_title_id:
        return selected_title_suggested_asks(selected_title_id)
    return suggested_asks()
