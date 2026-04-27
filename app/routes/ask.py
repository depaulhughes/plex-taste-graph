import json
import logging
import re
import time
from collections import Counter, deque
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_connection, now_iso
from app.graph_builder import apply_resolved_clusters
from app.models import json_list
from app.openai_client import OpenAITasteClient, server_diagnostic_response

router = APIRouter()
MIN_SIMILARITY = 0.6


class AskRequest(BaseModel):
    question: str
    explain_with_ai: bool = False
    selected_title_id: Optional[int] = None


logger = logging.getLogger("taste_graph.ask")


@router.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse("ask.html", {"request": request})


@router.post("/api/ask")
def api_ask(payload: AskRequest) -> dict:
    total_start = time.perf_counter()
    blocked = server_diagnostic_response(payload.question)
    if blocked:
        store_question(payload.question, blocked)
        return blocked

    graph_version = current_graph_version()
    normalized = normalize_question(payload.question)
    mode = "ai" if payload.explain_with_ai else "fast"
    cache_key = cache_key_for(mode, normalized, payload.selected_title_id)
    cached = get_cached_answer(cache_key, graph_version)
    if cached:
        cached["cached"] = True
        logger.info("ask cache hit total=%.3fs", time.perf_counter() - total_start)
        return cached

    retrieval_start = time.perf_counter()
    fast_answer = similarity_fast_answer(payload.question, payload.selected_title_id)
    deterministic_answer = deterministic_connection_answer(payload.question, payload.selected_title_id)
    retrieval_time = time.perf_counter() - retrieval_start

    openai_time = 0.0
    if fast_answer and not payload.explain_with_ai:
        answer = fast_answer
    elif deterministic_answer and not payload.explain_with_ai:
        answer = deterministic_answer
    elif get_settings().openai_api_key and should_use_ai(payload.question, payload.explain_with_ai, fast_answer, deterministic_answer):
        context = build_focused_context(payload.question, fast_answer, payload.selected_title_id, deterministic_answer)
        openai_start = time.perf_counter()
        try:
            answer = OpenAITasteClient().answer_question(payload.question, context, timeout_seconds=3.0)
        except Exception:
            answer = deterministic_answer or fast_answer or local_taste_answer(payload.question)
        openai_time = time.perf_counter() - openai_start
        if fast_answer:
            answer.setdefault("fast_result", fast_answer)
        if deterministic_answer:
            answer.setdefault("graph_reasoning", deterministic_answer.get("graph_reasoning"))
    else:
        answer = deterministic_answer or fast_answer or local_taste_answer(payload.question)

    answer.setdefault("can_explain_with_ai", bool(get_settings().openai_api_key) and not payload.explain_with_ai)
    answer["timing"] = {
        "local_retrieval_seconds": round(retrieval_time, 4),
        "openai_seconds": round(openai_time, 4),
        "total_seconds": round(time.perf_counter() - total_start, 4),
    }
    set_cached_answer(cache_key, graph_version, payload.question, answer)
    store_question(payload.question, answer)
    logger.info(
        "ask timings local=%.3fs openai=%.3fs total=%.3fs mode=%s",
        retrieval_time,
        openai_time,
        time.perf_counter() - total_start,
        mode,
    )
    return answer


def normalize_question(question: str) -> str:
    return " ".join(question.strip().lower().split())


def cache_key_for(mode: str, normalized: str, selected_title_id: Optional[int]) -> str:
    return f"{mode}:title={selected_title_id or 'none'}:{normalized}"


def current_graph_version() -> str:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM titles) AS title_count,
                (SELECT COUNT(*) FROM edges) AS edge_count,
                COALESCE((SELECT MAX(updated_at) FROM titles), '') AS max_title_update,
                COALESCE((SELECT MAX(created_at) FROM edges), '') AS max_edge_create
            """
        ).fetchone()
    return f"{row['title_count']}:{row['edge_count']}:{row['max_title_update']}:{row['max_edge_create']}"


def get_cached_answer(cache_key: str, graph_version: str) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT answer FROM ask_cache WHERE cache_key = ? AND graph_version = ?",
            (cache_key, graph_version),
        ).fetchone()
    return json.loads(row["answer"]) if row else None


def set_cached_answer(cache_key: str, graph_version: str, question: str, answer: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ask_cache (cache_key, graph_version, question, answer, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                graph_version=excluded.graph_version,
                question=excluded.question,
                answer=excluded.answer,
                created_at=excluded.created_at
            """,
            (cache_key, graph_version, question, json.dumps(answer), now_iso()),
        )


