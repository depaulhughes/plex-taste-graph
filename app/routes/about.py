from collections import Counter
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db import get_connection
from app.graph_builder import apply_resolved_clusters, stats_payload
router = APIRouter()


def default_about_stats() -> Dict[str, Any]:
    return {
        "total_titles": 0,
        "enriched_titles": 0,
        "graph_edges": 0,
        "mapped_titles": 0,
        "outlier_titles": 0,
        "top_clusters": [],
        "cluster_distribution": [],
        "cluster_total": 0,
    }


def about_stats() -> Dict[str, Any]:
    stats = default_about_stats()
    try:
        raw = stats_payload() or {}
    except Exception:
        raw = {}

    stats["total_titles"] = int(raw.get("total_titles", 0) or 0)
    stats["enriched_titles"] = int(raw.get("enriched_titles", 0) or 0)
    stats["graph_edges"] = int(raw.get("graph_edges", 0) or 0)
    stats["top_clusters"] = raw.get("top_clusters") or []

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.primary_cluster, t.enrichment_status, p.tone_tags, p.theme_tags,
                   p.style_tags, p.mood_tags
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            """
        ).fetchall()

    rows = apply_resolved_clusters([dict(row) for row in rows])
    cluster_counter = Counter()
    mapped_titles = 0
    outlier_titles = 0
    for row in rows:
        enriched = row.get("enrichment_status") == "enriched"
        if enriched:
            cluster = row.get("resolved_cluster") or row.get("primary_cluster") or "Mixed / Transitional"
            cluster_counter[cluster] += 1
            mapped_titles += 1
        else:
            cluster_counter["Mixed / Transitional"] += 1
        if row.get("is_outlier"):
            outlier_titles += 1

    cluster_total = sum(cluster_counter.values())
    ordered = cluster_counter.most_common()
    top = ordered[:8]
    other = max(0, cluster_total - sum(count for _, count in top))
    distribution = [{"cluster": cluster, "count": count} for cluster, count in top]
    if other:
        distribution.append({"cluster": "Other", "count": other})
    stats["mapped_titles"] = mapped_titles
    stats["outlier_titles"] = outlier_titles
    stats["cluster_distribution"] = distribution
    stats["cluster_total"] = cluster_total
    return stats


@router.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        "about.html",
        {"request": request, "stats": about_stats()},
    )
