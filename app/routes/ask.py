import json
import logging
import re
import time
from collections import Counter, deque
from typing import Any, Optional

import openai
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import get_connection, now_iso
from app.graph_neighbors import get_title_graph_neighbors
from app.graph_builder import apply_resolved_clusters
from app.models import json_list
from app.openai_client import OpenAITasteClient, server_diagnostic_response

router = APIRouter()
ASK_CACHE_SCHEMA_VERSION = "6"
ASK_EXPLAIN_CACHE_VERSION = "1"


class AskRequest(BaseModel):
    question: str = ""
    query: Optional[str] = None
    explain_with_ai: bool = False
    selected_title_id: Optional[int] = None
    intent: Optional[str] = None


class AskExplainRequest(BaseModel):
    question: str = ""
    query: Optional[str] = None
    selected_title_id: Optional[int] = None
    intent: Optional[str] = None


logger = logging.getLogger("taste_graph.ask")


def log_missing_neighbors(
    question: str,
    selected_title_id: Optional[int],
    focus: Optional[dict],
    neighbor_data: Optional[dict],
    reason: str,
) -> None:
    settings = get_settings()
    logger.warning(
        "ask no-neighbors fallback request=%r selected_title_id=%s resolved_title_id=%s resolved_title=%r db_path=%s edge_count=%s edge_types=%s is_enriched=%s is_mapped=%s reason=%s",
        question,
        selected_title_id,
        focus.get("id") if focus else None,
        focus.get("title") if focus else None,
        settings.sqlite_path,
        (neighbor_data or {}).get("edge_count"),
        (neighbor_data or {}).get("edge_type_counts"),
        focus.get("enrichment_status") if focus else None,
        None if not focus else ((neighbor_data or {}).get("edge_count", 0) > 0),
        reason,
    )


@router.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse("ask.html", {"request": request})


@router.post("/api/ask")
def api_ask(payload: AskRequest) -> dict:
    question_text = payload.query or payload.question
    total_start = time.perf_counter()
    blocked = server_diagnostic_response(question_text)
    if blocked:
        store_question(question_text, blocked)
        return blocked

    graph_version = current_graph_version()
    normalized = normalize_question(question_text)
    mode = "ai" if payload.explain_with_ai else "fast"
    intent = resolve_ask_intent(question_text, payload.intent)
    cache_key = cache_key_for(mode, normalized, payload.selected_title_id, intent)
    cached = get_cached_answer(cache_key, graph_version)
    if cached:
        cached["cached"] = True
        logger.info(
            "ask cache hit intent=%s selected_title_id=%s cache_key=%s total=%.3fs",
            intent,
            payload.selected_title_id,
            cache_key,
            time.perf_counter() - total_start,
        )
        return cached

    retrieval_start = time.perf_counter()
    warnings: list[str] = []
    try:
        fast_answer = similarity_fast_answer(question_text, payload.selected_title_id, intent)
    except Exception as exc:
        logger.exception("ask fast-answer failure intent=%s selected_title_id=%s query=%r", intent, payload.selected_title_id, question_text)
        fast_answer = None
        warnings.append("Local graph bucketing partially failed.")
    try:
        deterministic_answer = deterministic_connection_answer(question_text, payload.selected_title_id)
    except Exception:
        logger.exception("ask deterministic failure intent=%s selected_title_id=%s query=%r", intent, payload.selected_title_id, question_text)
        deterministic_answer = None
        warnings.append("Connection reasoning was unavailable.")
    retrieval_time = time.perf_counter() - retrieval_start

    openai_time = 0.0
    if fast_answer and not payload.explain_with_ai:
        answer = fast_answer
    elif deterministic_answer and not payload.explain_with_ai:
        answer = deterministic_answer
    elif get_settings().openai_api_key and should_use_ai(question_text, payload.explain_with_ai, fast_answer, deterministic_answer):
        context = build_focused_context(question_text, fast_answer, payload.selected_title_id, deterministic_answer)
        openai_start = time.perf_counter()
        try:
            answer = OpenAITasteClient().answer_question(question_text, context, timeout_seconds=3.0)
        except Exception:
            answer = deterministic_answer or fast_answer or local_taste_answer(question_text)
        openai_time = time.perf_counter() - openai_start
        if fast_answer:
            answer.setdefault("fast_result", fast_answer)
        if deterministic_answer:
            answer.setdefault("graph_reasoning", deterministic_answer.get("graph_reasoning"))
    else:
        answer = deterministic_answer or fast_answer or local_taste_answer(question_text)

    answer = normalize_ask_response(answer, payload.selected_title_id, intent, warnings, question_text)
    answer.setdefault("can_explain_with_ai", bool(get_settings().openai_api_key) and not payload.explain_with_ai)
    answer["timing"] = {
        "local_retrieval_seconds": round(retrieval_time, 4),
        "openai_seconds": round(openai_time, 4),
        "total_seconds": round(time.perf_counter() - total_start, 4),
    }
    set_cached_answer(cache_key, graph_version, question_text, answer)
    store_question(question_text, answer)
    logger.info(
        "ask timings intent=%s selected_title_id=%s cache_key=%s local=%.3fs openai=%.3fs total=%.3fs mode=%s",
        intent,
        payload.selected_title_id,
        cache_key,
        retrieval_time,
        openai_time,
        time.perf_counter() - total_start,
        mode,
    )
    return answer