def build_context(limit: int = 80) -> str:
    with get_connection() as conn:
        titles = conn.execute(
            """
            SELECT t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY p.johnny_core_score DESC, p.weirdness_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        edges = conn.execute(
            """
            SELECT s.title AS source, t.title AS target, e.weight, e.confidence,
                   e.edge_type, e.shared_traits, e.explanation
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            ORDER BY e.weight DESC
            LIMIT 120
            """
        ).fetchall()
    titles = apply_resolved_clusters(titles)
    compact_titles = []
    for row in titles:
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        compact_titles.append(
            {
                "title": row["title"],
                "year": row["year"],
                "type": row["type"],
                "source": row["source"],
                "primary_cluster": row["primary_cluster"],
                "tags": tags[:8],
                "scores": {
                    "weirdness": row["weirdness_score"],
                    "emotional_weight": row["emotional_weight_score"],
                    "intensity": row["intensity_score"],
                    "pacing": row["pacing_score"],
                    "johnny_core": row["johnny_core_score"],
                },
                "summary": row["ai_summary"],
            }
        )
    compact_edges = [
        {
            "source": edge["source"],
            "target": edge["target"],
            "weight": edge["weight"],
            "confidence": edge.get("confidence") or edge["weight"],
            "edge_type": edge.get("edge_type") or "strong",
            "traits": json_list(edge["shared_traits"]),
            "why": edge["explanation"],
        }
        for edge in edges
    ]
    return json.dumps({"titles": compact_titles, "strongest_connections": compact_edges})


def build_focused_context(
    question: str,
    fast_answer: Optional[dict] = None,
    selected_title_id: Optional[int] = None,
    deterministic_answer: Optional[dict] = None,
) -> str:
    if deterministic_answer and deterministic_answer.get("graph_reasoning"):
        return json.dumps(
            {
                "question": question,
                "graph_reasoning": deterministic_answer["graph_reasoning"],
                "fast_result_summary": {
                    "recommendation": fast_answer.get("recommendation"),
                    "best_matches": [item.get("title") for item in (fast_answer.get("best_matches") or [])[:5] if isinstance(item, dict)],
                } if fast_answer else None,
            }
        )
    focus = title_by_id(selected_title_id) if selected_title_id else matched_title_from_question(question)
    candidate_titles = []
    if fast_answer:
        for group in ("best_matches", "weirdest_matches", "emotionally_heavier_matches", "safer_easier_watches"):
            for item in fast_answer.get(group, []) or []:
                if isinstance(item, dict) and item.get("title") not in candidate_titles:
                    candidate_titles.append(item["title"])
    params = []
    title_clause = ""
    if focus:
        candidate_titles.insert(0, focus["title"])
    if candidate_titles:
        candidate_titles = candidate_titles[:15]
        placeholders = ",".join("?" for _ in candidate_titles)
        title_clause = f"WHERE t.title IN ({placeholders})"
        params.extend(candidate_titles)
    with get_connection() as conn:
        titles = conn.execute(
            f"""
            SELECT t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            {title_clause}
            ORDER BY p.johnny_core_score DESC, p.weirdness_score DESC
            LIMIT 15
            """,
            params,
        ).fetchall()
        focus_profile = None
        if focus:
            focus_profile = conn.execute(
                """
                SELECT t.title, t.year, t.type, t.source, t.primary_cluster,
                       p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                       p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                       p.pacing_score, p.johnny_core_score, p.ai_summary
                FROM titles t
                LEFT JOIN taste_profiles p ON p.title_id = t.id
                WHERE t.id = ?
                LIMIT 1
                """,
                (focus["id"],),
            ).fetchone()
        edges = conn.execute(
            """
            SELECT s.title AS source, t.title AS target, e.weight, e.confidence,
                   e.edge_type, e.shared_traits, e.explanation
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            WHERE (? = '' OR s.title = ? OR t.title = ?)
            ORDER BY CASE e.edge_type WHEN 'strong' THEN 0 ELSE 1 END, e.confidence DESC
            LIMIT 30
            """,
            (focus["title"] if focus else "", focus["title"] if focus else "", focus["title"] if focus else ""),
        ).fetchall()
    compact_titles = []
    titles = apply_resolved_clusters(titles)
    if focus_profile:
        focus_profile = apply_resolved_clusters([focus_profile])[0]
    for row in titles:
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        compact_titles.append(
            {
                "title": row["title"],
                "year": row["year"],
                "type": row["type"],
                "cluster": row["primary_cluster"],
                "tags": tags[:8],
                "scores": {
                    "weirdness": row["weirdness_score"],
                    "emotional_weight": row["emotional_weight_score"],
                    "intensity": row["intensity_score"],
                    "pacing": row["pacing_score"],
                    "johnny_core": row["johnny_core_score"],
                },
                "summary": row["ai_summary"],
            }
        )
    return json.dumps(
        {
            "question": question,
            "matched_title": focus["title"] if focus else None,
            "selected_title_profile": compact_title_payload(focus_profile) if focus_profile else None,
            "titles": compact_titles,
            "relevant_edges": [
                {
                    "source": edge["source"],
                    "target": edge["target"],
                    "edge_type": edge.get("edge_type") or "strong",
                    "confidence": edge.get("confidence") or edge["weight"],
                    "traits": json_list(edge["shared_traits"]),
                    "why": edge["explanation"],
                }
                for edge in edges
            ],
            "candidate_comparisons": [
                {
                    "title": item.get("title"),
                    "edge_type": item.get("edge_type", "strong"),
                    "shared_traits": item.get("shared_traits", [])[:5],
                    "reason": item.get("reason"),
                    "scores": item.get("scores", {}),
                }
                for group in ("best_matches", "weirdest_matches", "emotionally_heavier_matches", "safer_easier_watches", "bridge_titles")
                for item in (fast_answer or {}).get(group, [])[:3]
                if isinstance(item, dict)
            ],
            "fast_result": fast_answer,
        }
    )


def should_use_ai(
    question: str,
    explain_with_ai: bool,
    fast_answer: Optional[dict],
    deterministic_answer: Optional[dict],
) -> bool:
    if explain_with_ai:
        return True
    lowered = normalize_question(question)
    if fast_answer or deterministic_answer:
        return False
    return any(term in lowered for term in ("explain", "why", "compare", "difference between"))


def store_question(question: str, answer: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO questions (question, answer, created_at) VALUES (?, ?, ?)",
            (question, json.dumps(answer), now_iso()),
        )


SIMILARITY_PATTERNS = [
    r"what(?:'s| is)? closest to (.+)",
    r"what(?:'s| is)? similar to (.+)",
    r"movies like (.+)",
    r"shows like (.+)",
    r"nearest to (.+)",
    r"recommendations near (.+)",
    r"similar to (.+)",
]


def is_similarity_question(question: str) -> bool:
    lowered = normalize_question(question)
    return any(re.search(pattern, lowered) for pattern in SIMILARITY_PATTERNS)


def matched_title_from_question(question: str) -> Optional[dict]:
    lowered = normalize_question(question)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, year, type, source, primary_cluster
            FROM titles
            ORDER BY LENGTH(title) DESC
            """
        ).fetchall()
    for row in rows:
        if normalize_question(row["title"]) in lowered:
            return row
    for pattern in SIMILARITY_PATTERNS:
        match = re.search(pattern, lowered)
        if not match:
            continue
        fragment = match.group(1).strip(" ?.!")
        for row in rows:
            title = normalize_question(row["title"])
            if fragment in title or title in fragment:
                return row
    return None


