import json
import logging
import re
from collections import Counter
from typing import Any

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
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
    profiles: list[TitleProfile] = []
    for row in rows:
        title = {key: row[key] for key in row if key in {
            "id", "plex_rating_key", "title", "year", "type", "summary", "genres", "directors",
            "writers", "actors", "poster_url", "plex_url", "source", "enrichment_status",
            "is_anchor", "primary_cluster", "last_enriched_at", "added_at", "created_at", "updated_at"
        }}
        profile = {key: row[key] for key in row if key not in title}
        profiles.append(TitleProfile(title=title, profile=profile))
    return profiles


def rebuild_edges() -> int:
    profiles = load_title_profiles()
    edges = edges_with_soft_bridges(profiles)
    stamp = now_iso()
    with get_connection() as conn:
        conn.execute("DELETE FROM edges")
        for edge in edges:
            conn.execute(
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
                {**edge_db_payload(edge), "created_at": stamp},
            )
    return len(edges)


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
                    "cluster": row.get("resolved_cluster") or row.get("primary_cluster") or "Outliers",
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