@router.post("/api/ask/explain")
def api_ask_explain(payload: AskExplainRequest) -> dict:
    question_text = payload.query or payload.question
    intent = resolve_ask_intent(question_text, payload.intent)
    settings = get_settings()
    focus = title_by_id(payload.selected_title_id) if payload.selected_title_id else None
    fast_answer = similarity_fast_answer(question_text, payload.selected_title_id, intent)
    top_match_ids = [item.get("id") for item in (fast_answer or {}).get("best_matches", []) if isinstance(item, dict) and item.get("id")]
    normalized = normalize_question(question_text)
    cache_key = explain_cache_key_for(normalized, payload.selected_title_id, intent, top_match_ids)
    graph_version = current_graph_version()
    cached = get_cached_answer(cache_key, graph_version)
    if cached:
        cached["cached"] = True
        logger.info(
            "ask explain cache hit selected_title_id=%s selected_title=%r intent=%s cache_key=%s ok=%s",
            payload.selected_title_id,
            (focus or {}).get("title"),
            intent,
            cache_key,
            cached.get("ok"),
        )
        return cached
    section_counts = {k: len(v) for k, v in (fast_answer or {}).get("sections", {}).items()} if fast_answer else {}
    best_matches = (fast_answer or {}).get("best_matches") or []
    bridge_titles = (fast_answer or {}).get("bridge_titles") or []
    top_match_titles = [
        item.get("title")
        for item in (best_matches + bridge_titles)
        if isinstance(item, dict) and item.get("title")
    ][:3]
    logger.info(
        "ask_explain_start selected_title_id=%s selected_title=%r intent=%s query=%r has_openai_key=%s model=%s openai_sdk_version=%s local_bucket_counts=%s best_matches_count=%s bridge_titles_count=%s top_match_titles=%s payload_bytes=%s",
        payload.selected_title_id,
        (focus or {}).get("title"),
        intent,
        payload.question,
        bool(settings.openai_api_key),
        settings.openai_model,
        getattr(openai, "__version__", "unknown"),
        section_counts,
        len(best_matches),
        len(bridge_titles),
        top_match_titles,
        len(payload.question.encode("utf-8")),
    )
    if not settings.openai_api_key:
        logger.info("ask_explain_failed selected_title_id=%s intent=%s reason=missing-openai-key", payload.selected_title_id, intent)
        return {"ok": False, "error_code": "missing_openai_api_key", "user_message": "AI explanation unavailable: missing OpenAI API key.", "error": "AI explanation unavailable: missing OpenAI API key."}
    try:
        deterministic_answer = deterministic_connection_answer(payload.question, payload.selected_title_id)
        context = build_ai_explain_context(payload.question, fast_answer, payload.selected_title_id, deterministic_answer, intent)
        logger.info(
            "ask explain context selected_title_id=%s selected_title=%r intent=%s prompt_length=%s",
            payload.selected_title_id,
            (focus or {}).get("title"),
            intent,
            len(context),
        )
        response = OpenAITasteClient().explain_graph_answer(payload.question, context, timeout_seconds=10.0)
        explanation = response.get("explanation") or "AI explanation unavailable right now."
        title = response.get("title") or "AI explanation"
        result = {
            "ok": True,
            "intent": intent,
            "selected_title_id": payload.selected_title_id,
            "title": title,
            "explanation": explanation,
        }
        set_cached_answer(cache_key, graph_version, payload.question, result)
        logger.info(
            "ask_explain_success selected_title_id=%s selected_title=%r intent=%s cache_key=%s ai_response_length=%s",
            payload.selected_title_id,
            (focus or {}).get("title"),
            intent,
            cache_key,
            len(explanation),
        )
        return result
    except Exception as exc:
        logger.exception(
            "ask_explain_failed selected_title_id=%s selected_title=%r intent=%s query=%r exception_type=%s exception_message=%s",
            payload.selected_title_id,
            (focus or {}).get("title"),
            intent,
            payload.question,
            type(exc).__name__,
            str(exc),
        )
        error_code = type(exc).__name__
        user_message = "AI explanation unavailable right now."
        if error_code == "APIConnectionError":
            user_message = "AI explanation unavailable: couldn’t reach OpenAI."
        elif error_code == "AuthenticationError":
            user_message = "AI explanation unavailable: OpenAI authentication failed."
        elif error_code == "APITimeoutError":
            user_message = "AI explanation unavailable: OpenAI timed out while explaining these graph relationships."
        elif error_code == "NotFoundError":
            user_message = f"AI explanation unavailable: model {settings.openai_model} was not found."
        return {"ok": False, "error_code": error_code, "user_message": user_message, "error": user_message}


@router.get("/api/debug/openai")
def api_debug_openai() -> dict:
    settings = get_settings()
    client_configured = False
    if settings.openai_api_key:
        try:
            OpenAITasteClient()
            client_configured = True
        except Exception:
            client_configured = False
    return {
        "openai_key_present": bool(settings.openai_api_key),
        "model": settings.openai_model or "gpt-4o-mini",
        "client_configured": client_configured,
        "openai_sdk_version": getattr(openai, "__version__", "unknown"),
    }


def normalize_question(question: str) -> str:
    return " ".join(question.strip().lower().split())


def resolve_ask_intent(question: str, explicit_intent: Optional[str] = None) -> str:
    if explicit_intent:
        return explicit_intent
    lowered = normalize_question(question)
    if "why" in lowered and "connect" in lowered:
        return "why_connects"
    if "weirder" in lowered:
        return "weirder"
    if "heavier" in lowered or "emotionally heavier" in lowered:
        return "heavier"
    if "safer" in lowered or "easier" in lowered or "lighter" in lowered:
        return "safer"
    if "similar to this" in lowered or "similar to" in lowered or "movies like" in lowered or "shows like" in lowered:
        return "similar"
    return "closest"


def normalize_ask_response(answer: dict, selected_title_id: Optional[int], intent: str, warnings: list[str], requested_query: str) -> dict:
    focus = title_by_id(selected_title_id) if selected_title_id else None
    answer = dict(answer or {})
    answer["ok"] = True
    answer["intent"] = intent
    answer["requested_query"] = requested_query
    answer["display_title"] = requested_query or answer.get("recommendation")
    answer["selected_title_id"] = selected_title_id
    answer["selected_title_name"] = (focus or {}).get("title") or answer.get("matched_title")
    answer["warnings"] = warnings
    if answer.get("sections") and isinstance(answer.get("sections"), dict):
        normalized_sections = {}
        for key, section in answer["sections"].items():
            if not isinstance(section, dict):
                continue
            normalized_sections[key] = {
                "items": [dict(item) if isinstance(item, dict) else item for item in (section.get("items", []) or [])],
                "mode": section.get("mode", "strict"),
                "label": section.get("label"),
                "subtitle": section.get("subtitle"),
                "empty_reason": section.get("empty_reason"),
            }
        answer["sections"] = normalized_sections
    else:
        answer["sections"] = {
            "best_matches": {"items": [dict(item) if isinstance(item, dict) else item for item in (answer.get("best_matches", []) or [])], "mode": "strict", "label": answer.get("bucket_titles", {}).get("best_matches", "Best matches"), "subtitle": None, "empty_reason": None},
            "weirdest_matches": {"items": [dict(item) if isinstance(item, dict) else item for item in (answer.get("weirdest_matches", []) or [])], "mode": "strict", "label": answer.get("bucket_titles", {}).get("weirdest_matches", "Weirder picks"), "subtitle": None, "empty_reason": answer.get("bucket_empty_reasons", {}).get("weirdest_matches")},
            "emotionally_heavier_matches": {"items": [dict(item) if isinstance(item, dict) else item for item in (answer.get("emotionally_heavier_matches", []) or [])], "mode": "strict", "label": answer.get("bucket_titles", {}).get("emotionally_heavier_matches", "Emotionally heavier"), "subtitle": None, "empty_reason": answer.get("bucket_empty_reasons", {}).get("emotionally_heavier_matches")},
            "safer_easier_watches": {"items": [dict(item) if isinstance(item, dict) else item for item in (answer.get("safer_easier_watches", []) or [])], "mode": "strict", "label": answer.get("bucket_titles", {}).get("safer_easier_watches", "Safer / easier"), "subtitle": None, "empty_reason": answer.get("bucket_empty_reasons", {}).get("safer_easier_watches")},
            "bridge_titles": {"items": [dict(item) if isinstance(item, dict) else item for item in (answer.get("bridge_titles", []) or [])], "mode": "strict", "label": answer.get("bucket_titles", {}).get("bridge_titles", "Bridge titles"), "subtitle": None, "empty_reason": answer.get("bucket_empty_reasons", {}).get("bridge_titles")},
        }
    return answer