def matched_titles_from_question(question: str, limit: int = 2) -> list[dict]:
    lowered = normalize_question(question)
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, title, year, type, source, primary_cluster
            FROM titles
            ORDER BY LENGTH(title) DESC
            """
        ).fetchall()
    matched = []
    seen_ids = set()
    for row in rows:
        normalized_title = normalize_question(row["title"])
        if normalized_title and normalized_title in lowered and row["id"] not in seen_ids:
            matched.append(row)
            seen_ids.add(row["id"])
            if len(matched) >= limit:
                return matched
    return matched


def similarity_fast_answer(question: str, selected_title_id: Optional[int] = None) -> Optional[dict]:
    if not selected_title_id and not is_similarity_question(question):
        return None
    focus = title_by_id(selected_title_id) if selected_title_id else matched_title_from_question(question)
    if not focus:
        return None
    with get_connection() as conn:
        focus_profile = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score, p.pacing_score,
                   p.johnny_core_score, p.ai_summary
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id = ?
            """,
            (focus["id"],),
        ).fetchone()
        edges = conn.execute(
            """
            SELECT e.*, s.title AS source_title, t.title AS target_title,
                   s.id AS source_id, t.id AS target_id
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            WHERE e.source_title_id = ? OR e.target_title_id = ?
            ORDER BY CASE e.edge_type WHEN 'strong' THEN 0 ELSE 1 END,
                     (e.confidence + e.weight) DESC,
                     e.weight DESC
            """,
            (focus["id"], focus["id"]),
        ).fetchall()
        neighbor_ids = [
            edge["target_id"] if edge["source_id"] == focus["id"] else edge["source_id"]
            for edge in edges
        ][:20]
        profiles = {}
        if neighbor_ids:
            placeholders = ",".join("?" for _ in neighbor_ids)
            for row in conn.execute(
                f"""
                SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                       p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                       p.weirdness_score, p.emotional_weight_score, p.intensity_score, p.pacing_score,
                       p.johnny_core_score, p.ai_summary
                FROM titles t
                LEFT JOIN taste_profiles p ON p.title_id = t.id
                WHERE t.id IN ({placeholders})
                """,
                neighbor_ids,
            ).fetchall():
                profiles[row["id"]] = row

    strong = []
    soft = []
    for edge in edges:
        neighbor_id = edge["target_id"] if edge["source_id"] == focus["id"] else edge["source_id"]
        profile = profiles.get(neighbor_id)
        if not profile:
            continue
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(profile.get(key)))
        item = format_match(profile, tags)
        item["edge_type"] = edge.get("edge_type") or "strong"
        item["confidence"] = edge.get("confidence") or edge["weight"]
        item["shared_traits"] = json_list(edge.get("shared_traits"))
        item["reason"] = edge["explanation"] or profile.get("ai_summary") or "Graph neighbor."
        item["rank_score"] = edge_rank_score(edge, focus_profile, profile)
        if item["edge_type"] == "soft":
            soft.append(item)
        else:
            strong.append(item)
    strong.sort(key=lambda item: item["rank_score"], reverse=True)
    soft.sort(key=lambda item: item["rank_score"], reverse=True)
    strong = [item for item in strong if float(item.get("confidence") or 0) >= MIN_SIMILARITY]
    soft = [item for item in soft if float(item.get("confidence") or 0) >= MIN_SIMILARITY]

    if not strong and not soft:
        return {
            "recommendation": f"{focus['title']} has no graph neighbors yet.",
            "why_it_fits": "There are no nearby titles above the current confidence threshold yet. This title needs more surrounding enriched catalog context before Taste Graph can place it confidently.",
            "nearby_titles": [],
            "confidence": 0.4,
            "tags_that_drove_answer": ["needs more graph context"],
            "best_matches": [],
            "weirdest_matches": [],
            "emotionally_heavier_matches": [],
            "safer_easier_watches": [],
            "why_these_fit": "No strong or soft connections are available for this title yet.",
            "tags_driving_recommendation": ["needs more graph context"],
            "answer_source": "local_graph",
        }

    combined = strong + soft
    base_weird = int(focus_profile.get("weirdness_score") or 0) if focus_profile else 0
    base_emotion = int(focus_profile.get("emotional_weight_score") or 0) if focus_profile else 0
    base_intensity = int(focus_profile.get("intensity_score") or 0) if focus_profile else 0
    best_matches = take_unique_matches(strong + soft, limit=8)
    weird_candidates = sorted(
        [item for item in combined if (item["scores"].get("weirdness") or 0) > base_weird],
        key=lambda item: ((item["scores"].get("weirdness") or 0), item.get("rank_score") or 0),
        reverse=True,
    )
    heavier_candidates = sorted(
        [item for item in combined if (item["scores"].get("emotional_weight") or 0) > base_emotion],
        key=lambda item: ((item["scores"].get("emotional_weight") or 0), item.get("rank_score") or 0),
        reverse=True,
    )
    safer_candidates = sorted(
        [item for item in combined if (item["scores"].get("intensity") or 10) < base_intensity],
        key=lambda item: ((item["scores"].get("intensity") or 10), -(item.get("rank_score") or 0), -((item["scores"].get("pacing") or 0))),
    )
    bridge_candidates = bridge_bucket(combined, focus_profile.get("primary_cluster") if focus_profile else None)
    buckets = build_curated_buckets(
        best_candidates=best_matches,
        weird_candidates=weird_candidates,
        heavier_candidates=heavier_candidates,
        safer_candidates=safer_candidates,
        bridge_candidates=bridge_candidates,
    )
    question_lower = normalize_question(question)
    primary_reason = combined[0]["reason"]
    if "why" in question_lower and "connect" in question_lower:
        primary_reason = (
            f"{focus['title']} leans toward {focus_profile.get('primary_cluster') or 'an outlier zone'}, "
            f"and its nearest neighbors share {', '.join(best_matches[0].get('shared_traits', [])[:3]) or 'a similar pressure profile'}."
        )
    return {
        "recommendation": f"Closest to {focus['title']}: {best_matches[0]['title']}.",
        "why_it_fits": primary_reason,
        "nearby_titles": [item["title"] for item in best_matches[:8]],
        "confidence": best_matches[0].get("confidence", 0.7),
        "tags_that_drove_answer": best_matches[0].get("shared_traits", [])[:6],
        "best_matches": buckets["best_matches"],
        "weirdest_matches": buckets["weirdest_matches"],
        "emotionally_heavier_matches": buckets["emotionally_heavier_matches"],
        "safer_easier_watches": buckets["safer_easier_watches"],
        "bridge_titles": buckets["bridge_titles"],
        "why_these_fit": "Returned instantly from local graph connections. Strong matches are listed first; soft matches are looser bridges.",
        "tags_driving_recommendation": best_matches[0].get("shared_traits", [])[:6],
        "matched_title": focus_profile["title"] if focus_profile else focus["title"],
        "answer_source": "local_graph",
    }


