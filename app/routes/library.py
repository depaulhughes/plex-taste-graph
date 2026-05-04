from collections import Counter
from statistics import mean

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.db import get_connection
from app.graph_neighbors import get_title_graph_neighbors
from app.graph_builder import apply_resolved_clusters, get_library_titles
from app.models import json_list

router = APIRouter()


def list_titles() -> list[dict]:
    return get_library_titles()


def library_filters_payload(titles: list[dict]) -> dict:
    cluster_counts = Counter(title["cluster"] for title in titles if title.get("cluster"))
    tag_counts = Counter()
    media_type_counts = Counter(title["media_type"] for title in titles if title.get("media_type"))
    source_counts = Counter(title["source"] for title in titles if title.get("source"))
    for title in titles:
        for tag in title.get("display_tags", []):
            tag_counts[tag] += 1
    return {
        "clusters": [{"label": cluster, "count": cluster_counts[cluster]} for cluster in sorted(cluster_counts.keys())],
        "top_tags": [tag for tag, _ in tag_counts.most_common(14)],
        "media_types": [{"label": key, "count": media_type_counts[key]} for key in sorted(media_type_counts.keys())],
        "sources": [{"label": key, "count": source_counts[key]} for key in sorted(source_counts.keys())],
        "counts": {
            "total": len(titles),
            "outliers": sum(1 for title in titles if title.get("is_outlier")),
            "mapped": sum(1 for title in titles if not title.get("is_outlier")),
            "pending": sum(1 for title in titles if title.get("status") != "enriched"),
        },
    }


@router.get("/library", response_class=HTMLResponse)
def library(request: Request) -> HTMLResponse:
    titles = list_titles()
    return request.app.state.templates.TemplateResponse(
        "library.html",
        {
            "request": request,
            "titles": titles,
            "library_filters": library_filters_payload(titles),
        },
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
    row["display_tags"] = build_display_tags(row)
    neighbor_data = get_title_graph_neighbors(title_id)
    edges = neighbor_data["edges"]
    strong_edges = neighbor_data["strong_edges"]
    soft_edges = neighbor_data["soft_edges"]
    bridge_edges = neighbor_data["bridge_edges"]
    secondary_edges = soft_edges + bridge_edges
    recommendations = build_title_recommendations(row, edges)
    why_it_lands_here = build_why_it_lands_here(row)
    signal_breakdown = build_signal_breakdown(row)
    graph_insight = build_graph_insight(strong_edges)
    metadata_quality = {
        "enriched": row.get("enrichment_status") == "enriched",
        "mapped": not bool(row.get("is_outlier")),
        "connection_count": len(edges),
        "strong_count": len(strong_edges),
        "soft_count": len(secondary_edges),
    }
    return {
        "title": row,
        "edges": edges,
        "strong_edges": strong_edges,
        "soft_edges": soft_edges,
        "bridge_edges": bridge_edges,
        "secondary_edges": secondary_edges,
        "recommendations": recommendations,
        "why_it_lands_here": why_it_lands_here,
        "signal_breakdown": signal_breakdown,
        "graph_insight": graph_insight,
        "metadata_quality": metadata_quality,
    }


def build_display_tags(row: dict) -> list[str]:
    values: list[str] = []
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
        values.extend([str(item).strip() for item in row.get(key, []) if str(item).strip()])
    context = str(row.get("closest_viewing_context") or "").strip()
    if context:
        values.extend([part.strip() for part in context.replace(";", ",").split(",") if part.strip()])
    deduped: list[str] = []
    seen = set()
    for value in values:
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value)
    if deduped:
        return deduped[:5]
    fallback = str(row.get("primary_cluster") or "").strip()
    return [fallback] if fallback else []


def build_why_it_lands_here(row: dict) -> str:
    cluster = row.get("primary_cluster") or "this part of the map"
    evidence = build_display_tags(row)
    if evidence:
        return f"This title lands in {cluster} because its enrichment keeps pointing toward {', '.join(evidence[:4])}."
    return f"This title lands in {cluster} because its score profile and nearby graph relationships pull it into that neighborhood."


def build_signal_breakdown(row: dict) -> list[dict[str, str]]:
    tone = ", ".join(row.get("tone_tags", [])[:2]) or "its immediate tone"
    mood = ", ".join(row.get("mood_tags", [])[:2]) or "its mood profile"
    theme = ", ".join(row.get("theme_tags", [])[:2]) or "its thematic pressure"
    return [
        {
            "label": "Johnny-core",
            "value": f"{row.get('johnny_core_score') or 0}/10",
            "description": f"How strongly it pulls toward your core taste gravity through {tone}.",
        },
        {
            "label": "Weirdness",
            "value": f"{row.get('weirdness_score') or 0}/10",
            "description": f"How off-center or formally strange it feels because of {mood}.",
        },
        {
            "label": "Emotional weight",
            "value": f"{row.get('emotional_weight_score') or 0}/10",
            "description": f"How much pressure it carries through {theme}.",
        },
    ]


def build_graph_insight(strong_edges: list[dict]) -> dict:
    confidences = [float(edge.get("confidence") or edge.get("weight") or 0) for edge in strong_edges]
    shared_terms = Counter()
    cluster_counts = Counter()
    for edge in strong_edges:
        cluster = str(edge.get("nearby_cluster") or edge.get("target_cluster") or edge.get("source_cluster") or "").strip()
        if cluster:
            cluster_counts[cluster] += 1
        for trait in edge.get("shared_traits", [])[:5]:
            value = str(trait).strip()
            if value:
                shared_terms[value] += 1
        explanation = str(edge.get("explanation") or "").strip()
        if explanation:
            for part in explanation.replace(";", ",").split(","):
                value = part.strip()
                if 3 <= len(value) <= 40:
                    shared_terms[value] += 1

    top_terms = [term for term, _ in shared_terms.most_common(5)]
    top_clusters = [{"label": cluster, "count": count} for cluster, count in cluster_counts.most_common(4)]
    highest_match = strong_edges[0] if strong_edges else None
    lowest_match = strong_edges[-1] if strong_edges else None

    if top_terms:
        summary = f"These neighbors mostly connect through {', '.join(top_terms[:4])}."
    elif strong_edges:
        summary = "These neighbors represent the most trusted graph neighborhood currently attached to this title."
    else:
        summary = "No trusted graph neighborhood has formed yet."

    return {
        "strong_match_count": len(strong_edges),
        "average_strong_score": round(mean(confidences) * 100) if confidences else None,
        "top_shared_terms": top_terms,
        "top_clusters": top_clusters,
        "highest_match": {
            "title": highest_match.get("nearby_title"),
            "score": round((highest_match.get("confidence") or highest_match.get("weight") or 0) * 100),
        } if highest_match else None,
        "lowest_match": {
            "title": lowest_match.get("nearby_title"),
            "score": round((lowest_match.get("confidence") or lowest_match.get("weight") or 0) * 100),
        } if lowest_match else None,
        "summary": summary,
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
