from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.db import get_connection
from app.graph_builder import apply_resolved_clusters
from app.models import json_list

router = APIRouter()


def list_titles() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.johnny_core_score
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
    rows = apply_resolved_clusters(rows)
    for row in rows:
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        row["top_tags"] = tags[:5]
        row["source"] = row.get("source") or "manual"
        row["enrichment_status"] = row.get("enrichment_status") or "pending"
        row["primary_cluster"] = row.get("resolved_cluster") or row.get("primary_cluster") or ("Pending enrichment" if row["enrichment_status"] != "enriched" else "Mixed / Transitional")
    return rows


@router.get("/library", response_class=HTMLResponse)
def library(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        "library.html",
        {"request": request, "titles": list_titles()},
    )


@router.get("/title/{title_id}", response_class=HTMLResponse)
def title_detail(title_id: int, request: Request) -> HTMLResponse:
    data = title_payload(title_id)
    return request.app.state.templates.TemplateResponse(
        "title.html",
        {"request": request, **data},
    )


@router.get("/api/titles")
def api_titles() -> list[dict]:
    return list_titles()


@router.get("/api/title/{title_id}")
def api_title(title_id: int) -> dict:
    return title_payload(title_id)


def title_payload(title_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT t.*, p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.intensity_score, p.weirdness_score, p.emotional_weight_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary,
                   p.recommendation_hooks, p.closest_viewing_context
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id = ?
            """,
            (title_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Title not found")
        edges = conn.execute(
            """
            SELECT e.*, s.title AS source_title, t.title AS target_title
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            WHERE e.source_title_id = ? OR e.target_title_id = ?
            ORDER BY e.edge_type ASC, e.confidence DESC, e.weight DESC
            """,
            (title_id, title_id),
        ).fetchall()
    row = apply_resolved_clusters([row])[0]
    row["genres"] = json_list(row.get("genres"))
    row["directors"] = json_list(row.get("directors"))
    row["writers"] = json_list(row.get("writers"))
    row["actors"] = json_list(row.get("actors"))
    row["tags"] = []
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
        row[key] = json_list(row.get(key))
        row["tags"].extend(row[key])
    row["recommendation_hooks"] = json_list(row.get("recommendation_hooks"))
    row["closest_viewing_context"] = row.get("closest_viewing_context") or ""
    row["primary_cluster"] = row.get("resolved_cluster") or row.get("primary_cluster") or ("Pending enrichment" if row.get("enrichment_status") != "enriched" else "Mixed / Transitional")
    for edge in edges:
        edge["nearby_title"] = edge["target_title"] if edge["source_title_id"] == title_id else edge["source_title"]
        edge["nearby_id"] = edge["target_title_id"] if edge["source_title_id"] == title_id else edge["source_title_id"]
        edge["shared_traits"] = json_list(edge.get("shared_traits"))
    strong_edges = [edge for edge in edges if edge.get("edge_type", "strong") == "strong"]
    soft_edges = [edge for edge in edges if edge.get("edge_type") == "soft"]
    recommendations = build_title_recommendations(row, edges)
    return {
        "title": row,
        "edges": edges,
        "strong_edges": strong_edges,
        "soft_edges": soft_edges,
        "recommendations": recommendations,
    }


def build_title_recommendations(title: dict, edges: list[dict]) -> dict:
    connected_ids = [edge["nearby_id"] for edge in edges]
    if not connected_ids:
        return {"closest": [], "weirder": [], "heavier": [], "safer": []}
    placeholders = ",".join("?" for _ in connected_ids)
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.id, t.title, t.year, t.primary_cluster, p.weirdness_score,
                   p.emotional_weight_score, p.pacing_score, p.johnny_core_score
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id IN ({placeholders})
            """,
            connected_ids,
        ).fetchall()
    rows = apply_resolved_clusters(rows)
    by_id = {row["id"]: row for row in rows}
    closest = [by_id[edge["nearby_id"]] for edge in edges if edge["nearby_id"] in by_id][:5]
    base_weird = int(title.get("weirdness_score") or 0)
    base_emotion = int(title.get("emotional_weight_score") or 0)
    weirder = sorted([row for row in rows if int(row["weirdness_score"]) >= base_weird], key=lambda row: row["weirdness_score"], reverse=True)[:4]
    heavier = sorted([row for row in rows if int(row["emotional_weight_score"]) >= base_emotion], key=lambda row: row["emotional_weight_score"], reverse=True)[:4]
    safer = sorted(rows, key=lambda row: (row["pacing_score"], -row["weirdness_score"]), reverse=True)[:4]
    return {"closest": closest, "weirder": weirder, "heavier": heavier, "safer": safer}