def deterministic_connection_answer(question: str, selected_title_id: Optional[int] = None) -> Optional[dict]:
    reasoning = connection_reasoning(question, selected_title_id)
    if not reasoning:
        return None
    source = reasoning["source_title"]
    target = reasoning["target_title"]
    shared_tags = reasoning.get("shared_tags", [])
    path = reasoning.get("path", [])
    signal_diff = reasoning.get("signal_diff", {})
    explanation_bits = []
    if shared_tags:
        explanation_bits.append(f"shared tags like {', '.join(shared_tags[:4])}")
    if reasoning.get("cluster_overlap"):
        explanation_bits.append(f"cluster relationship: {reasoning['cluster_overlap']}")
    if path and len(path) > 2:
        explanation_bits.append(f"a path through {' -> '.join(path)}")
    else:
        explanation_bits.append("a direct connection in the taste graph")
    if signal_diff:
        tightest = sorted(signal_diff.items(), key=lambda item: item[1]["difference"])[:2]
        explanation_bits.append(
            "closest signal alignment in "
            + ", ".join(item[0].replace("_", " ") for item in tightest)
        )
    explanation = (
        f"{source['title']} connects to {target['title']} through "
        + "; ".join(explanation_bits)
        + "."
    )
    target_match = compact_match_from_reasoning(reasoning)
    bridge_matches = bridge_matches_from_reasoning(reasoning)
    return {
        "recommendation": f"Why {source['title']} connects to {target['title']}",
        "why_it_fits": explanation,
        "nearby_titles": path[1:] if len(path) > 1 else [target["title"]],
        "confidence": reasoning.get("similarity_score", 0.0),
        "tags_that_drove_answer": shared_tags[:6],
        "best_matches": [target_match] if target_match else [],
        "weirdest_matches": [],
        "emotionally_heavier_matches": [],
        "safer_easier_watches": [],
        "bridge_titles": bridge_matches,
        "why_these_fit": explanation,
        "tags_driving_recommendation": shared_tags[:6],
        "graph_reasoning": reasoning,
        "answer_source": "deterministic_graph",
    }


