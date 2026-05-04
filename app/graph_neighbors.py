from __future__ import annotations

from collections import Counter
from typing import Any

from app.db import get_connection
from app.graph_builder import apply_resolved_clusters
from app.models import json_list


def get_title_graph_neighbors(title_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        edge_rows = conn.execute(
            """
            SELECT e.*, s.title AS source_title, t.title AS target_title,
                   s.primary_cluster AS source_cluster, t.primary_cluster AS target_cluster
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            WHERE e.source_title_id = ? OR e.target_title_id = ?
            ORDER BY CASE e.edge_type
                        WHEN 'strong' THEN 0
                        WHEN 'soft' THEN 1
                        WHEN 'bridge' THEN 2
                        ELSE 3
                     END,
                     e.confidence DESC,
                     e.weight DESC
            """,
            (title_id, title_id),
        ).fetchall()

        ordered_neighbor_ids: list[int] = []
        seen_neighbor_ids: set[int] = set()
        for row in edge_rows:
            neighbor_id = row["target_title_id"] if row["source_title_id"] == title_id else row["source_title_id"]
            if neighbor_id in seen_neighbor_ids:
                continue
            seen_neighbor_ids.add(neighbor_id)
            ordered_neighbor_ids.append(neighbor_id)

        neighbor_profiles: dict[int, dict[str, Any]] = {}
        if ordered_neighbor_ids:
            placeholders = ",".join("?" for _ in ordered_neighbor_ids)
            profile_rows = conn.execute(
                f"""
                SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                       p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                       p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                       p.pacing_score, p.johnny_core_score, p.ai_summary
                FROM titles t
                LEFT JOIN taste_profiles p ON p.title_id = t.id
                WHERE t.id IN ({placeholders})
                """,
                ordered_neighbor_ids,
            ).fetchall()
            for row in apply_resolved_clusters(profile_rows):
                profile = dict(row)
                for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
                    profile[key] = json_list(profile.get(key))
                neighbor_profiles[profile["id"]] = profile

    edges: list[dict[str, Any]] = []
    edge_type_counts: Counter[str] = Counter()
    for raw in edge_rows:
        edge = dict(raw)
        neighbor_id = edge["target_title_id"] if edge["source_title_id"] == title_id else edge["source_title_id"]
        neighbor_profile = neighbor_profiles.get(neighbor_id)
        edge["nearby_id"] = neighbor_id
        edge["nearby_title"] = edge["target_title"] if edge["source_title_id"] == title_id else edge["source_title"]
        edge["nearby_cluster"] = (
            (neighbor_profile or {}).get("resolved_cluster")
            or (neighbor_profile or {}).get("primary_cluster")
            or (edge.get("target_cluster") if edge["source_title_id"] == title_id else edge.get("source_cluster"))
        )
        edge["shared_traits"] = json_list(edge.get("shared_traits"))
        edge["neighbor_profile"] = neighbor_profile
        edge_type = edge.get("edge_type") or "strong"
        edge_type_counts[edge_type] += 1
        edges.append(edge)

    strong_edges = [edge for edge in edges if (edge.get("edge_type") or "strong") == "strong"]
    soft_edges = [edge for edge in edges if edge.get("edge_type") == "soft"]
    bridge_edges = [edge for edge in edges if edge.get("edge_type") == "bridge"]

    return {
        "edges": edges,
        "strong_edges": strong_edges,
        "soft_edges": soft_edges,
        "bridge_edges": bridge_edges,
        "edge_count": len(edges),
        "edge_type_counts": dict(edge_type_counts),
    }
