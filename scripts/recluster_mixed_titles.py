from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TASTE_GRAPH_DB_PATH", str(ROOT / "data" / "taste_graph.sqlite"))

from app.db import get_connection, now_iso
from app.models import json_list, normalise_tag


TARGET_CLUSTERS = (
    "Crime / Investigation",
    "Prestige / Character Drama",
    "Political / Institutional Thriller",
    "Action / Survival Pressure",
    "Comedy / Satire / Social Bite",
    "Romance / Coming-of-Age / Heartbreak",
    "Mystery / Psychological Suspense",
    "Historical / War / Moral Conflict",
    "Pop Adventure / Crowdpleasers",
)

CLUSTER_PREFERENCE = {
    "Crime / Investigation": 9,
    "Political / Institutional Thriller": 8,
    "Historical / War / Moral Conflict": 7,
    "Mystery / Psychological Suspense": 6,
    "Action / Survival Pressure": 5,
    "Prestige / Character Drama": 4,
    "Romance / Coming-of-Age / Heartbreak": 3,
    "Comedy / Satire / Social Bite": 2,
    "Pop Adventure / Crowdpleasers": 1,
}

SOURCE_CLUSTERS = ("Mixed / Transitional", "Unclustered")

CLUSTER_RULES: dict[str, dict[str, object]] = {
    "Crime / Investigation": {
        "genres": {"crime", "mystery"},
        "keywords": {
            "detective", "police", "cop", "murder", "killer", "investigation", "investigate",
            "court", "jury", "trial", "lawyer", "legal", "corruption", "heist", "gang",
            "mafia", "mob", "organized crime", "drug ring", "undercover", "prison", "jail",
            "criminal", "case", "witness", "noir", "robbery",
        },
        "score": 0.0,
    },
    "Prestige / Character Drama": {
        "genres": {"drama"},
        "keywords": {
            "family", "marriage", "relationship", "career", "social realism", "character study",
            "moral compromise", "midlife", "addiction", "friendship", "domestic", "identity",
            "working class", "ambition", "isolation", "loneliness", "coming to terms", "grief",
        },
        "score": 0.0,
    },
    "Political / Institutional Thriller": {
        "genres": set(),
        "keywords": {
            "government", "journalist", "journalism", "espionage", "spy", "surveillance",
            "conspiracy", "bureaucracy", "state", "president", "white house", "senate",
            "campaign", "media", "corporate", "whistleblower", "intelligence", "cia", "fbi",
            "investigative reporting", "cover-up", "totalitarian", "propaganda", "rebel",
            "state power", "rewriting history", "surveillance state",
        },
        "score": 0.0,
    },
    "Action / Survival Pressure": {
        "genres": {"action"},
        "keywords": {
            "survival", "mission", "rescue", "chase", "explosion", "soldier", "battle",
            "war zone", "escape", "hostage", "assassin", "hunt", "fight", "combat", "siege",
            "disaster", "virus", "infected", "apocalypse", "post-apocalyptic", "wilderness",
            "mountain", "canyoneering", "under attack",
        },
        "score": 0.0,
    },
    "Comedy / Satire / Social Bite": {
        "genres": {"comedy"},
        "keywords": {
            "satire", "satirical", "absurd", "dark comedy", "romantic comedy", "cringe",
            "awkward", "media satire", "workplace comedy", "buddy comedy", "farce", "parody",
            "high school comedy", "college comedy", "social satire", "comedic", "funny",
        },
        "score": 0.0,
    },
    "Romance / Coming-of-Age / Heartbreak": {
        "genres": {"romance"},
        "keywords": {
            "romance", "romantic", "love", "heartbreak", "teen", "teenager", "young woman",
            "young man", "coming of age", "friendship", "longing", "first love", "breakup",
            "nostalgic", "youth", "adolescence", "growing up",
        },
        "score": 0.0,
    },
    "Mystery / Psychological Suspense": {
        "genres": {"mystery", "horror"},
        "keywords": {
            "psychological", "paranoia", "bizarre", "surreal", "ambiguous", "unreliable",
            "nightmare", "hallucination", "time travel", "virus", "memory", "obsession",
            "suspense", "mind", "dream", "strange", "disappearance", "secret", "unknown",
            "uncanny", "haunting", "paranormal", "debunking", "terror", "hotel", "signal",
            "extraterrestrial", "alien", "dystopian",
        },
        "score": 0.0,
    },
    "Historical / War / Moral Conflict": {
        "genres": {"history", "war"},
        "keywords": {
            "war", "military", "occupation", "historical", "period drama", "world war",
            "battlefield", "genocide", "ideological", "empire", "civil war", "soldier",
            "frontline", "kingdom", "revolution", "resistance", "colonial", "moral conflict",
        },
        "score": 0.0,
    },
    "Pop Adventure / Crowdpleasers": {
        "genres": {"adventure", "family", "animation"},
        "keywords": {
            "adventure", "family", "franchise", "hero", "quest", "crowd-pleasing", "crowd pleasing",
            "pirate", "sea", "journey", "treasure", "school", "magic", "monster", "spaceship",
            "kids", "children", "whimsical", "charming", "fun", "uplifting",
        },
        "score": 0.0,
    },
}