def cache_key_for(mode: str, normalized: str, selected_title_id: Optional[int], intent: Optional[str] = None) -> str:
    return f"v{ASK_CACHE_SCHEMA_VERSION}:{mode}:title={selected_title_id or 'none'}:intent={(intent or 'auto')}:{normalized}"


def explain_cache_key_for(
    normalized: str,
    selected_title_id: Optional[int],
    intent: Optional[str],
    top_match_ids: list[int],
) -> str:
    top_fragment = ",".join(str(item) for item in top_match_ids[:6]) or "none"
    return f"explain:v{ASK_EXPLAIN_CACHE_VERSION}:title={selected_title_id or 'none'}:intent={(intent or 'auto')}:{top_fragment}:{normalized}"


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
    compact_titles = []
    titles = apply_resolved_clusters(titles)
    if focus_profile:
        focus_profile = apply_resolved_clusters([focus_profile])[0]
    focused_edges = get_title_graph_neighbors(focus["id"])["edges"][:30] if focus else []
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
                    "source": edge["source_title"],
                    "target": edge["target_title"],
                    "edge_type": edge.get("edge_type") or "strong",
                    "confidence": edge.get("confidence") or edge["weight"],
                    "traits": json_list(edge["shared_traits"]),
                    "why": edge["explanation"],
                }
                for edge in focused_edges
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


def build_ai_explain_context(
    question: str,
    fast_answer: Optional[dict] = None,
    selected_title_id: Optional[int] = None,
    deterministic_answer: Optional[dict] = None,
    intent: Optional[str] = None,
) -> str:
    focus = title_by_id(selected_title_id) if selected_title_id else matched_title_from_question(question)
    focus_profile = fetch_title_profile(focus["id"]) if focus else None
    bucket_titles = (fast_answer or {}).get("bucket_titles") or {}
    bucket_empty_reasons = (fast_answer or {}).get("bucket_empty_reasons") or {}
    lead_bucket_by_intent = {
        "closest": "best_matches",
        "similar": "best_matches",
        "weirder": "weirdest_matches",
        "heavier": "emotionally_heavier_matches",
        "safer": "safer_easier_watches",
        "why_connects": "bridge_titles",
        "bridge": "bridge_titles",
    }
    lead_bucket = lead_bucket_by_intent.get(intent or "", "best_matches")

    def summarize_match(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": item.get("title"),
            "year": item.get("year"),
            "cluster": item.get("cluster"),
            "scores": item.get("scores") or {},
            "edge_type": item.get("edge_type") or "strong",
            "edge_score": item.get("confidence"),
            "edge_reason": item.get("reason"),
            "shared_traits": (item.get("shared_traits") or [])[:4],
            "tags": (item.get("tags") or [])[:4],
        }

    bucket_names = []
    for name in (lead_bucket, "best_matches", "bridge_titles"):
        if name and name not in bucket_names:
            bucket_names.append(name)
    local_buckets = {}
    for name in bucket_names:
        items = [
            summarize_match(item)
            for item in ((fast_answer or {}).get(name) or [])[:3]
            if isinstance(item, dict)
        ]
        local_buckets[name] = {
            "title": bucket_titles.get(name),
            "count": len(items),
            "empty_reason": bucket_empty_reasons.get(name),
            "items": items,
        }

    explanation_payload = {
        "graph_type": "Plex taste graph",
        "domain_rules": {
            "nodes_are": "movies and TV titles from a Plex library",
            "edges_are": "taste-similarity connections between titles",
            "do_not_treat_as": "locations, routes, points of interest, or physical graph traversal",
        },
        "question": question,
        "intent": intent,
        "selected_title": compact_title_payload(focus_profile) if focus_profile else None,
        "selected_title_detail": summarize_title_relationship_profile(focus_profile) if focus_profile else None,
        "deterministic_graph_reasoning": ((deterministic_answer or {}).get("graph_reasoning") or "")[:900],
        "local_graph_answer_summary": {
            "recommendation": (fast_answer or {}).get("recommendation"),
            "why_it_fits": (fast_answer or {}).get("why_it_fits"),
            "why_these_fit": (fast_answer or {}).get("why_these_fit"),
        },
        "lead_bucket": {
            "name": lead_bucket,
            "title": bucket_titles.get(lead_bucket),
            "empty_reason": bucket_empty_reasons.get(lead_bucket),
        },
        "local_buckets": local_buckets,
        "top_match_titles": [
            item.get("title")
            for item in ((fast_answer or {}).get("best_matches") or [])[:3]
            if isinstance(item, dict) and item.get("title")
        ],
        "explanation_instructions": [
            "Explain only the already-returned local graph evidence.",
            "If best matches or bridge titles exist, do not claim there are no edges or no graph context.",
            "Use plain language for movie and TV taste, tone, themes, style, mood, and emotional pressure.",
            "Mention uncertainty when a result is a bridge, soft, or looser same-neighborhood alternative.",
            "Do not invent titles, tags, clusters, or connections.",
        ],
    }
    return json.dumps(explanation_payload, ensure_ascii=False)


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