def connection_reasoning(question: str, selected_title_id: Optional[int] = None) -> Optional[dict]:
    matched = matched_titles_from_question(question, limit=2)
    source = title_by_id(selected_title_id) if selected_title_id else None
    if source and matched:
        target = next((row for row in matched if row["id"] != source["id"]), None)
    else:
        target = None
    if not source and len(matched) >= 2:
        source, target = matched[0], matched[1]
    elif not source and matched:
        source = matched[0]
    if not source or not target:
        return None

    source_profile = fetch_title_profile(source["id"])
    target_profile = fetch_title_profile(target["id"])
    if not source_profile or not target_profile:
        return None

    path_nodes, path_edges = shortest_connection_path(source["id"], target["id"])
    source_tags = set(compact_title_payload(source_profile)["tags"])
    target_tags = set(compact_title_payload(target_profile)["tags"])
    shared_tags = sorted(source_tags & target_tags)
    signal_diff = {}
    for key in ("johnny_core", "weirdness", "emotional_weight", "pacing"):
        left_value = int(compact_title_payload(source_profile)["scores"].get(key) or 0)
        right_value = int(compact_title_payload(target_profile)["scores"].get(key) or 0)
        signal_diff[key] = {
            "source": left_value,
            "target": right_value,
            "difference": abs(left_value - right_value),
        }
    cluster_a = source_profile.get("primary_cluster") or "Outliers"
    cluster_b = target_profile.get("primary_cluster") or "Outliers"
    if cluster_a == cluster_b:
        cluster_overlap = cluster_a
    else:
        cluster_overlap = f"{cluster_a} -> {cluster_b}"
    edge_weights = [round(float(edge.get("confidence") or edge.get("weight") or 0), 3) for edge in path_edges]
    similarity_score = round(sum(edge_weights) / len(edge_weights), 3) if edge_weights else 0.0
    return {
        "source_title": {"id": source["id"], "title": source["title"]},
        "target_title": {"id": target["id"], "title": target["title"]},
        "shared_tags": shared_tags[:8],
        "signal_diff": signal_diff,
        "path": [node["title"] for node in path_nodes] if path_nodes else [source["title"], target["title"]],
        "cluster_overlap": cluster_overlap,
        "cluster": cluster_overlap,
        "similarity_score": similarity_score,
        "edge_weights": edge_weights,
    }


