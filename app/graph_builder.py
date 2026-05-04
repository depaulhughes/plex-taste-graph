import json
import logging
import re
import random
from datetime import datetime
from collections import Counter
from typing import Any, Optional

from app.db import get_connection, now_iso
from app.models import TitleProfile, json_list, normalise_tag
from app.taste_engine import edge_db_payload, edges_with_soft_bridges

logger = logging.getLogger("taste_graph.home")
SIGNAL_KEYS = (
    "weirdness_score",
    "emotional_weight_score",
    "intensity_score",
    "pacing_score",
    "johnny_core_score",
)
OUTLIER_EDGE_THRESHOLD = 5
DERIVED_CLUSTERS = (
    "Identity Breakdown",
    "Body Horror",
    "Tech Paranoia",
    "War / Spiritual Brutality",
    "Institutional Decay",
    "Systems / Pressure",
    "Mystery / Puzzle Box",
    "Surreal / Absurd",
    "Emotional Collapse",
    "Violence / Chaos",
    "Existential Dread",
    "Coming-of-Age / Heartbreak",
    "Adventure / Wonder",
    "Core Taste Orbit",
    "Mixed / Transitional",
)


def load_title_profiles() -> list[TitleProfile]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.id AS profile_id, p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.intensity_score, p.weirdness_score, p.emotional_weight_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary,
                   p.recommendation_hooks, p.closest_viewing_context
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
    resolved_rows = apply_resolved_clusters([dict(row) for row in rows])
    profiles: list[TitleProfile] = []
    for row in resolved_rows:
        title = {key: row[key] for key in row if key in {
            "id", "plex_rating_key", "title", "year", "type", "summary", "genres", "directors",
            "writers", "actors", "poster_url", "plex_url", "source", "enrichment_status",
            "is_anchor", "primary_cluster", "last_enriched_at", "added_at", "created_at", "updated_at"
        }}
        profile = {key: row[key] for key in row if key not in title}
        profiles.append(TitleProfile(title=title, profile=profile))
    return profiles


def rebuild_edges(
    *,
    dry_run: bool = False,
    max_candidates: int = 150,
    top_edges_per_title: int = 8,
    min_score: float = 0.60,
    progress_every: int = 50,
    progress_callback=None,
) -> int:
    return rebuild_edges_summary(
        dry_run=dry_run,
        max_candidates=max_candidates,
        top_edges_per_title=top_edges_per_title,
        min_score=min_score,
        progress_every=progress_every,
        progress_callback=progress_callback,
    )["total_edges_written"]


def rebuild_edges_summary(
    *,
    dry_run: bool = False,
    max_candidates: int = 150,
    top_edges_per_title: int = 8,
    min_score: float = 0.60,
    progress_every: int = 50,
    progress_callback=None,
) -> dict[str, Any]:
    profiles = load_title_profiles()
    if progress_callback:
        progress_callback({"stage": "load", "processed": len(profiles), "total": len(profiles), "total_titles": len(profiles)})
    edges, stats = edges_with_soft_bridges(
        profiles,
        per_node_limit=top_edges_per_title,
        min_per_node=min(5, top_edges_per_title),
        max_candidates=max_candidates,
        min_score=min_score,
        progress_every=progress_every,
        progress_callback=progress_callback,
    )
    stamp = now_iso()
    counts = Counter(edge.get("edge_type", "strong") for edge in edges)
    edge_totals: Counter[int] = Counter()
    for edge in edges:
        edge_totals[int(edge["source_title_id"])] += 1
        edge_totals[int(edge["target_title_id"])] += 1
    zero_edges = sum(1 for profile in profiles if edge_totals.get(profile.title_id, 0) == 0)
    if not dry_run:
        payloads = [{**edge_db_payload(edge), "created_at": stamp} for edge in edges]
        with get_connection() as conn:
            conn.execute("DELETE FROM edges")
            conn.executemany(
                """
                INSERT INTO edges (
                    source_title_id, target_title_id, weight, confidence, edge_type,
                    shared_traits, explanation, created_at
                )
                VALUES (
                    :source_title_id, :target_title_id, :weight, :confidence, :edge_type,
                    :shared_traits, :explanation, :created_at
                )
                """,
                payloads,
            )
    return {
        "total_titles": len(profiles),
        "total_edges_written": len(edges),
        "strong_edges": counts.get("strong", 0),
        "soft_edges": counts.get("soft", 0),
        "bridge_edges": counts.get("bridge", 0),
        "titles_with_zero_edges": zero_edges,
        **stats.as_dict(),
    }


def row_signal_vector(row: dict[str, Any]) -> list[float]:
    vector = []
    for key in SIGNAL_KEYS:
        value = row.get(key)
        try:
            vector.append(float(value if value is not None else 5))
        except (TypeError, ValueError):
            vector.append(5.0)
    return vector


