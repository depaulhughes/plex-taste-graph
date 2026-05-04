import logging
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.graph_builder import get_home_insight_sections, stats_payload

router = APIRouter()
logger = logging.getLogger("taste_graph.home")


def default_home_stats() -> Dict[str, Any]:
    return {
        "total_titles": 0,
        "enriched_titles": 0,
        "pending_titles": 0,
        "graph_edges": 0,
        "average_scores": {
            "johnny_core": 0.0,
            "weirdness": 0.0,
            "emotional_weight": 0.0,
        },
        "top_signal_tags": [],
        "top_clusters": [],
        "taste_extremes": {
            "johnny_core": None,
            "weirdness": None,
            "emotional_weight": None,
        },
        "taste_extremes_ranked": {
            "johnny_core": [],
            "weirdness": [],
            "emotional_weight": [],
        },
        "recent_titles": [],
        "insight_sections": [],
    }


def fallback_insight_sections(raw: Dict[str, Any]) -> list[Dict[str, Any]]:
    ranked = raw.get("taste_extremes_ranked") or {}
    sections = []
    for key, title, subtitle in (
        ("johnny_core", "Top Johnny-core titles", "Titles closest to the center of your current taste gravity."),
        ("weirdness", "Weirdest titles", "The most off-center titles in the current library."),
        ("emotional_weight", "Most emotionally heavy titles", "The heaviest titles in the current library."),
    ):
        items = []
        for item in ranked.get(key) or []:
            items.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "year": item.get("year"),
                "source": item.get("source") or "manual",
                "enrichment_status": "enriched",
                "primary_cluster": item.get("primary_cluster") or "Outliers",
                "display_cluster": item.get("primary_cluster") or "Outliers",
                "johnny_core_score": item.get("johnny_core_score"),
                "weirdness_score": item.get("weirdness_score"),
                "emotional_weight_score": item.get("emotional_weight_score"),
                "has_scores": True,
                "url": f"/graph?title_id={item.get('id')}",
            })
            if len(items) >= 6:
                break
        if items:
            sections.append({
                "title": title,
                "subtitle": subtitle,
                "type": "fallback",
                "items": items,
            })
    return sections


def normalized_home_stats() -> Dict[str, Any]:
    stats = default_home_stats()
    try:
        raw = stats_payload() or {}
    except Exception:
        return stats

    stats.update({
        "total_titles": raw.get("total_titles", 0) or 0,
        "enriched_titles": raw.get("enriched_titles", 0) or 0,
        "pending_titles": raw.get("pending_titles", 0) or 0,
        "graph_edges": raw.get("graph_edges", 0) or 0,
        "top_signal_tags": raw.get("top_signal_tags") or [],
        "top_clusters": raw.get("top_clusters") or [],
        "recent_titles": raw.get("recent_titles") or [],
    })
    average_scores = raw.get("average_scores") or {}
    stats["average_scores"] = {
        "johnny_core": float(average_scores.get("johnny_core", 0) or 0),
        "weirdness": float(average_scores.get("weirdness", 0) or 0),
        "emotional_weight": float(average_scores.get("emotional_weight", 0) or 0),
    }
    taste_extremes = raw.get("taste_extremes") or {}
    stats["taste_extremes"] = {
        "johnny_core": taste_extremes.get("johnny_core"),
        "weirdness": taste_extremes.get("weirdness"),
        "emotional_weight": taste_extremes.get("emotional_weight"),
    }
    taste_extremes_ranked = raw.get("taste_extremes_ranked") or {}
    stats["taste_extremes_ranked"] = {
        "johnny_core": taste_extremes_ranked.get("johnny_core") or [],
        "weirdness": taste_extremes_ranked.get("weirdness") or [],
        "emotional_weight": taste_extremes_ranked.get("emotional_weight") or [],
    }
    try:
        stats["insight_sections"] = get_home_insight_sections(limit=4)
    except Exception as exc:
        logger.exception("home insight section generation failed: %s", exc)
        stats["insight_sections"] = []
    if not stats["insight_sections"]:
        stats["insight_sections"] = fallback_insight_sections(raw)
        logger.warning(
            "home insight sections fell back to deterministic shelves: count=%s enriched=%s total=%s",
            len(stats["insight_sections"]),
            stats["enriched_titles"],
            stats["total_titles"],
        )
    return stats


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        "index.html",
        {"request": request, "stats": normalized_home_stats()},
    )