def fetch_title_profile(title_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id = ?
            LIMIT 1
            """,
            (title_id,),
        ).fetchone()
    if not row:
        return None
    return apply_resolved_clusters([row])[0]


def shortest_connection_path(source_id: int, target_id: int) -> tuple[list[dict], list[dict]]:
    if source_id == target_id:
        profile = fetch_title_profile(source_id)
        return ([profile] if profile else []), []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT e.source_title_id, e.target_title_id, e.weight, e.confidence, e.edge_type,
                   s.title AS source_title, t.title AS target_title
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            ORDER BY CASE e.edge_type WHEN 'strong' THEN 0 ELSE 1 END, e.confidence DESC, e.weight DESC
            """
        ).fetchall()
    adjacency: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for row in rows:
        edge = dict(row)
        adjacency.setdefault(row["source_title_id"], []).append((row["target_title_id"], edge))
        adjacency.setdefault(row["target_title_id"], []).append((row["source_title_id"], edge))

    queue = deque([source_id])
    parents: dict[int, tuple[Optional[int], Optional[dict[str, Any]]]] = {source_id: (None, None)}
    while queue:
        current = queue.popleft()
        for neighbor_id, edge in adjacency.get(current, []):
            if neighbor_id in parents:
                continue
            parents[neighbor_id] = (current, edge)
            if neighbor_id == target_id:
                queue.clear()
                break
            queue.append(neighbor_id)

    if target_id not in parents:
        source_profile = fetch_title_profile(source_id)
        target_profile = fetch_title_profile(target_id)
        return [row for row in (source_profile, target_profile) if row], []

    node_ids = []
    edge_rows = []
    current = target_id
    while current is not None:
        node_ids.append(current)
        parent, edge = parents[current]
        if edge:
            edge_rows.append(edge)
        current = parent
    node_ids.reverse()
    edge_rows.reverse()
    nodes = [fetch_title_profile(node_id) for node_id in node_ids]
    return [node for node in nodes if node], edge_rows


def compact_match_from_reasoning(reasoning: dict) -> Optional[dict]:
    target_profile = fetch_title_profile(reasoning["target_title"]["id"])
    if not target_profile:
        return None
    tags = compact_title_payload(target_profile)["tags"]
    item = format_match(target_profile, tags)
    item["confidence"] = reasoning.get("similarity_score", 0.0)
    item["shared_traits"] = reasoning.get("shared_tags", [])[:5]
    item["reason"] = "Computed from shared tags, signal similarity, cluster overlap, and path strength."
    return item


def bridge_matches_from_reasoning(reasoning: dict) -> list[dict]:
    path = reasoning.get("path", [])
    if len(path) <= 2:
        return []
    matches = []
    for title_name in path[1:-1]:
        row = matched_title_from_question(title_name)
        if not row:
            continue
        profile = fetch_title_profile(row["id"])
        if not profile:
            continue
        tags = compact_title_payload(profile)["tags"]
        item = format_match(profile, tags)
        item["reason"] = "Bridge title on the shortest connection path."
        matches.append(item)
    return take_unique_matches(matches, limit=5)