def load_cluster_candidate_matches(
    focus_profile: Optional[dict],
    existing_ids: set[int],
    limit: int = 18,
) -> list[dict]:
    if not focus_profile:
        return []
    focus_cluster = focus_profile.get("primary_cluster")
    if not focus_cluster:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score, p.pacing_score,
                   p.johnny_core_score, p.ai_summary, p.recommendation_hooks, p.closest_viewing_context
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id != ? AND t.enrichment_status = 'enriched' AND t.primary_cluster = ?
            ORDER BY p.johnny_core_score DESC, p.weirdness_score DESC
            LIMIT ?
            """,
            (focus_profile["id"], focus_cluster, limit * 3),
        ).fetchall()
    matches = []
    for profile in apply_resolved_clusters(rows):
        if profile["id"] in existing_ids:
            continue
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
            tags.extend(json_list(profile.get(key)))
        context_terms = str(profile.get("closest_viewing_context") or "").strip()
        if context_terms:
            tags.extend([part.strip() for part in context_terms.replace(";", ",").split(",") if part.strip()])
        item = format_match(profile, tags)
        item["edge_type"] = "cluster_nearby"
        item["confidence"] = score_profile_similarity(focus_profile, profile)
        item["shared_traits"] = shared_profile_terms(focus_profile, profile)
        item["reason"] = (
            f"Nearby cluster alternative in {profile.get('primary_cluster') or 'the same neighborhood'}."
        )
        item["rank_score"] = item["confidence"]
        matches.append(item)
        if len(matches) >= limit:
            break
    return matches


WEIRD_TERMS = {
    "surreal", "dreamlike", "uncanny", "psychological", "abstract", "fragmented identity",
    "identity fracture", "existential", "hallucinatory", "nonlinear", "absurd", "experimental",
    "dissociative", "paranoid", "strange", "off-center", "haunting",
}

HEAVY_TERMS = {
    "grief", "trauma", "dread", "moral rot", "collapse", "despair", "bleak", "devastating",
    "brutal", "war", "guilt", "psychological pressure", "emotional devastation", "alienation",
    "obsession", "loss", "violence", "institutional decay",
}

LIGHTER_TERMS = {
    "playful", "funny", "warm", "whimsical", "adventure", "charming", "nostalgic", "romantic",
    "lighter", "comedic", "crowd-pleasing",
}


def item_text_blob(item: dict[str, Any]) -> str:
    bits: list[str] = []
    bits.extend(str(tag) for tag in (item.get("tags") or []))
    bits.extend(str(tag) for tag in (item.get("shared_traits") or []))
    if item.get("reason"):
        bits.append(str(item["reason"]))
    if item.get("cluster"):
        bits.append(str(item["cluster"]))
    return " ".join(bit.lower() for bit in bits if bit)


def count_terms(item: dict[str, Any], terms: set[str]) -> int:
    blob = item_text_blob(item)
    return sum(1 for term in terms if term in blob)


def cluster_match_bonus(item: dict[str, Any], focus_cluster: Optional[str]) -> float:
    return 0.35 if focus_cluster and item.get("cluster") == focus_cluster else 0.0


def item_shared_count(item: dict[str, Any]) -> int:
    return len(item.get("shared_traits") or [])


def load_related_candidate_matches(
    focus_profile: Optional[dict],
    existing_ids: set[int],
    limit: int = 24,
) -> list[dict]:
    if not focus_profile:
        return []
    focus_terms = []
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
        focus_terms.extend(json_list(focus_profile.get(key)))
    context_terms = str(focus_profile.get("closest_viewing_context") or "").strip()
    if context_terms:
        focus_terms.extend([part.strip() for part in context_terms.replace(";", ",").split(",") if part.strip()])
    normalized_terms = {str(term).strip().lower() for term in focus_terms if str(term).strip()}
    if not normalized_terms:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.primary_cluster,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score, p.pacing_score,
                   p.johnny_core_score, p.ai_summary, p.recommendation_hooks, p.closest_viewing_context
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id != ? AND t.enrichment_status = 'enriched'
            ORDER BY p.johnny_core_score DESC, p.weirdness_score DESC
            LIMIT 400
            """,
            (focus_profile["id"],),
        ).fetchall()
    matches = []
    for profile in apply_resolved_clusters(rows):
        if profile["id"] in existing_ids:
            continue
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
            tags.extend(json_list(profile.get(key)))
        context_value = str(profile.get("closest_viewing_context") or "").strip()
        if context_value:
            tags.extend([part.strip() for part in context_value.replace(";", ",").split(",") if part.strip()])
        lowered_tags = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
        overlap = [tag for tag in tags if str(tag).strip().lower() in normalized_terms]
        if not overlap and profile.get("primary_cluster") != focus_profile.get("primary_cluster"):
            continue
        item = format_match(profile, tags)
        item["edge_type"] = "related_fallback"
        item["confidence"] = score_profile_similarity(focus_profile, profile)
        item["shared_traits"] = overlap[:6] or shared_profile_terms(focus_profile, profile)
        item["reason"] = (
            f"Shared neighborhood alternative via {' / '.join(item['shared_traits'][:3])}."
            if item["shared_traits"]
            else f"Same-cluster alternative in {profile.get('primary_cluster') or 'a nearby neighborhood'}."
        )
        item["rank_score"] = item["confidence"] + min(len(item["shared_traits"]) * 0.12, 0.48)
        matches.append(item)
    matches.sort(key=lambda item: item.get("rank_score") or 0, reverse=True)
    return take_unique_matches(matches, limit=limit)


def score_profile_similarity(focus: dict, neighbor: dict) -> float:
    score = 0.0
    for key in ("weirdness_score", "emotional_weight_score", "johnny_core_score", "pacing_score", "intensity_score"):
        a = int(focus.get(key) or 5)
        b = int(neighbor.get(key) or 5)
        score += max(0.0, 1 - abs(a - b) / 9)
    return round(score / 5, 3)


def shared_profile_terms(focus: dict, neighbor: dict, limit: int = 5) -> list[str]:
    left = []
    right = []
    for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags", "recommendation_hooks"):
        left.extend(json_list(focus.get(key)))
        right.extend(json_list(neighbor.get(key)))
    left.extend([part.strip() for part in str(focus.get("closest_viewing_context") or "").replace(";", ",").split(",") if part.strip()])
    right.extend([part.strip() for part in str(neighbor.get("closest_viewing_context") or "").replace(";", ",").split(",") if part.strip()])
    overlap = []
    right_set = {str(item).strip().lower(): str(item).strip() for item in right if str(item).strip()}
    for item in left:
        value = str(item).strip()
        if not value:
            continue
        match = right_set.get(value.lower())
        if match and match not in overlap:
            overlap.append(match)
        if len(overlap) >= limit:
            break
    return overlap


