import json
import logging
from pathlib import Path
from typing import Any, Optional

import openai
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.db import get_connection, now_iso
from app.models import SEED_CLUSTERS, json_list


TASTE_VOCABULARY = [
    "tech paranoia",
    "body horror",
    "weird sci-fi dread",
    "systems under pressure",
    "moral rot",
    "institutional decay",
    "anti-hero spiral",
    "war trauma",
    "spiritual brutality",
    "psychological collapse",
    "puzzle-box mystery",
    "simulation anxiety",
    "corporate manipulation",
    "identity breakdown",
    "surveillance and control",
    "existential horror",
    "emotionally devastating cinema",
    "surreal dread",
    "paranoia",
    "obsession",
    "alienation",
    "procedural mystery",
    "prestige crime",
    "tragic masculinity",
]


SYSTEM_PROMPT = """You enrich titles for Taste Graph v1, a visual discovery map of one person's movie/show taste.
Return strict JSON only.
This is not generic genre tagging. Do not return bland tags such as good, fun, movie, drama, action, classic, or interesting.
Favor nuanced taste language: themes, tone, mood, texture, moral atmosphere, psychic pressure, formal style, and vibe proximity.
Scores should be calibrated for discovery: 1 is very low, 10 is extreme."""

STRICT_RETRY_PROMPT = """Return ONLY valid JSON.
Escape all quotes inside strings.
No markdown.
No commentary.
No prose outside the JSON object.
Fill every required field."""

FAILED_ENRICHMENT_LOG = get_settings().data_dir / "failed_enrichment_responses.log"
logger = logging.getLogger("taste_graph.openai")


class EnrichmentJSONError(RuntimeError):
    pass


class EnrichmentSchema(BaseModel):
    tone_tags: list[str] = Field(default_factory=list, min_length=1)
    theme_tags: list[str] = Field(default_factory=list, min_length=1)
    style_tags: list[str] = Field(default_factory=list, min_length=1)
    mood_tags: list[str] = Field(default_factory=list, min_length=1)
    intensity_score: int = Field(ge=1, le=10)
    weirdness_score: int = Field(ge=1, le=10)
    emotional_weight_score: int = Field(ge=1, le=10)
    pacing_score: int = Field(ge=1, le=10)
    johnny_core_score: int = Field(ge=1, le=10)
    primary_cluster: str = Field(min_length=1)
    ai_summary: str = Field(min_length=1)
    recommendation_hooks: list[str] = Field(default_factory=list, min_length=1)
    closest_viewing_context: str = Field(min_length=1)


def _title_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": row["title"],
        "year": row["year"],
        "type": row["type"],
        "summary": row["summary"],
        "genres": json_list(row["genres"]),
        "directors": json_list(row["directors"]),
        "writers": json_list(row["writers"]),
        "actors": json_list(row["actors"]),
    }


class OpenAITasteClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for enrichment and Q&A.")
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model

    def enrich_title(self, row: dict[str, Any]) -> dict[str, Any]:
        prompt = self._enrichment_prompt(row)
        try:
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                response_format=EnrichmentSchema,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                temperature=0.35,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise EnrichmentJSONError("Structured output returned no parsed payload.")
            return self._normalized_profile(parsed.model_dump())
        except Exception as exc:
            logger.warning("Structured enrichment parse failed for %s (%s): %s", row.get("title"), row.get("year"), exc)
            return self._retry_enrichment_with_strict_json(row, prompt, exc)

    def _enrichment_prompt(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "title_metadata": _title_payload(row),
            "seed_clusters": SEED_CLUSTERS,
            "taste_vocabulary_guidance": TASTE_VOCABULARY,
            "schema": {
                "tone_tags": [],
                "theme_tags": [],
                "style_tags": [],
                "mood_tags": [],
                "intensity_score": "integer 1-10",
                "weirdness_score": "integer 1-10",
                "emotional_weight_score": "integer 1-10",
                "pacing_score": "integer 1-10",
                "johnny_core_score": "integer 1-10",
                "primary_cluster": "one concise taste cluster, ideally from the provided vocabulary or seed clusters",
                "ai_summary": "2-4 sentences explaining how this title fits the taste graph",
                "recommendation_hooks": "3-5 short phrases for when/why to recommend this title",
                "closest_viewing_context": "short text like 'watch when you want...'",
            },
            "requirements": [
                "tone_tags must contain 3-6 non-generic items when possible",
                "theme_tags must contain 3-6 non-generic items when possible",
                "style_tags must contain 2-5 non-generic items when possible",
                "mood_tags must contain 2-5 non-generic items when possible",
                "ai_summary must be 2-4 sentences",
                "recommendation_hooks must contain 3-5 concise phrases",
                "closest_viewing_context must be a short recommendation sentence",
                "Never leave arrays empty unless the source metadata is truly unusable",
            ],
        }

    def _retry_enrichment_with_strict_json(self, row: dict[str, Any], prompt: dict[str, Any], original_error: Exception) -> dict[str, Any]:
        response = self.client.chat.completions.with_raw_response.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": f"{SYSTEM_PROMPT}\n{STRICT_RETRY_PROMPT}"},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0.2,
        )
        completion = response.parse()
        raw_content = completion.choices[0].message.content or ""
        try:
            parsed = json.loads(raw_content or "{}")
            validated = EnrichmentSchema.model_validate(parsed)
            return self._normalized_profile(validated.model_dump())
        except (json.JSONDecodeError, ValidationError, TypeError, EnrichmentJSONError) as retry_error:
            self._log_failed_response(row, raw_content, original_error, retry_error)
            raise EnrichmentJSONError("malformed JSON after retry") from retry_error

    def _normalized_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        normalized = EnrichmentSchema.model_validate(profile).model_dump()
        for key, minimum, maximum in (
            ("tone_tags", 3, 6),
            ("theme_tags", 3, 6),
            ("style_tags", 2, 5),
            ("mood_tags", 2, 5),
            ("recommendation_hooks", 3, 5),
        ):
            normalized[key] = [str(item).strip() for item in normalized.get(key, []) if str(item).strip()][:maximum]
            if len(normalized[key]) < minimum:
                raise EnrichmentJSONError(f"{key} did not meet minimum length requirements")
        normalized["ai_summary"] = " ".join(str(normalized.get("ai_summary", "")).split())
        normalized["closest_viewing_context"] = " ".join(str(normalized.get("closest_viewing_context", "")).split())
        if not normalized["ai_summary"] or not normalized["closest_viewing_context"]:
            raise EnrichmentJSONError("Required summary fields were empty after normalization")
        return normalized

    def _log_failed_response(self, row: dict[str, Any], raw_content: str, original_error: Exception, retry_error: Exception) -> None:
        FAILED_ENRICHMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with FAILED_ENRICHMENT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "title": row.get("title"),
                        "year": row.get("year"),
                        "original_error": str(original_error),
                        "retry_error": str(retry_error),
                        "raw_response": raw_content,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def answer_question(self, question: str, context: str, timeout_seconds: float = 3.0) -> dict[str, Any]:
        blocked = server_diagnostic_response(question)
        if blocked:
            return blocked
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are Ask Taste Graph. Answer only taste, vibe, similarity, and what-to-watch questions "
                        "using ONLY the provided structured graph facts. Do not generalize beyond the facts. "
                        "Do not invent tags, paths, motivations, clusters, or relationships that are not explicitly present. "
                        "If the facts are thin, say so plainly. "
                        "Treat strong graph connections as primary evidence and soft graph connections as secondary, looser evidence. "
                        "When a recommendation relies on a soft connection, say that it is a looser match. "
                        "Return JSON with best_matches, weirdest_matches, emotionally_heavier_matches, "
                        "safer_easier_watches, why_these_fit, tags_driving_recommendation, confidence, "
                        "plus legacy recommendation, why_it_fits, nearby_titles, and tags_that_drove_answer fields."
                    ),
                },
                {"role": "user", "content": f"Question: {question}\n\nLibrary context:\n{context}"},
            ],
            temperature=0.2,
            timeout=timeout_seconds,
        )
        return json.loads(response.choices[0].message.content or "{}")

    def explain_graph_answer(self, question: str, context: str, timeout_seconds: float = 10.0) -> dict[str, Any]:
        blocked = server_diagnostic_response(question)
        if blocked:
            return {"explanation": blocked.get("why_it_fits") or blocked.get("recommendation") or ""}
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            max_tokens=260,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are explaining a Plex movie and TV taste graph. "
                        "Nodes are Plex titles. Edges are taste-similarity connections between titles. "
                        "This is NOT a location graph. Do not mention locations, routes, points of interest, destinations, or physical graph traversal. "
                        "Use ONLY the provided local graph evidence. Explain why the already-returned recommendation cards make sense. "
                        "If bridge or soft matches appear, describe them as looser or more tentative fits. "
                        "If a directional bucket is empty, explain the local ceiling or fallback honestly, but do not claim there are no edges when matches are present. "
                        "Do not invent titles, tags, clusters, reasons, or connections. "
                        "Return JSON with keys: title, explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        "Explain this local taste-graph answer in plain language using only the evidence below.\n"
                        "Focus on tone, themes, mood, style, emotional weight, weirdness, cluster logic, and explicit edge reasons.\n\n"
                        f"Taste graph evidence JSON:\n{context}"
                    ),
                },
            ],
            temperature=0.2,
            timeout=timeout_seconds,
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        return {
            "title": payload.get("title") or "AI explanation",
            "explanation": payload.get("explanation") or "AI explanation unavailable right now.",
        }