def title_by_id(title_id: Optional[int]) -> Optional[dict]:
    if not title_id:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, title, year, type, source, primary_cluster
            FROM titles
            WHERE id = ?
            """,
            (title_id,),
        ).fetchone()
    if not row:
        return None
    return apply_resolved_clusters([row])[0]


def edge_rank_score(edge: dict, focus: Optional[dict], neighbor: dict) -> float:
    confidence = float(edge.get("confidence") or edge.get("weight") or 0)
    edge_weight = float(edge.get("weight") or 0)
    trait_bonus = min(len(json_list(edge.get("shared_traits"))) * 0.08, 0.32)
    score_similarity = 0.0
    if focus:
        for key in ("weirdness_score", "emotional_weight_score", "johnny_core_score", "pacing_score"):
            a = int(focus.get(key) or 5)
            b = int(neighbor.get(key) or 5)
            score_similarity += max(0.0, 1 - abs(a - b) / 9) * 0.08
    edge_type_bonus = 0.35 if (edge.get("edge_type") or "strong") == "strong" else 0.0
    return confidence + edge_weight + trait_bonus + score_similarity + edge_type_bonus


def take_unique_matches(items: list[dict], used_ids: Optional[set] = None, limit: int = 5) -> list[dict]:
    seen = used_ids if used_ids is not None else set()
    unique = []
    for item in items:
        item_id = item.get("id")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        unique.append(item)
        if len(unique) >= limit:
            break
    return unique


def bridge_bucket(items: list[dict], focus_cluster: Optional[str]) -> list[dict]:
    cross_cluster = [
        item for item in items
        if item.get("edge_type") == "soft" and item.get("cluster") != focus_cluster
    ]
    same_cluster_soft = [
        item for item in items
        if item.get("edge_type") == "soft" and item.get("cluster") == focus_cluster
    ]
    cross_cluster.sort(key=lambda item: item.get("rank_score") or item.get("confidence") or 0, reverse=True)
    same_cluster_soft.sort(key=lambda item: item.get("rank_score") or item.get("confidence") or 0, reverse=True)
    return cross_cluster + same_cluster_soft


def build_curated_buckets(
    best_candidates: list[dict],
    weird_candidates: list[dict],
    heavier_candidates: list[dict],
    safer_candidates: list[dict],
    bridge_candidates: list[dict],
) -> dict:
    used_ids: set = set()
    best_matches = take_unique_matches(best_candidates, used_ids, limit=6)
    weirdest_matches = take_unique_matches(weird_candidates, used_ids, limit=4)
    emotionally_heavier_matches = take_unique_matches(heavier_candidates, used_ids, limit=4)
    safer_easier_watches = take_unique_matches(safer_candidates, used_ids, limit=4)
    bridge_titles = take_unique_matches(bridge_candidates, used_ids, limit=4)
    return {
        "best_matches": best_matches,
        "weirdest_matches": weirdest_matches,
        "emotionally_heavier_matches": emotionally_heavier_matches,
        "safer_easier_watches": safer_easier_watches,
        "bridge_titles": bridge_titles,
    }


def local_taste_answer(question: str) -> dict:
    lowered = question.lower()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score,
                   p.pacing_score, p.johnny_core_score, p.ai_summary
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY p.johnny_core_score DESC, p.emotional_weight_score DESC, p.weirdness_score DESC
            """
        ).fetchall()
        edge_rows = conn.execute(
            """
            SELECT s.title AS source, t.title AS target, e.weight, e.confidence,
                   e.edge_type, e.shared_traits, e.explanation
            FROM edges e
            JOIN titles s ON s.id = e.source_title_id
            JOIN titles t ON t.id = e.target_title_id
            ORDER BY e.weight DESC
            """
        ).fetchall()

    rows = apply_resolved_clusters(rows)
    if not rows:
        return {
            "recommendation": "Seed the demo graph first.",
            "why_it_fits": "There are no taste profiles yet. Run python3 scripts/seed_demo.py, then ask again.",
            "nearby_titles": [],
            "confidence": 0.95,
            "tags_that_drove_answer": ["empty graph"],
        }

    tag_terms = [
        "tech paranoia",
        "body horror",
        "systems under pressure",
        "moral rot",
        "institutional decay",
        "identity breakdown",
        "weird sci-fi dread",
        "surreal dread",
        "war trauma",
        "spiritual brutality",
        "anti-hero spiral",
        "puzzle-box mystery",
        "simulation anxiety",
        "corporate manipulation",
        "existential horror",
        "emotionally devastating",
        "emotional devastation",
    ]
    requested_tags = [term for term in tag_terms if term in lowered]
    focus_title = None
    for row in rows:
        if row["title"].lower() in lowered:
            focus_title = row["title"]
            break
    edge_boosts = {}
    focus_neighbors = []
    if focus_title:
        for edge in edge_rows:
            other = None
            if edge["source"] == focus_title:
                other = edge["target"]
            elif edge["target"] == focus_title:
                other = edge["source"]
            if other:
                multiplier = 35 if (edge.get("edge_type") or "strong") == "strong" else 14
                edge_boosts[other] = float(edge.get("confidence") or edge["weight"]) * multiplier
                focus_neighbors.append(other)
    ranked = []
    for row in rows:
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(row.get(key)))
        tag_text = " ".join(tags).lower()
        score = int(row["johnny_core_score"]) * 2 + int(row["emotional_weight_score"]) + int(row["weirdness_score"])
        score += edge_boosts.get(row["title"], 0)
        if row["title"] == focus_title:
            score -= 50
        score += sum(22 for term in requested_tags if term in tag_text)
        if "not too slow" in lowered or "fast" in lowered:
            score += int(row["pacing_score"]) * 2
        if "weird" in lowered:
            score += int(row["weirdness_score"]) * 2
        if "devastating" in lowered or "emotional" in lowered:
            score += int(row["emotional_weight_score"]) * 2
        confidence = min(0.95, max(0.3, score / 100))
        ranked.append((score, confidence, row, tags))
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0]
    base_row = best[2]
    base_weird = int(base_row["weirdness_score"] or 0)
    base_emotion = int(base_row["emotional_weight_score"] or 0)
    base_intensity = int(base_row["intensity_score"] or 0)
    best_candidates = [format_match(item[2], item[3], confidence=item[1]) for item in ranked[:10]]
    weird_candidates = [
        format_match(item[2], item[3], confidence=item[1])
        for item in sorted(
            [item for item in ranked if int(item[2]["weirdness_score"] or 0) > base_weird],
            key=lambda item: (int(item[2]["weirdness_score"] or 0), item[0]),
            reverse=True,
        )
    ]
    emotionally_heavier = [
        format_match(item[2], item[3], confidence=item[1])
        for item in sorted(
            [item for item in ranked if int(item[2]["emotional_weight_score"] or 0) > base_emotion],
            key=lambda item: (int(item[2]["emotional_weight_score"] or 0), item[0]),
            reverse=True,
        )
    ]
    safer_candidates = [
        format_match(item[2], item[3], confidence=item[1])
        for item in sorted(
            [item for item in ranked if int(item[2]["intensity_score"] or 10) < base_intensity],
            key=lambda item: (int(item[2]["intensity_score"] or 10), -item[0], -(int(item[2]["pacing_score"] or 0))),
        )
    ]
    nearby = []
    best_title = best[2]["title"]
    for edge in edge_rows[:40]:
        if edge["source"] == best_title:
            nearby.append(edge["target"])
        elif edge["target"] == best_title:
            nearby.append(edge["source"])
    tags_counter = Counter(best[3])
    driving_tags = requested_tags or [tag for tag, _ in tags_counter.most_common(5)]
    bridge_candidates = [
        format_match(item[2], item[3], confidence=item[1])
        for item in ranked
        if (item[2].get("primary_cluster") or "Outliers") != (base_row.get("primary_cluster") or "Outliers")
    ]
    buckets = build_curated_buckets(
        best_candidates=best_candidates,
        weird_candidates=weird_candidates,
        heavier_candidates=emotionally_heavier,
        safer_candidates=safer_candidates,
        bridge_candidates=bridge_candidates,
    )
    return {
        "recommendation": f"Start with {best_title}.",
        "why_it_fits": best[2]["ai_summary"]
        or "It ranks highly in the local taste graph based on Johnny-core score, emotional weight, weirdness, and matching tags.",
        "nearby_titles": focus_neighbors[:5] or nearby[:5] or [item[2]["title"] for item in ranked[1:6]],
        "confidence": best[1],
        "tags_that_drove_answer": driving_tags[:6],
        "best_matches": buckets["best_matches"],
        "weirdest_matches": buckets["weirdest_matches"],
        "emotionally_heavier_matches": buckets["emotionally_heavier_matches"],
        "safer_easier_watches": buckets["safer_easier_watches"],
        "bridge_titles": buckets["bridge_titles"],
        "why_these_fit": (
            f"These are nearest to {focus_title} by connection strength, then adjusted for tags and scores."
            if focus_title
            else best[2]["ai_summary"]
            or "These are ranked from the current graph using tags, scores, clusters, and connection proximity."
        ),
        "tags_driving_recommendation": driving_tags[:6],
    }