def build_ask_recommendation_buckets(selected_title_id: int) -> Optional[dict]:
    focus_profile = fetch_title_profile(selected_title_id)
    if not focus_profile:
        return None
    neighbor_data = get_title_graph_neighbors(selected_title_id)
    edges = neighbor_data["edges"]
    matches = []
    existing_ids = {selected_title_id}
    for edge in edges:
        profile = edge.get("neighbor_profile")
        if not profile:
            continue
        existing_ids.add(profile["id"])
        tags = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            tags.extend(json_list(profile.get(key)))
        item = format_match(profile, tags)
        item["edge_type"] = edge.get("edge_type") or "strong"
        item["confidence"] = edge.get("confidence") or edge.get("weight")
        item["shared_traits"] = json_list(edge.get("shared_traits"))
        item["reason"] = edge.get("explanation") or profile.get("ai_summary") or "Graph neighbor."
        item["rank_score"] = edge_rank_score(edge, focus_profile, profile)
        matches.append(item)
    matches.sort(key=lambda item: item["rank_score"], reverse=True)
    cluster_fallback = load_cluster_candidate_matches(focus_profile, existing_ids)
    related_fallback = load_related_candidate_matches(focus_profile, existing_ids)

    def unique_extend(primary: list[dict], extra: list[dict], limit: int) -> list[dict]:
        used: set[int] = set()
        combined = take_unique_matches(primary, used, limit=limit)
        if len(combined) < limit:
            combined.extend(take_unique_matches(extra, used, limit=limit - len(combined)))
        return combined

    base_weird = int(focus_profile.get("weirdness_score") or 0)
    base_emotion = int(focus_profile.get("emotional_weight_score") or 0)
    base_intensity = int(focus_profile.get("intensity_score") or 0)
    focus_cluster = focus_profile.get("primary_cluster")

    best_matches = take_unique_matches(matches, limit=8)
    best_match_ids = {item.get("id") for item in best_matches if item.get("id")}

    def prune_duplicates(items: list[dict]) -> list[dict]:
        return [item for item in items if item.get("id") not in best_match_ids]

    def allow_best_reuse(items: list[dict], limit: int = 5) -> list[dict]:
        return take_unique_matches(items, limit=limit)

    def weird_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
        weirdness = int(item["scores"].get("weirdness") or 0)
        intensity = int(item["scores"].get("intensity") or 0)
        return (
            weirdness,
            count_terms(item, WEIRD_TERMS),
            item_shared_count(item),
            float(item.get("rank_score") or 0) + cluster_match_bonus(item, focus_cluster),
        )

    def heavy_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
        emotion = int(item["scores"].get("emotional_weight") or 0)
        intensity = int(item["scores"].get("intensity") or 0)
        return (
            emotion,
            count_terms(item, HEAVY_TERMS) + max(intensity - base_intensity, 0),
            item_shared_count(item),
            float(item.get("rank_score") or 0) + cluster_match_bonus(item, focus_cluster),
        )

    def safe_sort_key(item: dict[str, Any]) -> tuple[float, float, float, float]:
        emotion = int(item["scores"].get("emotional_weight") or 0)
        weirdness = int(item["scores"].get("weirdness") or 0)
        intensity = int(item["scores"].get("intensity") or 0)
        return (
            -(count_terms(item, LIGHTER_TERMS) + max(base_emotion - emotion, 0) + max(base_weird - weirdness, 0)),
            intensity,
            -item_shared_count(item),
            -(float(item.get("rank_score") or 0) + cluster_match_bonus(item, focus_cluster)),
        )
    weird_primary = sorted(
        [item for item in matches if int(item["scores"].get("weirdness") or 0) > base_weird],
        key=lambda item: ((int(item["scores"].get("weirdness") or 0) - base_weird), item.get("rank_score") or 0),
        reverse=True,
    )
    heavier_primary = sorted(
        [item for item in matches if int(item["scores"].get("emotional_weight") or 0) > base_emotion],
        key=lambda item: ((int(item["scores"].get("emotional_weight") or 0) - base_emotion), item.get("rank_score") or 0),
        reverse=True,
    )
    safer_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("emotional_weight") or 0) < base_emotion
            or int(item["scores"].get("weirdness") or 0) < base_weird
            or int(item["scores"].get("intensity") or 0) < base_intensity
        ],
        key=lambda item: (
            int(item["scores"].get("emotional_weight") or 0),
            int(item["scores"].get("weirdness") or 0),
            int(item["scores"].get("intensity") or 0),
            -float(item.get("rank_score") or 0),
        ),
    )
    bridge_primary = sorted(
        [
            item for item in matches
            if item.get("edge_type") in {"bridge", "soft"} or item.get("cluster") != focus_cluster
        ],
        key=lambda item: item.get("rank_score") or 0,
        reverse=True,
    )
    weird_equal_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("weirdness") or 0) == base_weird
            and int(item["scores"].get("intensity") or 0) > base_intensity
        ],
        key=lambda item: ((int(item["scores"].get("intensity") or 0) - base_intensity), item.get("rank_score") or 0),
        reverse=True,
    )
    heavier_equal_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("emotional_weight") or 0) == base_emotion
            and (
                int(item["scores"].get("weirdness") or 0) > base_weird
                or int(item["scores"].get("intensity") or 0) > base_intensity
            )
        ],
        key=lambda item: (
            max(int(item["scores"].get("weirdness") or 0) - base_weird, 0)
            + max(int(item["scores"].get("intensity") or 0) - base_intensity, 0),
            item.get("rank_score") or 0,
        ),
        reverse=True,
    )
    safer_equal_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("emotional_weight") or 0) == base_emotion
            and int(item["scores"].get("weirdness") or 0) == base_weird
            and int(item["scores"].get("intensity") or 0) < base_intensity
        ],
        key=lambda item: (
            int(item["scores"].get("intensity") or 0),
            -float(item.get("rank_score") or 0),
        ),
    )

    weird_fallback = sorted(
        [item for item in cluster_fallback if int(item["scores"].get("weirdness") or 0) > base_weird],
        key=weird_sort_key,
        reverse=True,
    )
    heavier_fallback = sorted(
        [item for item in cluster_fallback if int(item["scores"].get("emotional_weight") or 0) > base_emotion],
        key=heavy_sort_key,
        reverse=True,
    )
    safer_fallback = sorted(
        [
            item for item in cluster_fallback
            if int(item["scores"].get("emotional_weight") or 0) < base_emotion
            or int(item["scores"].get("weirdness") or 0) < base_weird
            or int(item["scores"].get("intensity") or 0) < base_intensity
        ],
        key=safe_sort_key,
    )
    weird_equal_fallback = sorted(
        [
            item for item in cluster_fallback
            if int(item["scores"].get("weirdness") or 0) == base_weird
            and int(item["scores"].get("intensity") or 0) > base_intensity
        ],
        key=lambda item: ((int(item["scores"].get("intensity") or 0) - base_intensity), item.get("rank_score") or 0),
        reverse=True,
    )
    heavier_equal_fallback = sorted(
        [
            item for item in cluster_fallback
            if int(item["scores"].get("emotional_weight") or 0) == base_emotion
            and (
                int(item["scores"].get("weirdness") or 0) > base_weird
                or int(item["scores"].get("intensity") or 0) > base_intensity
            )
        ],
        key=lambda item: (
            max(int(item["scores"].get("weirdness") or 0) - base_weird, 0)
            + max(int(item["scores"].get("intensity") or 0) - base_intensity, 0),
            item.get("rank_score") or 0,
        ),
        reverse=True,
    )
    safer_equal_fallback = sorted(
        [
            item for item in cluster_fallback
            if int(item["scores"].get("emotional_weight") or 0) == base_emotion
            and int(item["scores"].get("weirdness") or 0) == base_weird
            and int(item["scores"].get("intensity") or 0) < base_intensity
        ],
        key=lambda item: (
            int(item["scores"].get("intensity") or 0),
            -float(item.get("rank_score") or 0),
        ),
    )

    weird_adjacent_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("weirdness") or 0) >= max(base_weird - 1, 0)
            and count_terms(item, WEIRD_TERMS) > 0
        ],
        key=weird_sort_key,
        reverse=True,
    )
    weird_adjacent_fallback = sorted(
        [
            item for item in related_fallback
            if int(item["scores"].get("weirdness") or 0) >= max(base_weird - 1, 0)
            and count_terms(item, WEIRD_TERMS) > 0
        ],
        key=weird_sort_key,
        reverse=True,
    )
    heavy_adjacent_primary = sorted(
        [
            item for item in matches
            if int(item["scores"].get("emotional_weight") or 0) >= max(base_emotion - 1, 0)
            and count_terms(item, HEAVY_TERMS) > 0
        ],
        key=heavy_sort_key,
        reverse=True,
    )
    heavy_adjacent_fallback = sorted(
        [
            item for item in related_fallback
            if int(item["scores"].get("emotional_weight") or 0) >= max(base_emotion - 1, 0)
            and count_terms(item, HEAVY_TERMS) > 0
        ],
        key=heavy_sort_key,
        reverse=True,
    )
    safe_adjacent_primary = sorted(
        [
            item for item in matches
            if count_terms(item, LIGHTER_TERMS) > 0
            or int(item["scores"].get("emotional_weight") or 0) <= base_emotion
            or int(item["scores"].get("weirdness") or 0) <= base_weird
        ],
        key=safe_sort_key,
    )
    safe_adjacent_fallback = sorted(
        [
            item for item in related_fallback
            if count_terms(item, LIGHTER_TERMS) > 0
            or int(item["scores"].get("emotional_weight") or 0) <= base_emotion
            or int(item["scores"].get("weirdness") or 0) <= base_weird
        ],
        key=safe_sort_key,
    )

    weird_bucket_mode = "strict"
    weird_bucket_title = "Weirder picks"
    weird_bucket_subtitle = None
    weird_bucket_items = prune_duplicates(unique_extend(weird_primary, weird_fallback, 5))
    if not weird_bucket_items:
        weird_bucket_items = prune_duplicates(unique_extend(weird_equal_primary, weird_equal_fallback, 5))
        if weird_bucket_items:
            weird_bucket_mode = "equal"
            weird_bucket_title = "Equally weird nearby picks"
            weird_bucket_subtitle = f"{focus_profile['title']} is already one of the weirder titles in this neighborhood, so these are similarly strange nearby matches."
    if not weird_bucket_items:
        weird_bucket_items = prune_duplicates(unique_extend(weird_adjacent_primary, weird_adjacent_fallback, 5))
        if weird_bucket_items:
            weird_bucket_mode = "fallback"
            weird_bucket_title = f"Adjacent strange picks near {focus_profile['title']}"
            weird_bucket_subtitle = f"{focus_profile['title']} is already near the weirdness ceiling, so these are adjacent strange picks rather than strictly weirder ones."
    if not weird_bucket_items:
        weird_bucket_items = allow_best_reuse(weird_equal_primary or weird_adjacent_primary or best_matches, 5)
        if weird_bucket_items:
            weird_bucket_mode = "equal"
            weird_bucket_title = f"Adjacent strange picks near {focus_profile['title']}"
            weird_bucket_subtitle = f"{focus_profile['title']} is already one of the stranger titles in this neighborhood, so these nearby matches are similarly off-center rather than strictly weirder."

    heavier_bucket_mode = "strict"
    heavier_bucket_title = "Emotionally heavier"
    heavier_bucket_subtitle = None
    heavier_bucket_items = prune_duplicates(unique_extend(heavier_primary, heavier_fallback, 5))
    if not heavier_bucket_items:
        heavier_bucket_items = prune_duplicates(unique_extend(heavier_equal_primary, heavier_equal_fallback, 5))
        if heavier_bucket_items:
            heavier_bucket_mode = "equal"
            heavier_bucket_title = "Equally heavy nearby picks"
            heavier_bucket_subtitle = f"{focus_profile['title']} is already emotionally heavy, so these are similarly intense nearby matches."
    if not heavier_bucket_items:
        heavier_bucket_items = prune_duplicates(unique_extend(heavy_adjacent_primary, heavy_adjacent_fallback, 5))
        if heavier_bucket_items:
            heavier_bucket_mode = "fallback"
            heavier_bucket_title = f"Adjacent emotionally intense picks near {focus_profile['title']}"
            heavier_bucket_subtitle = f"{focus_profile['title']} is already near the emotional ceiling, so these are adjacent intense picks rather than strictly heavier ones."
    if not heavier_bucket_items:
        heavier_bucket_items = allow_best_reuse(heavier_equal_primary or heavy_adjacent_primary or best_matches, 5)
        if heavier_bucket_items:
            heavier_bucket_mode = "equal"
            heavier_bucket_title = f"Adjacent emotionally intense picks near {focus_profile['title']}"
            heavier_bucket_subtitle = f"{focus_profile['title']} is already emotionally heavy, so these nearby matches are similarly intense rather than strictly heavier."

    safer_bucket_mode = "strict"
    safer_bucket_title = "Safer / easier"
    safer_bucket_subtitle = None
    safer_bucket_items = prune_duplicates(unique_extend(safer_primary, safer_fallback, 5))
    if not safer_bucket_items:
        safer_bucket_items = prune_duplicates(unique_extend(safer_equal_primary, safer_equal_fallback, 5))
        if safer_bucket_items:
            safer_bucket_mode = "equal"
            safer_bucket_title = "Lighter nearby picks"
            safer_bucket_subtitle = f"These sit at a similar score level but read lighter or less punishing than {focus_profile['title']}."
    if not safer_bucket_items:
        safer_bucket_items = prune_duplicates(unique_extend(safe_adjacent_primary, safe_adjacent_fallback, 5))
        if safer_bucket_items:
            safer_bucket_mode = "fallback"
            safer_bucket_title = "Safer adjacent picks"
            safer_bucket_subtitle = f"No clearly lighter graph-neighbor surfaced, so these are lower-pressure nearby alternatives."
    if not safer_bucket_items:
        safer_bucket_items = allow_best_reuse(safe_adjacent_primary or safer_equal_primary or best_matches, 5)
        if safer_bucket_items:
            safer_bucket_mode = "fallback"
            safer_bucket_title = "Lighter nearby picks"
            safer_bucket_subtitle = f"No clearly safer graph-neighbor surfaced, so these are the closest nearby matches with the gentlest available pressure profile."

    bridge_items = prune_duplicates(take_unique_matches(bridge_primary, limit=5))

    sections = {
        "best_matches": {
            "items": best_matches,
            "mode": "strict" if best_matches else "empty",
            "label": "Best matches",
            "subtitle": None,
            "empty_reason": "No strong recommendation set yet.",
        },
        "weirdest_matches": {
            "items": weird_bucket_items,
            "mode": weird_bucket_mode if weird_bucket_items else "empty",
            "label": weird_bucket_title,
            "subtitle": weird_bucket_subtitle,
            "empty_reason": "This title is already near the current weirdness ceiling for its neighborhood." if not weird_bucket_items else None,
        },
        "emotionally_heavier_matches": {
            "items": heavier_bucket_items,
            "mode": heavier_bucket_mode if heavier_bucket_items else "empty",
            "label": heavier_bucket_title,
            "subtitle": heavier_bucket_subtitle,
            "empty_reason": "This title is already near the current emotional ceiling for its neighborhood." if not heavier_bucket_items else None,
        },
        "safer_easier_watches": {
            "items": safer_bucket_items,
            "mode": safer_bucket_mode if safer_bucket_items else "empty",
            "label": safer_bucket_title,
            "subtitle": safer_bucket_subtitle,
            "empty_reason": "This neighborhood is uniformly intense, so no safer nearby picks surfaced." if not safer_bucket_items else None,
        },
        "bridge_titles": {
            "items": bridge_items,
            "mode": "strict" if bridge_items else "empty",
            "label": "Bridge titles",
            "subtitle": None,
            "empty_reason": "No bridge-style or cross-cluster neighbors surfaced from the current graph." if not bridge_items else None,
        },
    }

    buckets = {
        "focus_profile": focus_profile,
        "neighbor_data": neighbor_data,
        "all_matches": matches,
        "best_matches": sections["best_matches"]["items"],
        "weirdest_matches": sections["weirdest_matches"]["items"],
        "emotionally_heavier_matches": sections["emotionally_heavier_matches"]["items"],
        "safer_easier_watches": sections["safer_easier_watches"]["items"],
        "bridge_titles": sections["bridge_titles"]["items"],
        "sections": sections,
        "bucket_titles": {
            key: value["label"] for key, value in sections.items()
        },
        "bucket_empty_reasons": {
            key: value["empty_reason"] for key, value in sections.items()
        },
    }
    logger.info(
        "ask buckets selected_title_id=%s selected_title=%r selected_scores=%s total_neighbors=%s edge_type_counts=%s weird_strict=%s weird_equal=%s weird_fallback=%s weird_final=%s weird_mode=%s heavier_strict=%s heavier_equal=%s heavier_fallback=%s heavier_final=%s heavier_mode=%s safer_strict=%s safer_equal=%s safer_fallback=%s safer_final=%s safer_mode=%s bridge_final=%s",
        selected_title_id,
        focus_profile.get("title"),
        {
            "johnny_core": focus_profile.get("johnny_core_score"),
            "weirdness": focus_profile.get("weirdness_score"),
            "emotional_weight": focus_profile.get("emotional_weight_score"),
            "intensity": focus_profile.get("intensity_score"),
        },
        len(matches),
        neighbor_data.get("edge_type_counts"),
        len(weird_primary),
        len(weird_equal_primary),
        len(weird_adjacent_primary) + len(weird_adjacent_fallback),
        len(buckets["weirdest_matches"]),
        sections["weirdest_matches"]["mode"],
        len(heavier_primary),
        len(heavier_equal_primary),
        len(heavy_adjacent_primary) + len(heavy_adjacent_fallback),
        len(buckets["emotionally_heavier_matches"]),
        sections["emotionally_heavier_matches"]["mode"],
        len(safer_primary),
        len(safer_equal_primary),
        len(safe_adjacent_primary) + len(safe_adjacent_fallback),
        len(buckets["safer_easier_watches"]),
        sections["safer_easier_watches"]["mode"],
        len(buckets["bridge_titles"]),
    )
    return buckets