HEAVY_TERMS = {
    "grief", "trauma", "dread", "moral rot", "collapse", "despair", "bleak", "devastating",
    "brutal", "war", "guilt", "pressure", "alienation", "obsession", "loss", "violence",
    "institutional", "occupation", "murder", "killer", "prison", "isolation",
}
WEIRD_TERMS = {
    "surreal", "dreamlike", "uncanny", "psychological", "abstract", "fragmented", "identity fracture",
    "existential", "hallucinatory", "nonlinear", "absurd", "experimental", "dissociative",
    "paranoid", "strange", "haunting", "bizarre", "nightmare", "obsession",
}
LIGHT_TERMS = {
    "playful", "funny", "warm", "whimsical", "adventure", "charming", "nostalgic", "romantic",
    "lighter", "comedic", "crowd-pleasing", "friendship", "quest", "family",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recluster titles stuck in Mixed / Transitional or Unclustered.")
    parser.add_argument("--dry-run", action="store_true", help="Print the proposed reclustering without writing changes.")
    parser.add_argument("--apply", action="store_true", help="Write the proposed cluster changes to SQLite.")
    parser.add_argument("--limit", type=int, default=5, help="Examples to print per cluster in dry-run mode.")
    return parser.parse_args()


def tokenize(*values: object) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            iterable = value
        else:
            iterable = [value]
        for item in iterable:
            if item is None:
                continue
            text = str(item).strip().lower()
            if not text:
                continue
            normalized = normalise_tag(text) or text
            tokens.add(normalized)
            for piece in re.findall(r"[a-z0-9][a-z0-9/'\-]+", normalized):
                if len(piece) > 2:
                    tokens.add(piece)
    return tokens


def load_candidates() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.type, t.source, t.summary, t.genres, t.primary_cluster,
                   t.enrichment_status,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.recommendation_hooks, p.ai_summary, p.closest_viewing_context,
                   p.johnny_core_score, p.weirdness_score, p.emotional_weight_score
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.enrichment_status = 'enriched'
              AND t.primary_cluster IN ('Mixed / Transitional', 'Unclustered')
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()

    items: list[dict] = []
    for row in rows:
        item = dict(row)
        item["genres"] = json_list(item.get("genres"))
        item["tone_tags"] = json_list(item.get("tone_tags"))
        item["theme_tags"] = json_list(item.get("theme_tags"))
        item["style_tags"] = json_list(item.get("style_tags"))
        item["mood_tags"] = json_list(item.get("mood_tags"))
        item["recommendation_hooks"] = json_list(item.get("recommendation_hooks"))
        item["tokens"] = tokenize(
            item.get("title"),
            item.get("summary"),
            item.get("ai_summary"),
            item.get("closest_viewing_context"),
            item.get("genres"),
            item.get("tone_tags"),
            item.get("theme_tags"),
            item.get("style_tags"),
            item.get("mood_tags"),
            item.get("recommendation_hooks"),
        )
        items.append(item)
    return items


def add_weight(scores: dict[str, float], cluster: str, amount: float, reason: str, reasons: dict[str, list[str]]) -> None:
    scores[cluster] += amount
    reasons[cluster].append(reason)


def classify(item: dict) -> tuple[str | None, dict[str, float], dict[str, list[str]]]:
    tokens = item["tokens"]
    genres = {str(genre).strip().lower() for genre in item.get("genres", [])}
    weirdness = int(item.get("weirdness_score") or 5)
    emotional = int(item.get("emotional_weight_score") or 5)
    summary = str(item.get("summary") or "").lower()
    title = str(item.get("title") or "")

    scores = {cluster: 0.0 for cluster in TARGET_CLUSTERS}
    reasons: dict[str, list[str]] = defaultdict(list)

    for cluster, config in CLUSTER_RULES.items():
        for genre in config["genres"]:
            if genre in genres:
                add_weight(scores, cluster, 2.2, f"genre:{genre}", reasons)
        for keyword in config["keywords"]:
            key = normalise_tag(keyword) or keyword.lower()
            if key in tokens or key in summary:
                add_weight(scores, cluster, 1.3, f"keyword:{keyword}", reasons)

    if item.get("type") == "show":
        if {"crime", "mystery"} & genres:
            add_weight(scores, "Crime / Investigation", 0.8, "show-crime", reasons)
        if {"action", "adventure", "science fiction"} & genres:
            add_weight(scores, "Pop Adventure / Crowdpleasers", 0.5, "show-pop", reasons)

    if weirdness >= 7:
        weird_hits = len(WEIRD_TERMS & tokens)
        add_weight(scores, "Mystery / Psychological Suspense", 0.7 + weird_hits * 0.3, "weirdness-signal", reasons)
    if emotional >= 7:
        heavy_hits = len(HEAVY_TERMS & tokens)
        add_weight(scores, "Prestige / Character Drama", 0.5 + heavy_hits * 0.2, "emotional-signal", reasons)
        add_weight(scores, "Historical / War / Moral Conflict", heavy_hits * 0.15, "heavy-terms", reasons)
    if len(LIGHT_TERMS & tokens) >= 2:
        add_weight(scores, "Pop Adventure / Crowdpleasers", 0.8, "lighter-terms", reasons)

    if "romance" in genres and "comedy" in genres:
        add_weight(scores, "Romance / Coming-of-Age / Heartbreak", 1.5, "romcom-blend", reasons)
    if "thriller" in genres and {"government", "journalist", "surveillance", "conspiracy", "campaign", "state", "cia", "fbi", "spy"} & tokens:
        add_weight(scores, "Political / Institutional Thriller", 1.3, "institution-thriller", reasons)
    if "thriller" in genres and {"survival", "rescue", "hostage", "mission", "fight", "combat", "escape", "assassin"} & tokens:
        add_weight(scores, "Action / Survival Pressure", 1.0, "action-thriller", reasons)
    if "science fiction" in genres and {"time", "virus", "signal", "alien", "extraterrestrial", "dystopian", "future"} & tokens:
        add_weight(scores, "Mystery / Psychological Suspense", 1.0, "science-fiction-uncertainty", reasons)
    if "science fiction" in genres and {"surveillance", "state", "government", "totalitarian", "control", "bureaucracy"} & tokens:
        add_weight(scores, "Political / Institutional Thriller", 1.0, "science-fiction-politics", reasons)
    if "action" in genres and "science fiction" in genres:
        add_weight(scores, "Action / Survival Pressure", 0.9, "action-sci-fi", reasons)
    if "drama" in genres and "comedy" in genres and not ({"satire", "satirical", "parody", "absurd", "cringe", "awkward"} & tokens):
        add_weight(scores, "Prestige / Character Drama", 0.8, "dramedy-character-focus", reasons)
    if "horror" in genres and "fantasy" in genres:
        add_weight(scores, "Mystery / Psychological Suspense", 0.9, "horror-fantasy", reasons)
    if {"freud", "jung", "psychoanalysis", "patient", "therapist"} & tokens:
        add_weight(scores, "Prestige / Character Drama", 1.2, "psychology-drama", reasons)
        add_weight(scores, "Mystery / Psychological Suspense", 0.4, "psychology-overlap", reasons)
    if {"batman", "catwoman", "penguin", "gotham"} & tokens:
        add_weight(scores, "Action / Survival Pressure", 0.9, "superhero-action", reasons)
    if {"war", "world", "occupation", "military", "soldier", "frontline"} & tokens and "science fiction" not in genres:
        add_weight(scores, "Historical / War / Moral Conflict", 0.8, "war-pressure", reasons)
    if "music" in genres and {"career", "ambition", "friendship", "identity"} & tokens:
        add_weight(scores, "Prestige / Character Drama", 1.0, "music-drama", reasons)
    if "animation" in genres or "family" in genres:
        add_weight(scores, "Pop Adventure / Crowdpleasers", 1.4, "family-animation", reasons)
    if "war" in genres or "history" in genres:
        add_weight(scores, "Historical / War / Moral Conflict", 1.6, "war-history", reasons)
    if "crime" in genres and "drama" in genres:
        add_weight(scores, "Crime / Investigation", 0.9, "crime-drama", reasons)
        add_weight(scores, "Prestige / Character Drama", 0.5, "crime-drama-overlap", reasons)
    if "thriller" in genres and "science fiction" in genres:
        add_weight(scores, "Mystery / Psychological Suspense", 1.2, "sci-fi-thriller", reasons)
        add_weight(scores, "Political / Institutional Thriller", 0.4, "sci-fi-thriller-overlap", reasons)
    if "adventure" in genres and weirdness <= 6 and emotional <= 7:
        add_weight(scores, "Pop Adventure / Crowdpleasers", 1.0, "adventure-balance", reasons)
    if "science fiction" in genres and "mystery" in genres:
        add_weight(scores, "Mystery / Psychological Suspense", 0.8, "sci-fi-mystery", reasons)
    if {"artifact", "moon", "jupiter", "memories", "memory", "nightmarish", "wake", "wakes"} & tokens:
        add_weight(scores, "Mystery / Psychological Suspense", 0.9, "enigmatic-sci-fi", reasons)
    if {"boxing", "boxer", "champion", "trainer", "mentor", "heavyweight", "fighter"} & tokens:
        add_weight(scores, "Prestige / Character Drama", 1.2, "boxing-drama", reasons)
    if {"hijacking", "hijacked", "pirates", "pirate", "astronaut", "shark", "beast", "quest", "mission"} & tokens:
        add_weight(scores, "Action / Survival Pressure", 0.9, "mission-survival", reasons)
    if {"president", "secret", "service", "assassination", "campaign", "booth"} & tokens:
        add_weight(scores, "Political / Institutional Thriller", 1.1, "state-protection", reasons)
    if {"prince", "king", "kingdom", "uncle", "throne"} & tokens:
        add_weight(scores, "Historical / War / Moral Conflict", 1.2, "royal-conflict", reasons)
    if {"vampire", "vampires", "witch", "supernatural", "entity", "haunted", "headless", "horseman", "cabin"} & tokens:
        add_weight(scores, "Mystery / Psychological Suspense", 0.9, "supernatural-horror", reasons)
    if {"playwright", "deception", "betrayal", "game", "deadly"} & tokens:
        add_weight(scores, "Mystery / Psychological Suspense", 0.8, "psychological-deception", reasons)
    if {"counterfeit", "scam", "cars", "racing", "superhero", "kryptonite"} & tokens:
        add_weight(scores, "Action / Survival Pressure", 0.8, "pop-action-signal", reasons)
    if {"spy", "agency", "global", "threat", "genius"} & tokens:
        add_weight(scores, "Political / Institutional Thriller", 0.8, "spy-agency", reasons)

    ranked = sorted(
        scores.items(),
        key=lambda pair: (
            pair[1],
            len(reasons.get(pair[0], [])),
            CLUSTER_PREFERENCE.get(pair[0], 0),
        ),
        reverse=True,
    )
    best_cluster, best_score = ranked[0]
    second_cluster = ranked[1][0] if len(ranked) > 1 else None
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    best_reason_count = len(reasons.get(best_cluster, []))
    second_reason_count = len(reasons.get(second_cluster, [])) if second_cluster else 0

    if best_score < 1.8:
        return None, scores, reasons
    if best_score - second_score < 0.35 and best_score < 2.8 and best_reason_count <= second_reason_count:
        return None, scores, reasons

    return best_cluster, scores, reasons


def dry_run_report(rows: list[dict], limit: int) -> None:
    by_cluster: dict[str, list[dict]] = defaultdict(list)
    unclassified: list[dict] = []

    for row in rows:
        cluster, scores, reasons = classify(row)
        row["proposed_cluster"] = cluster
        row["classification_scores"] = scores
        row["classification_reasons"] = reasons
        if cluster:
            by_cluster[cluster].append(row)
        else:
            unclassified.append(row)

    print(f"Total {SOURCE_CLUSTERS} titles found: {len(rows)}")
    print("Proposed cluster counts:")
    for cluster, items in sorted(by_cluster.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        print(f"- {cluster}: {len(items)}")
        for item in items[:limit]:
            reasons = ", ".join(item["classification_reasons"].get(cluster, [])[:3]) or "heuristic"
            print(f"  • {item['title']} ({item.get('year') or 'n/a'}) [{reasons}]")
    print(f"Unclassified: {len(unclassified)}")
    for item in unclassified[:limit * 2]:
        top = sorted(item["classification_scores"].items(), key=lambda pair: pair[1], reverse=True)[:3]
        guess = ", ".join(f"{cluster}:{score:.1f}" for cluster, score in top)
        print(f"  • {item['title']} ({item.get('year') or 'n/a'}) [{guess}]")


def apply_changes(rows: list[dict]) -> None:
    updates = []
    for row in rows:
        cluster, _, _ = classify(row)
        if not cluster:
            continue
        if cluster == row.get("primary_cluster"):
            continue
        updates.append((cluster, now_iso(), row["id"]))

    print(f"Applying {len(updates)} cluster updates...")
    if not updates:
        print("No changes to apply.")
        return

    with get_connection() as conn:
        conn.executemany(
            """
            UPDATE titles
            SET primary_cluster = ?, updated_at = ?
            WHERE id = ?
            """,
            updates,
        )
    print("Cluster updates written.")
    print("Next step: python3 scripts/build_edges.py")


def main() -> None:
    args = parse_args()
    if not args.dry_run and not args.apply:
        args.dry_run = True

    rows = load_candidates()
    if args.dry_run:
        dry_run_report(rows, args.limit)
    if args.apply:
        apply_changes(rows)


if __name__ == "__main__":
    main()