def format_match(row: dict, tags: list[str], confidence: Optional[float] = None) -> dict:
    return {
        "id": row.get("id"),
        "title": row["title"],
        "year": row["year"],
        "cluster": row.get("primary_cluster") or "Outliers",
        "source": row.get("source") or "manual",
        "confidence": confidence,
        "scores": {
            "johnny_core": row["johnny_core_score"],
            "weirdness": row["weirdness_score"],
            "emotional_weight": row["emotional_weight_score"],
            "intensity": row.get("intensity_score"),
            "pacing": row["pacing_score"],
        },
        "tags": tags[:5],
        "edge_type": row.get("edge_type", "strong"),
        "reason": row.get("ai_summary") or ("Needs enrichment." if not tags else ", ".join(tags[:3])),
    }


def compact_title_payload(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    tags = []
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
        tags.extend(json_list(row.get(key)))
    return {
        "title": row["title"],
        "year": row["year"],
        "type": row["type"],
        "source": row["source"],
        "cluster": row.get("primary_cluster") or "Outliers",
        "tags": tags[:10],
        "scores": {
            "weirdness": row.get("weirdness_score"),
            "emotional_weight": row.get("emotional_weight_score"),
            "intensity": row.get("intensity_score"),
            "pacing": row.get("pacing_score"),
            "johnny_core": row.get("johnny_core_score"),
        },
        "summary": row.get("ai_summary"),
    }