def server_diagnostic_response(question: str) -> Optional[dict[str, Any]]:
    blocked_terms = [
        "transcode",
        "bandwidth",
        "cpu",
        "ram",
        "container",
        "docker",
        "nas",
        "server health",
        "buffer",
        "streaming problem",
        "plex health",
        "diagnostic",
    ]
    lowered = question.lower()
    if any(term in lowered for term in blocked_terms):
        return {
            "recommendation": "Taste Graph is only for visual discovery and taste-based recommendations.",
            "why_it_fits": "I can help with vibes, themes, clusters, similarity, and what to watch next, but not Plex server diagnostics or infrastructure issues.",
            "nearby_titles": [],
            "confidence": 1.0,
            "tags_that_drove_answer": ["taste-only boundary"],
        }
    return None


def store_taste_profile(title_id: int, profile: dict[str, Any]) -> None:
    stamp = now_iso()
    cleaned = {
        "title_id": title_id,
        "tone_tags": json.dumps(profile.get("tone_tags", [])),
        "theme_tags": json.dumps(profile.get("theme_tags", [])),
        "style_tags": json.dumps(profile.get("style_tags", [])),
        "mood_tags": json.dumps(profile.get("mood_tags", [])),
        "intensity_score": int(profile.get("intensity_score", 5)),
        "weirdness_score": int(profile.get("weirdness_score", 5)),
        "emotional_weight_score": int(profile.get("emotional_weight_score", 5)),
        "pacing_score": int(profile.get("pacing_score", 5)),
        "johnny_core_score": int(profile.get("johnny_core_score", 5)),
        "primary_cluster": profile.get("primary_cluster") or infer_primary_cluster(profile),
        "ai_summary": profile.get("ai_summary", ""),
        "recommendation_hooks": json.dumps(profile.get("recommendation_hooks", [])),
        "closest_viewing_context": profile.get("closest_viewing_context", ""),
        "created_at": stamp,
        "updated_at": stamp,
    }
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO taste_profiles (
                title_id, tone_tags, theme_tags, style_tags, mood_tags, intensity_score,
                weirdness_score, emotional_weight_score, pacing_score, johnny_core_score,
                ai_summary, recommendation_hooks, closest_viewing_context, created_at, updated_at
            )
            VALUES (
                :title_id, :tone_tags, :theme_tags, :style_tags, :mood_tags, :intensity_score,
                :weirdness_score, :emotional_weight_score, :pacing_score, :johnny_core_score,
                :ai_summary, :recommendation_hooks, :closest_viewing_context, :created_at, :updated_at
            )
            ON CONFLICT(title_id) DO UPDATE SET
                tone_tags=excluded.tone_tags,
                theme_tags=excluded.theme_tags,
                style_tags=excluded.style_tags,
                mood_tags=excluded.mood_tags,
                intensity_score=excluded.intensity_score,
                weirdness_score=excluded.weirdness_score,
                emotional_weight_score=excluded.emotional_weight_score,
                pacing_score=excluded.pacing_score,
                johnny_core_score=excluded.johnny_core_score,
                ai_summary=excluded.ai_summary,
                recommendation_hooks=excluded.recommendation_hooks,
                closest_viewing_context=excluded.closest_viewing_context,
                updated_at=excluded.updated_at
            """,
            cleaned,
        )
        conn.execute(
            """
            UPDATE titles
            SET enrichment_status = 'enriched',
                primary_cluster = ?,
                last_enriched_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (cleaned["primary_cluster"], stamp, stamp, title_id),
        )


def infer_primary_cluster(profile: dict[str, Any]) -> str:
    tags = []
    for key in ("theme_tags", "tone_tags", "style_tags", "mood_tags"):
        tags.extend(profile.get(key, []) or [])
    lowered = [str(tag).lower() for tag in tags]
    for candidate in TASTE_VOCABULARY:
        if candidate in lowered:
            return candidate
    return str(tags[0]) if tags else "Outliers"