def metadata_terms(row: dict[str, Any]) -> set[str]:
    terms = set()
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "genres", "primary_cluster"):
        for item in json_list(row.get(key)):
            normalized = normalise_tag(item)
            if normalized:
                terms.add(normalized)
                parts = [normalise_tag(part) for part in re.split(r"[/,&]", normalized)]
                terms.update(part for part in parts if part and len(part) > 2)
    summary = str(row.get("summary") or "")
    terms.update(
        normalise_tag(token)
        for token in re.findall(r"[a-zA-Z][a-zA-Z\-']{3,}", summary.lower())
        if token
    )
    return {term for term in terms if term}


def score_value(row: dict[str, Any], key: str, default: float = 5.0) -> float:
    try:
        value = row.get(key)
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return default


def has_term(terms: set[str], *phrases: str) -> bool:
    for phrase in phrases:
        normalized = normalise_tag(phrase)
        if not normalized:
            continue
        if normalized in terms:
            return True
        if " " in normalized and all(part in terms for part in normalized.split() if part):
            return True
    return False


def derive_cluster(row: dict[str, Any]) -> str:
    terms = metadata_terms(row)
    weirdness = score_value(row, "weirdness_score")
    emotional_weight = score_value(row, "emotional_weight_score")
    intensity = score_value(row, "intensity_score")
    johnny_core = score_value(row, "johnny_core_score")

    if has_term(terms, "identity breakdown", "identity crisis", "identity exploration", "dissociation", "double life"):
        return "Identity Breakdown"
    if has_term(terms, "body horror", "body mutation", "flesh horror", "mutation", "visceral horror"):
        return "Body Horror"
    if has_term(
        terms,
        "tech paranoia",
        "simulation anxiety",
        "surveillance and control",
        "corporate manipulation",
        "digital alienation",
        "proto matrix",
    ):
        return "Tech Paranoia"
    if has_term(terms, "war trauma", "spiritual brutality", "combat trauma", "battlefield nightmare", "war", "soldier", "military", "battalion", "frontline"):
        return "War / Spiritual Brutality"
    if has_term(
        terms,
        "moral rot",
        "institutional decay",
        "anti hero spiral",
        "prestige crime",
        "tragic masculinity",
        "moral compromise",
    ):
        return "Institutional Decay"
    if has_term(terms, "systems under pressure", "procedural mystery", "institutional pressure", "bureaucratic stress"):
        return "Systems / Pressure"
    if has_term(terms, "puzzle box mystery", "puzzle-box mystery", "conspiracy maze", "procedural conspiracy", "labyrinth mystery", "mystery"):
        return "Mystery / Puzzle Box"
    if has_term(
        terms,
        "science fiction",
        "time travel",
        "dystopia",
        "totalitarian",
        "future shock",
        "virus outbreak",
        "artificial intelligence",
        "human vs. machine",
        "cosmic insignificance",
        "spacecraft",
    ):
        return "Tech Paranoia"
    if has_term(
        terms,
        "crime",
        "courtroom",
        "jury",
        "police",
        "detective",
        "drug ring",
        "prison",
        "undercover",
        "heist",
        "legal",
    ):
        return "Systems / Pressure"
    if has_term(terms, "surreal dread", "absurdist humor", "whimsical absurdity", "dream logic", "oneiric", "surreal"):
        return "Surreal / Absurd"
    if has_term(terms, "psychological collapse", "emotionally devastating", "emotional devastation", "grief spiral", "inner breakdown"):
        return "Emotional Collapse"
    if has_term(terms, "violence", "chaos", "ultraviolence", "bloodshed", "mayhem"):
        return "Violence / Chaos"
    if has_term(terms, "horror", "haunted", "paranormal", "infected", "outbreak", "apocalypse", "survival horror"):
        return "Existential Dread"
    if has_term(terms, "existential horror", "alienation", "obsession", "cosmic dread", "meaning crisis"):
        return "Existential Dread"
    if has_term(
        terms,
        "forbidden love",
        "romance",
        "romantic",
        "heartbreak",
        "self-discovery",
        "coming of age",
        "teen-centric",
        "bittersweet",
    ):
        return "Coming-of-Age / Heartbreak"
    if has_term(
        terms,
        "family",
        "adventure",
        "fantasy",
        "quest",
        "friendship",
        "wonder",
        "heroic journey",
    ):
        return "Adventure / Wonder"

    if weirdness >= 8.5:
        return "Surreal / Absurd"
    if emotional_weight >= 8.5:
        return "Emotional Collapse"
    if intensity >= 8.5:
        return "Violence / Chaos"
    if johnny_core >= 8.0:
        return "Core Taste Orbit"
    if weirdness >= 7.5 and emotional_weight >= 7.0:
        return "Existential Dread"
    if emotional_weight >= 7.5 and intensity >= 7.0:
        return "Emotional Collapse"
    if intensity >= 7.5:
        return "Systems / Pressure"
    return "Mixed / Transitional"