def similarity_fast_answer(question: str, selected_title_id: Optional[int] = None, intent: Optional[str] = None) -> Optional[dict]:
    resolved_intent = resolve_ask_intent(question, intent)
    if not selected_title_id and not is_similarity_question(question):
        return None
    focus = title_by_id(selected_title_id) if selected_title_id else matched_title_from_question(question)
    if not focus:
        return None
    buckets = build_ask_recommendation_buckets(focus["id"])
    if not buckets:
        return None
    focus_profile = buckets["focus_profile"]
    neighbor_data = buckets["neighbor_data"]
    best_matches = buckets["best_matches"]

    if not best_matches:
        log_missing_neighbors(
            question=question,
            selected_title_id=selected_title_id,
            focus=focus,
            neighbor_data=neighbor_data,
            reason="exact-title-id-has-zero-usable-neighbor-profiles" if neighbor_data.get("edge_count") else "exact-title-id-has-zero-edges",
        )
        return {
            "recommendation": f"{focus['title']} has no graph neighbors yet.",
            "why_it_fits": "The stored graph currently has no usable neighboring edge data for this exact title id yet.",
            "nearby_titles": [],
            "confidence": 0.4,
            "tags_that_drove_answer": ["needs more graph context"],
            "best_matches": [],
            "weirdest_matches": [],
            "emotionally_heavier_matches": [],
            "safer_easier_watches": [],
            "bridge_titles": [],
            "why_these_fit": "No stored graph connections are available for this exact title yet.",
            "tags_driving_recommendation": ["needs more graph context"],
            "answer_source": "local_graph",
            "intent": resolved_intent,
            "bucket_empty_reasons": buckets["bucket_empty_reasons"],
            "bucket_titles": buckets.get("bucket_titles", {}),
        }

    question_lower = normalize_question(question)
    primary_reason = best_matches[0]["reason"]
    if resolved_intent == "why_connects" or ("why" in question_lower and "connect" in question_lower):
        primary_reason = (
            f"{focus['title']} leans toward {focus_profile.get('primary_cluster') or 'an outlier zone'}, "
            f"and its nearest neighbors share {', '.join(best_matches[0].get('shared_traits', [])[:3]) or 'a similar pressure profile'}."
        )
    recommendation = f"Closest to {focus['title']}: {best_matches[0]['title']}."
    if resolved_intent == "weirder":
        weird_mode = buckets["sections"]["weirdest_matches"]["mode"]
        weird_label = buckets["sections"]["weirdest_matches"]["label"]
        if buckets["weirdest_matches"] and weird_mode == "strict":
            recommendation = f"Weirder than {focus['title']}: {buckets['weirdest_matches'][0]['title']}."
        elif buckets["weirdest_matches"]:
            recommendation = f"{weird_label}: {buckets['weirdest_matches'][0]['title']}."
        else:
            recommendation = f"{focus['title']} is already near the weirdness ceiling for its neighborhood."
    elif resolved_intent == "heavier":
        heavier_mode = buckets["sections"]["emotionally_heavier_matches"]["mode"]
        heavier_label = buckets["sections"]["emotionally_heavier_matches"]["label"]
        if buckets["emotionally_heavier_matches"] and heavier_mode == "strict":
            recommendation = f"Heavier than {focus['title']}: {buckets['emotionally_heavier_matches'][0]['title']}."
        elif buckets["emotionally_heavier_matches"]:
            recommendation = f"{heavier_label}: {buckets['emotionally_heavier_matches'][0]['title']}."
        else:
            recommendation = f"{focus['title']} is already near the emotional ceiling for its neighborhood."
    elif resolved_intent == "safer":
        safer_mode = buckets["sections"]["safer_easier_watches"]["mode"]
        safer_label = buckets["sections"]["safer_easier_watches"]["label"]
        if buckets["safer_easier_watches"] and safer_mode == "strict":
            recommendation = f"Safer near {focus['title']}: {buckets['safer_easier_watches'][0]['title']}."
        elif buckets["safer_easier_watches"]:
            recommendation = f"{safer_label}: {buckets['safer_easier_watches'][0]['title']}."
        else:
            recommendation = f"{focus['title']}'s neighborhood is uniformly intense."
    elif resolved_intent == "similar":
        recommendation = f"Similar to {focus['title']}: {best_matches[0]['title']}."
    elif resolved_intent == "why_connects" or ("why" in question_lower and "connect" in question_lower):
        recommendation = f"Why {focus['title']} connects: {best_matches[0]['title']}."
    return {
        "recommendation": recommendation,
        "why_it_fits": primary_reason,
        "nearby_titles": [item["title"] for item in best_matches[:8]],
        "confidence": best_matches[0].get("confidence", 0.7),
        "tags_that_drove_answer": best_matches[0].get("shared_traits", [])[:6],
        "best_matches": buckets["best_matches"],
        "weirdest_matches": buckets["weirdest_matches"],
        "emotionally_heavier_matches": buckets["emotionally_heavier_matches"],
        "safer_easier_watches": buckets["safer_easier_watches"],
        "bridge_titles": buckets["bridge_titles"],
        "sections": buckets["sections"],
        "bucket_empty_reasons": buckets["bucket_empty_reasons"],
        "bucket_titles": buckets.get("bucket_titles", {}),
        "why_these_fit": "Returned instantly from stored graph connections. Strong matches lead, and nearby alternatives widen the bucket when the graph needs context.",
        "tags_driving_recommendation": best_matches[0].get("shared_traits", [])[:6],
        "matched_title": focus_profile["title"] if focus_profile else focus["title"],
        "answer_source": "local_graph",
        "intent": resolved_intent,
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
                   p.pacing_score, p.johnny_core_score, p.ai_summary,
                   p.recommendation_hooks, p.closest_viewing_context
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
            SELECT id, title, year, type, source, primary_cluster, enrichment_status
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
        if item.get("edge_type") in {"soft", "bridge"} and item.get("cluster") != focus_cluster
    ]
    same_cluster_soft = [
        item for item in items
        if item.get("edge_type") in {"soft", "bridge"} and item.get("cluster") == focus_cluster
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


def detailed_title_payload(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "title": row["title"],
        "year": row["year"],
        "type": row["type"],
        "source": row["source"],
        "cluster": row.get("primary_cluster") or "Mixed / Transitional",
        "scores": {
            "johnny_core": row.get("johnny_core_score"),
            "weirdness": row.get("weirdness_score"),
            "emotional_weight": row.get("emotional_weight_score"),
            "intensity": row.get("intensity_score"),
            "pacing": row.get("pacing_score"),
        },
        "tone_tags": json_list(row.get("tone_tags")),
        "theme_tags": json_list(row.get("theme_tags")),
        "style_tags": json_list(row.get("style_tags")),
        "mood_tags": json_list(row.get("mood_tags")),
        "recommendation_hooks": json_list(row.get("recommendation_hooks")),
        "closest_viewing_context": row.get("closest_viewing_context"),
        "summary": row.get("ai_summary"),
    }


def summarize_title_relationship_profile(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "title": row["title"],
        "year": row["year"],
        "cluster": row.get("primary_cluster") or "Mixed / Transitional",
        "scores": {
            "johnny_core": row.get("johnny_core_score"),
            "weirdness": row.get("weirdness_score"),
            "emotional_weight": row.get("emotional_weight_score"),
            "intensity": row.get("intensity_score"),
        },
        "tone_tags": json_list(row.get("tone_tags"))[:4],
        "theme_tags": json_list(row.get("theme_tags"))[:4],
        "style_tags": json_list(row.get("style_tags"))[:4],
        "mood_tags": json_list(row.get("mood_tags"))[:4],
        "recommendation_hooks": json_list(row.get("recommendation_hooks"))[:4],
        "closest_viewing_context": row.get("closest_viewing_context"),
    }