def apply_resolved_clusters(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    for row in rows:
        inferred_enriched = any(
            row.get(key) is not None
            for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", *SIGNAL_KEYS)
        )
        status = row.get("enrichment_status") or ("enriched" if inferred_enriched else "pending")
        if status != "enriched":
            row["resolved_cluster"] = row.get("primary_cluster") or "Pending enrichment"
            row["is_outlier"] = 0
            continue
        cluster = derive_cluster(row)
        edge_count = row.get("edge_count")
        in_main_component = row.get("in_main_component")
        is_outlier = int(
            (edge_count is not None and int(edge_count) < OUTLIER_EDGE_THRESHOLD)
            or (in_main_component is not None and not int(in_main_component))
        )
        row["resolved_cluster"] = cluster
        row["primary_cluster"] = cluster
        row["is_outlier"] = is_outlier
    return rows


def graph_payload() -> dict[str, list[dict[str, Any]]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.*, p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.intensity_score, p.weirdness_score, p.emotional_weight_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
        edges = conn.execute("SELECT * FROM edges ORDER BY weight DESC").fetchall()

    edge_counts = Counter()
    adjacency: dict[int, set[int]] = {}
    for edge in edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        edge_counts[source] += 1
        edge_counts[target] += 1
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    main_component_ids = largest_component([int(row["id"]) for row in rows], adjacency)
    for row in rows:
        row["edge_count"] = edge_counts.get(int(row["id"]), 0)
        row["in_main_component"] = 1 if int(row["id"]) in main_component_ids else 0
    rows = apply_resolved_clusters(rows)
    # The current graph product is mapped-only. Keep the graph payload aligned
    # with that simplified UI so the frontend does not filter out most of the
    # library as "outliers" while still rendering the same dataset elsewhere.
    for row in rows:
        row["is_outlier"] = 0
    nodes = []
    for row in rows:
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        nodes.append(
            {
                "data": {
                    "id": str(row["id"]),
                    "label": row["title"],
                    "title": row["title"],
                    "year": row["year"],
                    "type": row["type"],
                    "summary": row["summary"] or "",
                    "source": row.get("source") or "manual",
                    "enrichment_status": row.get("enrichment_status") or "pending",
                    "is_anchor": row.get("is_anchor") or 0,
                    "cluster": row.get("resolved_cluster") or row.get("primary_cluster") or "Mixed / Transitional",
                    "is_outlier": row.get("is_outlier") or 0,
                    "added_at": row.get("added_at"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                    "tags": tags[:10],
                    "johnny_core_score": row.get("johnny_core_score") or 1,
                    "weirdness_score": row.get("weirdness_score") or 1,
                    "emotional_weight_score": row.get("emotional_weight_score") or 1,
                    "intensity_score": row.get("intensity_score") or 1,
                    "ai_summary": row.get("ai_summary") or "",
                }
            }
        )

    cy_edges = [
        {
            "data": {
                "id": f"{edge['source_title_id']}-{edge['target_title_id']}",
                "source": str(edge["source_title_id"]),
                "target": str(edge["target_title_id"]),
                "weight": edge["weight"],
                "confidence": edge.get("confidence", edge["weight"]),
                "edge_type": edge.get("edge_type", "strong"),
                "shared_traits": json.loads(edge["shared_traits"] or "[]"),
                "explanation": edge["explanation"] or "",
            }
        }
        for edge in edges
    ]
    return {"nodes": nodes, "edges": cy_edges}


def stats_payload() -> dict[str, Any]:
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM titles").fetchone()["count"]
        enriched = conn.execute("SELECT COUNT(*) AS count FROM taste_profiles").fetchone()["count"]
        edge_count = conn.execute("SELECT COUNT(*) AS count FROM edges").fetchone()["count"]
        pending = conn.execute(
            "SELECT COUNT(*) AS count FROM titles WHERE enrichment_status = 'pending'"
        ).fetchone()["count"]
        recent_titles = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.source, t.primary_cluster, t.enrichment_status,
                   t.summary, t.genres,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.johnny_core_score,
                   p.intensity_score
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY
                COALESCE(
                    NULLIF(t.added_at, ''),
                    NULLIF(t.updated_at, ''),
                    NULLIF(t.created_at, ''),
                    printf('%020d', t.id)
                ) DESC,
                t.id DESC
            LIMIT 40
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT t.primary_cluster, t.summary, t.genres,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.johnny_core_score, p.weirdness_score, p.emotional_weight_score
            FROM taste_profiles p
            JOIN titles t ON t.id = p.title_id
            WHERE t.enrichment_status = 'enriched'
            """
        ).fetchall()
        averages = conn.execute(
            """
            SELECT
                ROUND(AVG(COALESCE(p.johnny_core_score, 0)), 1) AS johnny_core,
                ROUND(AVG(COALESCE(p.weirdness_score, 0)), 1) AS weirdness,
                ROUND(AVG(COALESCE(p.emotional_weight_score, 0)), 1) AS emotional_weight
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.enrichment_status = 'enriched'
            """
        ).fetchone()
        extremes_ranked = {
            "johnny_core": conn.execute(
                """
                SELECT t.id, t.title, t.year, t.primary_cluster, t.source,
                       COALESCE(ec.edge_count, 0) AS edge_count,
                       p.johnny_core_score, p.weirdness_score, p.emotional_weight_score
                FROM titles t
                JOIN taste_profiles p ON p.title_id = t.id
                LEFT JOIN (
                    SELECT title_id, COUNT(*) AS edge_count
                    FROM (
                        SELECT source_title_id AS title_id FROM edges
                        UNION ALL
                        SELECT target_title_id AS title_id FROM edges
                    )
                    GROUP BY title_id
                ) ec ON ec.title_id = t.id
                WHERE t.enrichment_status = 'enriched'
                ORDER BY
                    CASE WHEN t.source = 'plex' THEN 0 ELSE 1 END,
                    p.johnny_core_score DESC,
                    ec.edge_count DESC,
                    p.weirdness_score DESC,
                    p.emotional_weight_score DESC,
                    t.title COLLATE NOCASE
                LIMIT 8
                """
            ).fetchall(),
            "weirdness": conn.execute(
                """
                SELECT t.id, t.title, t.year, t.primary_cluster, t.source,
                       COALESCE(ec.edge_count, 0) AS edge_count,
                       p.johnny_core_score, p.weirdness_score, p.emotional_weight_score
                FROM titles t
                JOIN taste_profiles p ON p.title_id = t.id
                LEFT JOIN (
                    SELECT title_id, COUNT(*) AS edge_count
                    FROM (
                        SELECT source_title_id AS title_id FROM edges
                        UNION ALL
                        SELECT target_title_id AS title_id FROM edges
                    )
                    GROUP BY title_id
                ) ec ON ec.title_id = t.id
                WHERE t.enrichment_status = 'enriched'
                ORDER BY
                    p.weirdness_score DESC,
                    CASE WHEN t.source = 'plex' THEN 0 ELSE 1 END,
                    ec.edge_count DESC,
                    p.johnny_core_score DESC,
                    p.emotional_weight_score DESC,
                    t.title COLLATE NOCASE
                LIMIT 8
                """
            ).fetchall(),
            "emotional_weight": conn.execute(
                """
                SELECT t.id, t.title, t.year, t.primary_cluster, t.source,
                       COALESCE(ec.edge_count, 0) AS edge_count,
                       p.johnny_core_score, p.weirdness_score, p.emotional_weight_score
                FROM titles t
                JOIN taste_profiles p ON p.title_id = t.id
                LEFT JOIN (
                    SELECT title_id, COUNT(*) AS edge_count
                    FROM (
                        SELECT source_title_id AS title_id FROM edges
                        UNION ALL
                        SELECT target_title_id AS title_id FROM edges
                    )
                    GROUP BY title_id
                ) ec ON ec.title_id = t.id
                WHERE t.enrichment_status = 'enriched'
                ORDER BY
                    p.emotional_weight_score DESC,
                    CASE WHEN t.source = 'plex' THEN 0 ELSE 1 END,
                    ec.edge_count DESC,
                    p.johnny_core_score DESC,
                    p.weirdness_score DESC,
                    t.title COLLATE NOCASE
                LIMIT 8
                """
            ).fetchall(),
        }
    logger.debug("home recent_titles total_titles=%s rows_returned=%s", total, len(recent_titles))
    logger.debug(
        "home taste_extremes ranked_counts johnny_core=%s weirdness=%s emotional_weight=%s",
        len(extremes_ranked["johnny_core"]),
        len(extremes_ranked["weirdness"]),
        len(extremes_ranked["emotional_weight"]),
    )
    recent_titles = apply_resolved_clusters(recent_titles)
    for key in extremes_ranked:
        extremes_ranked[key] = apply_resolved_clusters(extremes_ranked[key])
    rows = apply_resolved_clusters(rows)
    clusters = Counter()
    tag_counter = Counter()
    for row in rows:
        if not row.get("is_outlier"):
            clusters[row.get("resolved_cluster") or row.get("primary_cluster") or "Outliers"] += 1
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        for tag in tags:
            normalized = normalise_tag(tag)
            if normalized:
                tag_counter[normalized] += 1
    used_ids = set()
    extremes = {}
    for key in ("johnny_core", "weirdness", "emotional_weight"):
        ranked = extremes_ranked[key]
        picked = None
        for item in ranked:
            if item["id"] not in used_ids:
                picked = item
                break
        if not picked and ranked:
            picked = ranked[0]
        extremes[key] = picked
        if picked:
            used_ids.add(picked["id"])
    return {
        "total_titles": total,
        "enriched_titles": enriched,
        "pending_titles": pending,
        "graph_edges": edge_count,
        "average_scores": {
            "johnny_core": float(averages["johnny_core"] or 0),
            "weirdness": float(averages["weirdness"] or 0),
            "emotional_weight": float(averages["emotional_weight"] or 0),
        },
        "top_signal_tags": [
            {"tag": tag.replace("-", " "), "count": count}
            for tag, count in tag_counter.most_common(8)
        ],
        "top_clusters": clusters.most_common(8),
        "taste_extremes": extremes,
        "taste_extremes_ranked": extremes_ranked,
        "recent_titles": recent_titles,
    }


def get_home_insight_sections(limit: int = 4) -> list[dict[str, Any]]:
    max_sections = max(3, min(5, int(limit or 4)))

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.summary, t.genres, t.source,
                   t.enrichment_status, t.primary_cluster, t.added_at, t.created_at, t.updated_at,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.intensity_score, p.weirdness_score, p.emotional_weight_score,
                   p.pacing_score, p.johnny_core_score, p.recommendation_hooks, p.closest_viewing_context
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
        edges = conn.execute(
            """
            SELECT source_title_id, target_title_id, edge_type
            FROM edges
            """
        ).fetchall()

    if not rows:
        return []

    edge_counts: Counter[int] = Counter()
    bridge_counts: Counter[int] = Counter()
    adjacency: dict[int, set[int]] = {}
    for edge in edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        edge_type = str(edge["edge_type"] or "strong")
        edge_counts[source] += 1
        edge_counts[target] += 1
        if edge_type == "bridge":
            bridge_counts[source] += 1
            bridge_counts[target] += 1
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    main_component_ids = largest_component([int(row["id"]) for row in rows], adjacency)
    prepared_rows: list[dict[str, Any]] = []
    for row in apply_resolved_clusters([dict(row) for row in rows]):
        item = dict(row)
        item_id = int(item["id"])
        item["edge_count"] = edge_counts.get(item_id, 0)
        item["bridge_count"] = bridge_counts.get(item_id, 0)
        item["in_main_component"] = 1 if item_id in main_component_ids else 0
        item["_resolved_cluster"] = item.get("resolved_cluster") or item.get("primary_cluster") or "Outliers"
        item["_source_rank"] = 0 if item.get("source") == "plex" else 1
        item["_year"] = int(item.get("year") or 0)
        item["_recency"] = item.get("added_at") or item.get("updated_at") or item.get("created_at") or ""
        item["_terms"] = metadata_terms(item)
        item["_score_sum"] = sum(score_value(item, key) for key in SIGNAL_KEYS)
        prepared_rows.append(item)

    enriched_rows = [row for row in prepared_rows if row.get("enrichment_status") == "enriched"]
    pending_rows = [row for row in prepared_rows if row.get("enrichment_status") != "enriched"]
    mapped_rows = [row for row in enriched_rows if not row.get("is_outlier")]

    cluster_counts = Counter(
        row["_resolved_cluster"]
        for row in enriched_rows
        if row["_resolved_cluster"] not in {"Mixed / Transitional", "Pending enrichment", "Outliers"}
    )
    interesting_clusters = [
        cluster
        for cluster, count in cluster_counts.most_common()
        if count >= 4 and cluster in {
            "Body Horror",
            "Tech Paranoia",
            "Institutional Decay",
            "Surreal / Absurd",
            "Identity Breakdown",
            "Systems / Pressure",
            "Emotional Collapse",
            "Violence / Chaos",
            "Existential Dread",
            "Coming-of-Age / Heartbreak",
        }
    ][:6]

    current_year = datetime.utcnow().year
    used_ids: set[int] = set()

    def normalize_title(row: dict[str, Any]) -> dict[str, Any]:
        scores_present = any(row.get(key) is not None for key in SIGNAL_KEYS)
        return {
            "id": int(row["id"]),
            "title": row.get("title"),
            "year": row.get("year"),
            "source": row.get("source") or "manual",
            "enrichment_status": row.get("enrichment_status") or "pending",
            "primary_cluster": row.get("_resolved_cluster") or row.get("primary_cluster") or "Outliers",
            "display_cluster": row.get("_resolved_cluster") or row.get("primary_cluster") or "Outliers",
            "edge_count": int(row.get("edge_count") or 0),
            "bridge_count": int(row.get("bridge_count") or 0),
            "johnny_core_score": row.get("johnny_core_score"),
            "weirdness_score": row.get("weirdness_score"),
            "emotional_weight_score": row.get("emotional_weight_score"),
            "url": f"/graph?title_id={int(row['id'])}",
            "has_scores": scores_present,
        }

    def pick_section(
        title: str,
        subtitle: str,
        section_type: str,
        candidates: list[dict[str, Any]],
        *,
        max_items: int = 6,
        min_items: int = 3,
    ) -> Optional[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in candidates:
            row_id = int(row["id"])
            if row_id in used_ids:
                continue
            items.append(normalize_title(row))
            if len(items) >= max_items:
                break
        if len(items) < min_items:
            return None
        used_ids.update(item["id"] for item in items)
        return {
            "title": title,
            "subtitle": subtitle,
            "type": section_type,
            "items": items,
        }

    def cluster_key(row: dict[str, Any], _cluster_name: str, score_key: Optional[str] = None) -> tuple[Any, ...]:
        score_part = score_value(row, score_key) if score_key else row["_score_sum"]
        return (
            score_part,
            row["_source_rank"],
            row["edge_count"],
            row["bridge_count"],
            row["_year"],
            row["title"].lower(),
        )

    def decade_key(row: dict[str, Any], decade_start: int) -> tuple[Any, ...]:
        return (
            row["_score_sum"],
            row["edge_count"],
            row["_source_rank"],
            row["title"].lower(),
        )

    pool: list[dict[str, Any]] = []

    pool.append(
        {
            "title": "Top Johnny-core titles",
            "subtitle": "Titles that sit closest to the center of your current taste gravity.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "johnny_core_score"),
                    row["_source_rank"],
                    row["edge_count"],
                    score_value(row, "weirdness_score"),
                    score_value(row, "emotional_weight_score"),
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Weirdest titles",
            "subtitle": "The most off-center titles in the current library.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "weirdness_score"),
                    row["_source_rank"],
                    row["edge_count"],
                    score_value(row, "emotional_weight_score"),
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Most emotionally heavy titles",
            "subtitle": "The titles carrying the most weight right now.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "emotional_weight_score"),
                    score_value(row, "intensity_score"),
                    row["_source_rank"],
                    row["edge_count"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Most connected titles",
            "subtitle": "The current core of the mapped network.",
            "type": "network",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    row["edge_count"],
                    row["bridge_count"],
                    score_value(row, "johnny_core_score"),
                    score_value(row, "weirdness_score"),
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Newest added enriched titles",
            "subtitle": "Recently enriched titles that are now ready to browse.",
            "type": "recent",
            "candidates": sorted(
                [row for row in enriched_rows if row.get("enrichment_status") == "enriched"],
                key=lambda row: (
                    row["_recency"],
                    row["_source_rank"],
                    row["edge_count"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Recently added pending titles",
            "subtitle": "Fresh library arrivals waiting on enrichment.",
            "type": "recent",
            "candidates": sorted(
                pending_rows,
                key=lambda row: (
                    row["_recency"],
                    row["_year"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Most isolated mapped titles",
            "subtitle": "Mapped titles that sit near the edges of the current network.",
            "type": "network",
            "candidates": sorted(
                [row for row in mapped_rows if row["edge_count"] > 0],
                key=lambda row: (
                    row["edge_count"],
                    row["bridge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
            ),
        }
    )
    pool.append(
        {
            "title": "Best bridge titles",
            "subtitle": "Titles that connect separate neighborhoods.",
            "type": "network",
            "candidates": sorted(
                [row for row in mapped_rows if row["bridge_count"] > 0],
                key=lambda row: (
                    row["bridge_count"],
                    row["edge_count"],
                    score_value(row, "johnny_core_score"),
                    score_value(row, "weirdness_score"),
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Highest weirdness in comedies",
            "subtitle": "Comedy-adjacent titles with a stranger tilt than expected.",
            "type": "tags",
            "candidates": sorted(
                [row for row in mapped_rows if has_term(row["_terms"], "comedy", "comedic", "satire", "parody", "humor", "humour")],
                key=lambda row: (
                    score_value(row, "weirdness_score"),
                    score_value(row, "emotional_weight_score"),
                    row["edge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Heaviest sci-fi",
            "subtitle": "Science fiction titles carrying the most emotional weight.",
            "type": "tags",
            "candidates": sorted(
                [
                    row
                    for row in mapped_rows
                    if has_term(
                        row["_terms"],
                        "science fiction",
                        "sci fi",
                        "sci-fi",
                        "space",
                        "future",
                        "time travel",
                        "alien",
                    )
                ],
                key=lambda row: (
                    score_value(row, "emotional_weight_score"),
                    score_value(row, "weirdness_score"),
                    row["_source_rank"],
                    row["edge_count"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Lightest picks",
            "subtitle": "The gentlest, least heavy titles currently in the library.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "emotional_weight_score"),
                    score_value(row, "intensity_score"),
                    score_value(row, "weirdness_score"),
                    row["_source_rank"],
                    row["edge_count"],
                    row["title"].lower(),
                ),
            ),
        }
    )
    pool.append(
        {
            "title": "Darkest thrillers",
            "subtitle": "Thriller, crime, and suspense titles with the heaviest atmosphere.",
            "type": "tags",
            "candidates": sorted(
                [
                    row
                    for row in mapped_rows
                    if has_term(row["_terms"], "thriller", "crime", "suspense", "detective", "murder", "investigation")
                ],
                key=lambda row: (
                    score_value(row, "emotional_weight_score"),
                    score_value(row, "intensity_score"),
                    score_value(row, "weirdness_score"),
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Family-friendly but weird",
            "subtitle": "Titles that look familiar on the surface but lean strange underneath.",
            "type": "tags",
            "candidates": sorted(
                [
                    row
                    for row in mapped_rows
                    if has_term(
                        row["_terms"],
                        "family",
                        "children",
                        "kids",
                        "animation",
                        "adventure",
                        "fantasy",
                        "wonder",
                    )
                ],
                key=lambda row: (
                    score_value(row, "weirdness_score"),
                    -score_value(row, "emotional_weight_score"),
                    row["edge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Recent releases with high weirdness",
            "subtitle": "Newer titles that already lean off-center.",
            "type": "recent",
            "candidates": sorted(
                [row for row in mapped_rows if row["_year"] and row["_year"] >= current_year - 6],
                key=lambda row: (
                    score_value(row, "weirdness_score"),
                    row["_year"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Titles with high Johnny-core + emotional weight",
            "subtitle": "Your strongest taste anchors with real emotional heft.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "johnny_core_score") + score_value(row, "emotional_weight_score"),
                    score_value(row, "johnny_core_score"),
                    score_value(row, "emotional_weight_score"),
                    row["edge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Titles with high weirdness + low emotional weight",
            "subtitle": "Stranger, lighter-toned titles that still stand out.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "weirdness_score") - score_value(row, "emotional_weight_score"),
                    score_value(row, "weirdness_score"),
                    row["edge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "Titles with high emotional weight + low weirdness",
            "subtitle": "Heavy, grounded titles that stay close to the center.",
            "type": "signals",
            "candidates": sorted(
                mapped_rows,
                key=lambda row: (
                    score_value(row, "emotional_weight_score") - score_value(row, "weirdness_score"),
                    score_value(row, "emotional_weight_score"),
                    row["edge_count"],
                    row["_source_rank"],
                    row["title"].lower(),
                ),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "1980s taste peaks",
            "subtitle": "The strongest older cuts from the 1980s.",
            "type": "decades",
            "candidates": sorted(
                [row for row in mapped_rows if row["_year"] and 1980 <= row["_year"] < 1990],
                key=lambda row: decade_key(row, 1980),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "1990s taste peaks",
            "subtitle": "The strongest cuts from the 1990s.",
            "type": "decades",
            "candidates": sorted(
                [row for row in mapped_rows if row["_year"] and 1990 <= row["_year"] < 2000],
                key=lambda row: decade_key(row, 1990),
                reverse=True,
            ),
        }
    )
    pool.append(
        {
            "title": "2000s taste peaks",
            "subtitle": "The strongest cuts from the 2000s.",
            "type": "decades",
            "candidates": sorted(
                [row for row in mapped_rows if row["_year"] and 2000 <= row["_year"] < 2010],
                key=lambda row: decade_key(row, 2000),
                reverse=True,
            ),
        }
    )
    for cluster in interesting_clusters:
        pool.append(
            {
                "title": f"Strongest {cluster} titles",
                "subtitle": f"Titles that sit most firmly inside {cluster}.",
                "type": "clusters",
                "candidates": sorted(
                    [row for row in mapped_rows if row["_resolved_cluster"] == cluster],
                    key=lambda row, cluster_name=cluster: cluster_key(row, cluster_name, {
                        "Body Horror": "weirdness_score",
                        "Tech Paranoia": "weirdness_score",
                        "Institutional Decay": "johnny_core_score",
                        "Surreal / Absurd": "weirdness_score",
                        "Identity Breakdown": "johnny_core_score",
                        "Systems / Pressure": "emotional_weight_score",
                        "Emotional Collapse": "emotional_weight_score",
                        "Violence / Chaos": "intensity_score",
                        "Existential Dread": "emotional_weight_score",
                        "Coming-of-Age / Heartbreak": "emotional_weight_score",
                    }.get(cluster_name)),
                    reverse=True,
                ),
            }
        )

    random.shuffle(pool)
    target_count = random.randint(3, max(3, max_sections))
    selected_sections: list[dict[str, Any]] = []
    for spec in pool:
        if len(selected_sections) >= target_count:
            break
        section = pick_section(spec["title"], spec["subtitle"], spec["type"], spec["candidates"])
        if section:
            selected_sections.append(section)

    if len(selected_sections) < 3:
        fallback_candidates = sorted(
            enriched_rows,
            key=lambda row: (
                score_value(row, "johnny_core_score") + score_value(row, "weirdness_score") + score_value(row, "emotional_weight_score"),
                row["_source_rank"],
                row["edge_count"],
                row["_year"],
                row["title"].lower(),
            ),
            reverse=True,
        )
        fallback = pick_section(
            "Taste Extremes",
            "The current strongest peaks in your library.",
            "fallback",
            fallback_candidates,
            max_items=6,
        )
        if fallback:
            selected_sections.append(fallback)

    if not selected_sections:
        return []
    return selected_sections[:target_count]


def largest_component(node_ids: list[int], adjacency: dict[int, set[int]]) -> set[int]:
    seen: set[int] = set()
    best: set[int] = set()
    for node_id in node_ids:
        if node_id in seen:
            continue
        stack = [node_id]
        component: set[int] = set()
        seen.add(node_id)
        while stack:
            current = stack.pop()
            component.add(current)
            for neighbor in adjacency.get(current, set()):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                stack.append(neighbor)
        if len(component) > len(best):
            best = component
    return best


def _library_display_tags(row: dict[str, Any], *, max_tags: int = 5) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []

    def push(value: str) -> None:
        normalized = normalise_tag(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        tags.append(str(value).strip())

    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
        for item in json_list(row.get(key)):
            push(item)
            if len(tags) >= max_tags:
                return tags

    context = str(row.get("closest_viewing_context") or "").strip()
    if context:
        for part in re.split(r"[;,/]", context):
            push(part)
            if len(tags) >= max_tags:
                return tags

    summary_terms = [
        term.replace("-", " ")
        for term in sorted(metadata_terms(row), key=len, reverse=True)
        if term
        and term not in {normalise_tag(row.get("resolved_cluster") or row.get("primary_cluster") or "")}
        and len(term) >= 5
    ]
    for term in summary_terms:
        push(term)
        if len(tags) >= max_tags:
            return tags

    cluster_fallback = row.get("resolved_cluster") or row.get("primary_cluster")
    if cluster_fallback and (row.get("enrichment_status") == "enriched") and not tags:
        push(cluster_fallback)

    return tags[:max_tags]


def _library_connection_bucket(row: dict[str, Any]) -> str:
    edge_count = int(row.get("connection_count") or 0)
    if int(row.get("is_outlier") or 0):
        return "outliers"
    if edge_count >= 10:
        return "most_connected"
    if edge_count >= OUTLIER_EDGE_THRESHOLD:
        return "strongly_mapped"
    return "least_connected"


def _library_signal_bucket(row: dict[str, Any]) -> str:
    johnny = score_value(row, "johnny_core_score", 0)
    weird = score_value(row, "weirdness_score", 0)
    emotional = score_value(row, "emotional_weight_score", 0)

    if johnny >= 8:
        return "high_johnny"
    if weird >= 8:
        return "high_weirdness"
    if emotional >= 8:
        return "high_emotional"
    if weird >= 7 and emotional <= 4:
        return "weird_but_light"
    if emotional >= 7 and weird <= 5:
        return "heavy_but_grounded"
    return "balanced"


def _library_decade_bucket(year: Any) -> str:
    try:
        value = int(year or 0)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 2020:
        return "2020s"
    if value >= 2010:
        return "2010s"
    if value >= 2000:
        return "2000s"
    if value >= 1990:
        return "1990s"
    if value >= 1980:
        return "1980s"
    if value >= 1970:
        return "1970s"
    if value > 0:
        return "pre-1970"
    return "unknown"


def get_library_titles(limit: Optional[int] = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.*,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.intensity_score, p.weirdness_score, p.emotional_weight_score,
                   p.pacing_score, p.johnny_core_score, p.recommendation_hooks,
                   p.closest_viewing_context,
                   COALESCE(ec.connection_count, 0) AS connection_count
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            LEFT JOIN (
                SELECT title_id, COUNT(*) AS connection_count
                FROM (
                    SELECT source_title_id AS title_id FROM edges
                    UNION ALL
                    SELECT target_title_id AS title_id FROM edges
                )
                GROUP BY title_id
            ) ec ON ec.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()

    normalized_rows = apply_resolved_clusters([dict(row) for row in rows])
    cards: list[dict[str, Any]] = []
    enriched_without_tags = 0

    for row in normalized_rows:
        display_tags = _library_display_tags(row)
        if row.get("enrichment_status") == "enriched" and not display_tags:
            enriched_without_tags += 1

        cluster = row.get("resolved_cluster") or row.get("primary_cluster") or (
            "Pending enrichment" if row.get("enrichment_status") != "enriched" else "Mixed / Transitional"
        )
        status = row.get("enrichment_status") or "pending"
        media_type = row.get("type") or "movie"
        connection_count = int(row.get("connection_count") or 0)
        source = row.get("source") or "manual"

        cards.append(
            {
                "id": int(row["id"]),
                "title": row.get("title"),
                "year": row.get("year"),
                "source": source,
                "media_type": media_type,
                "cluster": cluster,
                "status": status,
                "is_outlier": int(row.get("is_outlier") or 0),
                "connection_count": connection_count,
                "johnny_core": row.get("johnny_core_score"),
                "weirdness": row.get("weirdness_score"),
                "emotional_weight": row.get("emotional_weight_score"),
                "display_tags": display_tags,
                "signal_bucket": _library_signal_bucket(row),
                "decade_bucket": _library_decade_bucket(row.get("year")),
                "connection_bucket": _library_connection_bucket({
                    **row,
                    "connection_count": connection_count,
                }),
            }
        )

    logger.debug(
        "library cards total=%s enriched=%s enriched_without_display_tags=%s",
        len(cards),
        sum(1 for card in cards if card["status"] == "enriched"),
        enriched_without_tags,
    )

    if limit is not None:
        return cards[: int(limit)]
    return cards
