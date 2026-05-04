import json
import re
from collections import defaultdict
from dataclasses import asdict
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Optional

from app.models import TitleProfile, json_list, normalise_tag

DEFAULT_STRONG_THRESHOLD = 0.41
DEFAULT_SOFT_THRESHOLD = 0.30
DEFAULT_BRIDGE_THRESHOLD = 0.24
DEFAULT_MIN_EDGES = 5
DEFAULT_MAX_EDGES = 8
DEFAULT_MAX_CANDIDATES = 150
DEFAULT_PROGRESS_EVERY = 50

BROAD_CLUSTERS = {
    "adventure / wonder",
    "action / spectacle",
    "family / light",
    "mixed / transitional",
    "core taste orbit",
}

BROAD_ONLY_TERMS = {
    "adventure",
    "wonder",
    "action",
    "spectacle",
    "family",
    "light",
    "mixed",
    "transitional",
    "drama",
    "comedy",
    "thriller",
    "mystery",
    "romance",
    "crime",
    "science fiction",
    "horror",
}

STOPWORDS = {
    "about", "after", "around", "because", "before", "between", "during", "from",
    "into", "like", "more", "than", "that", "them", "they", "this", "when",
    "want", "with", "without", "through", "toward", "towards", "their", "where",
}

TONE_GROUPS = {
    "family_light": {
        "family", "children", "kid-safe", "playful", "whimsical", "gentle",
        "light comedy", "animated family adventure", "animal rescue",
    },
    "ordeal_survival": {
        "survival", "survival thriller", "isolation", "body peril", "endurance",
        "ordeal", "psychological pressure", "trauma", "bodily risk",
    },
    "bleak_horror": {
        "bleak", "existential horror", "psychological dread", "surreal dread",
        "body horror", "moral rot", "trauma", "collapse",
    },
    "crime_violence": {
        "violent crime thriller", "crime", "ultraviolence", "mayhem", "bloodshed",
        "gangland", "moral compromise", "prestige crime",
    },
    "wonder_optimism": {
        "wonder", "adventure", "hopeful", "playful", "heartwarming",
    },
}

TONE_CONFLICTS = (
    ("family_light", "ordeal_survival"),
    ("family_light", "bleak_horror"),
    ("family_light", "crime_violence"),
    ("wonder_optimism", "bleak_horror"),
    ("wonder_optimism", "crime_violence"),
)


@dataclass
class BuildStats:
    total_titles: int = 0
    source_titles_processed: int = 0
    candidate_pairs_evaluated: int = 0
    strong_edges_accepted: int = 0
    soft_edges_accepted: int = 0
    bridge_edges_accepted: int = 0
    rejected_broad_only_pairs: int = 0
    rejected_incompatible_pairs: int = 0
    rejected_generic_pairs: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def score_pair(left: TitleProfile, right: TitleProfile, stats: Optional[BuildStats] = None) -> Optional[dict[str, Any]]:
    shared_specific = shared_specific_terms(left, right)
    cluster_score = cluster_proximity_score(left, right, shared_specific_count=len(shared_specific))
    signal_traits, signal_score = signal_similarity_details(left, right)
    compatibility_penalty = incompatibility_penalty(left, right)
    anchor_bonus = 0.04 if left.is_anchor and right.is_anchor else 0.0

    narrow_cluster = same_narrow_cluster(left, right)
    score = round(
        min(len(shared_specific) * 0.12, 0.42)
        + cluster_score
        + signal_score
        + anchor_bonus
        - compatibility_penalty,
        3,
    )

    if not has_meaningful_edge_evidence(left, right, shared_specific, signal_traits, narrow_cluster):
        if stats:
            if not shared_specific and broad_cluster_only(left, right):
                stats.rejected_broad_only_pairs += 1
            elif compatibility_penalty > 0:
                stats.rejected_incompatible_pairs += 1
        return None
    if score < DEFAULT_STRONG_THRESHOLD:
        if stats and compatibility_penalty > 0:
            stats.rejected_incompatible_pairs += 1
        return None

    traits = shared_specific[:4]
    if len(traits) < 3:
        traits.extend(trait for trait in signal_traits if trait not in traits)
    traits = traits[:4]
    if not traits:
        return None

    explanation = build_explanation(traits, signal_traits, left, right)
    if generic_explanation(explanation, traits):
        if stats:
            stats.rejected_generic_pairs += 1
        return None

    confidence = min(score, 0.96)
    return {
        "weight": confidence,
        "confidence": confidence,
        "edge_type": "strong",
        "shared_traits": traits,
        "explanation": explanation,
    }


def closeness(left: TitleProfile, right: TitleProfile, key: str) -> float:
    a = int(left.profile.get(key, 5))
    b = int(right.profile.get(key, 5))
    return max(0.0, 1 - (abs(a - b) / 9))


def build_explanation(traits: list[str], signal_traits: list[str], left: TitleProfile, right: TitleProfile) -> str:
    lead = " / ".join(traits[:3])
    tail_bits = [trait for trait in signal_traits[:3] if trait not in traits]
    if same_narrow_cluster(left, right):
        tail_bits.append("narrow cluster overlap")
    tail = f", with {', '.join(tail_bits[:3])}" if tail_bits else ""
    return f"Both titles connect through {lead}{tail}."


def soft_score_pair(left: TitleProfile, right: TitleProfile, stats: Optional[BuildStats] = None) -> Optional[dict[str, Any]]:
    shared_specific = shared_specific_terms(left, right)
    signal_traits, signal_score = signal_similarity_details(left, right)
    cluster_score = cluster_proximity_score(left, right, shared_specific_count=len(shared_specific))
    penalty = incompatibility_penalty(left, right) * 0.75

    distinctive_scores = has_distinctive_score(left) and has_distinctive_score(right)
    same_narrow = same_narrow_cluster(left, right)
    if not shared_specific and not (distinctive_scores and len(signal_traits) >= 2) and not (same_narrow and signal_traits):
        if stats and incompatibility_penalty(left, right) > 0:
            stats.rejected_incompatible_pairs += 1
        return None

    score = round(
        min(len(shared_specific) * 0.1, 0.28)
        + cluster_score
        + signal_score * 0.85
        - penalty,
        3,
    )
    if score < DEFAULT_SOFT_THRESHOLD:
        if stats and penalty > 0:
            stats.rejected_incompatible_pairs += 1
        return None

    traits = shared_specific[:3] or signal_traits[:3]
    explanation = build_soft_explanation(traits, signal_traits)
    if generic_explanation(explanation, traits):
        if stats:
            stats.rejected_generic_pairs += 1
        return None

    confidence = round(min(score, 0.58), 3)
    return {
        "weight": round(confidence * 0.78, 3),
        "confidence": confidence,
        "edge_type": "soft",
        "shared_traits": traits[:4],
        "explanation": explanation,
    }


def same_cluster(left: TitleProfile, right: TitleProfile) -> bool:
    return normalise_tag(left.title.get("primary_cluster") or "") == normalise_tag(right.title.get("primary_cluster") or "")


def same_narrow_cluster(left: TitleProfile, right: TitleProfile) -> bool:
    left_cluster = normalise_tag(left.title.get("primary_cluster") or "")
    right_cluster = normalise_tag(right.title.get("primary_cluster") or "")
    if not left_cluster or not right_cluster:
        return False
    if left_cluster in BROAD_CLUSTERS or right_cluster in BROAD_CLUSTERS:
        return False
    return left_cluster == right_cluster


def cluster_parts(value: str) -> set[str]:
    parts = {value}
    for delimiter in ("/", "&", ","):
        for part in value.split(delimiter):
            cleaned = normalise_tag(part)
            if cleaned and cleaned not in {"and"}:
                parts.add(cleaned)
    return parts


def has_distinctive_score(profile: TitleProfile) -> bool:
    return any(
        int(profile.profile.get(key, 5)) >= 7
        for key in ("weirdness_score", "emotional_weight_score", "intensity_score", "johnny_core_score")
    )


def high_signal_affinity(left: TitleProfile, right: TitleProfile) -> bool:
    keys = ("weirdness_score", "emotional_weight_score", "intensity_score", "johnny_core_score")
    close_count = sum(1 for key in keys if closeness(left, right, key) >= 0.8)
    return close_count >= 3


def cluster_proximity_score(left: TitleProfile, right: TitleProfile, shared_specific_count: int = 0) -> float:
    left_cluster = normalise_tag(left.title.get("primary_cluster") or "")
    right_cluster = normalise_tag(right.title.get("primary_cluster") or "")
    if not left_cluster or not right_cluster:
        return 0.0
    if left_cluster == right_cluster:
        if left_cluster in BROAD_CLUSTERS:
            return 0.0
        return 0.08 if shared_specific_count else 0.04
    overlap = cluster_parts(left_cluster) & cluster_parts(right_cluster)
    if not overlap:
        return 0.0
    if any(part in BROAD_ONLY_TERMS for part in overlap):
        return 0.0
    return 0.03 if shared_specific_count else 0.0


def build_soft_explanation(traits: list[str], signal_traits: list[str]) -> str:
    lead = " / ".join((traits or signal_traits)[:3])
    return f"Soft connection: both titles share {lead}, but the overall tone is a looser neighborhood fit."


def bridge_score_pair(left: TitleProfile, right: TitleProfile) -> Optional[dict[str, Any]]:
    shared_specific = shared_specific_terms(left, right)
    signal_traits, signal_score = signal_similarity_details(left, right)
    cluster_score = cluster_proximity_score(left, right, shared_specific_count=len(shared_specific))
    type_bonus = 0.05 if (left.title.get("type") or "") == (right.title.get("type") or "") else 0.0
    year_bonus = year_proximity_score(left, right)
    penalty = incompatibility_penalty(left, right) * 0.6
    score = round(
        min(len(shared_specific) * 0.08, 0.24)
        + cluster_score
        + signal_score * 0.72
        + type_bonus
        + year_bonus
        - penalty,
        3,
    )

    if score < DEFAULT_BRIDGE_THRESHOLD:
        return None
    if not shared_specific and not signal_traits:
        return None

    traits = shared_specific[:3] or signal_traits[:3]
    explanation = build_bridge_explanation(traits[:3], left, right)
    if generic_explanation(explanation, traits):
        return None

    confidence = min(score, 0.46)
    return {
        "weight": round(confidence * 0.64, 3),
        "confidence": round(confidence, 3),
        "edge_type": "bridge",
        "shared_traits": traits[:4],
        "explanation": explanation,
    }


def year_proximity_score(left: TitleProfile, right: TitleProfile) -> float:
    left_year = left.title.get("year")
    right_year = right.title.get("year")
    try:
        if left_year is None or right_year is None:
            return 0.0
        distance = abs(int(left_year) - int(right_year))
    except (TypeError, ValueError):
        return 0.0
    if distance <= 5:
        return 0.05
    if distance <= 12:
        return 0.03
    if distance <= 20:
        return 0.015
    return 0.0


def build_bridge_explanation(traits: list[str], left: TitleProfile, right: TitleProfile) -> str:
    lead = " / ".join(traits[:3]) if traits else "a loose taste overlap"
    return (
        f"Bridge connection: these titles share {lead}, enough to link their nearby neighborhoods "
        "without claiming the same exact vibe."
    )


def candidate_pool(
    profile: TitleProfile,
    profiles_by_id: dict[int, TitleProfile],
    term_index: dict[str, set[int]],
    cluster_index: dict[str, set[int]],
    type_index: dict[str, set[int]],
    max_candidates: int,
) -> list[int]:
    candidate_ids: set[int] = set()
    profile_terms = specific_terms(profile)
    for term in profile_terms:
        candidate_ids.update(term_index.get(term, set()))

    cluster = canonical_term(profile.title.get("primary_cluster") or "")
    if cluster:
        candidate_ids.update(cluster_index.get(cluster, set()))
        for part in cluster_parts(cluster):
            if part and part not in BROAD_ONLY_TERMS:
                candidate_ids.update(term_index.get(part, set()))

    type_value = normalise_tag(profile.title.get("type") or "")
    if type_value:
        candidate_ids.update(type_index.get(type_value, set()))

    if len(candidate_ids) < max_candidates:
        source_scores = signal_vector(profile)
        for other_id, other in profiles_by_id.items():
            if other_id == profile.title_id:
                continue
            if quick_candidate_affinity(profile, other, source_scores) >= 0.6:
                candidate_ids.add(other_id)

    candidate_ids.discard(profile.title_id)
    ranked = sorted(
        candidate_ids,
        key=lambda other_id: quick_candidate_affinity(profile, profiles_by_id[other_id]),
        reverse=True,
    )
    return ranked[:max_candidates]


def strongest_edges(
    profiles: list[TitleProfile],
    per_node_limit: int = DEFAULT_MAX_EDGES,
    min_per_node: int = DEFAULT_MIN_EDGES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    min_score: float = 0.60,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[list[dict[str, Any]], BuildStats, list[TitleProfile], dict[int, list[int]]]:
    enriched = [
        profile
        for profile in profiles
        if profile.title.get("enrichment_status", "enriched") == "enriched"
    ]
    stats = BuildStats(total_titles=len(enriched))
    profiles_by_id = {profile.title_id: profile for profile in enriched}
    term_index: dict[str, set[int]] = defaultdict(set)
    cluster_index: dict[str, set[int]] = defaultdict(set)
    type_index: dict[str, set[int]] = defaultdict(set)
    for profile in enriched:
        for term in specific_terms(profile):
            term_index[term].add(profile.title_id)
        cluster = canonical_term(profile.title.get("primary_cluster") or "")
        if cluster:
            cluster_index[cluster].add(profile.title_id)
        type_value = normalise_tag(profile.title.get("type") or "")
        if type_value:
            type_index[type_value].add(profile.title_id)

    counts: dict[int, int] = {profile.title_id: 0 for profile in enriched}
    accepted: list[dict[str, Any]] = []
    existing_pairs: set[tuple[int, int]] = set()
    neighbor_rankings: dict[int, list[int]] = {}

    for index, profile in enumerate(enriched, start=1):
        pool = candidate_pool(profile, profiles_by_id, term_index, cluster_index, type_index, max_candidates=max_candidates)
        neighbor_rankings[profile.title_id] = pool
        local_candidates: list[dict[str, Any]] = []
        source_scores = signal_vector(profile)
        for other_id in pool:
            if other_id <= profile.title_id:
                continue
            pair = (profile.title_id, other_id)
            if pair in existing_pairs:
                continue
            other = profiles_by_id[other_id]
            quick_score = quick_candidate_affinity(profile, other, source_scores)
            if quick_score < min_score and not has_pool_exception(profile, other):
                if broad_cluster_only(profile, other):
                    stats.rejected_broad_only_pairs += 1
                elif incompatibility_penalty(profile, other) > 0:
                    stats.rejected_incompatible_pairs += 1
                continue
            stats.candidate_pairs_evaluated += 1
            scored = score_pair(profile, other, stats=stats)
            if scored:
                local_candidates.append(
                    {
                        "source_title_id": profile.title_id,
                        "target_title_id": other.title_id,
                        **scored,
                    }
                )
        local_candidates.sort(key=lambda item: item["weight"], reverse=True)
        for edge in local_candidates:
            source = int(edge["source_title_id"])
            target = int(edge["target_title_id"])
            if counts.get(source, 0) >= per_node_limit or counts.get(target, 0) >= per_node_limit:
                continue
            accepted.append(edge)
            existing_pairs.add((source, target))
            counts[source] = counts.get(source, 0) + 1
            counts[target] = counts.get(target, 0) + 1
            stats.strong_edges_accepted += 1
            if counts[source] >= per_node_limit:
                break
        stats.source_titles_processed = index
        if progress_callback and (index == len(enriched) or index % progress_every == 0):
            progress_callback({
                "stage": "strong",
                "processed": index,
                "total": len(enriched),
                **stats.as_dict(),
            })

    underconnected = {
        profile.title_id
        for profile in enriched
        if counts.get(profile.title_id, 0) < min_per_node
    }
    if underconnected:
        for title_id in list(underconnected):
            profile = profiles_by_id[title_id]
            source_scores = signal_vector(profile)
            fallback_candidates = []
            for other_id in neighbor_rankings.get(title_id, []):
                pair = tuple(sorted((title_id, other_id)))
                if pair in existing_pairs:
                    continue
                other = profiles_by_id[other_id]
                if quick_candidate_affinity(profile, other, source_scores) < min_score and not has_pool_exception(profile, other):
                    continue
                scored = score_pair(profile, other, stats=stats)
                if scored:
                    fallback_candidates.append({
                        "source_title_id": title_id,
                        "target_title_id": other_id,
                        **scored,
                    })
            fallback_candidates.sort(key=lambda item: item["weight"], reverse=True)
            for edge in fallback_candidates:
                source = int(edge["source_title_id"])
                target = int(edge["target_title_id"])
                if counts.get(source, 0) >= per_node_limit or counts.get(target, 0) >= per_node_limit:
                    continue
                accepted.append(edge)
                existing_pairs.add(tuple(sorted((source, target))))
                counts[source] = counts.get(source, 0) + 1
                counts[target] = counts.get(target, 0) + 1
                stats.strong_edges_accepted += 1
                if counts.get(title_id, 0) >= min_per_node:
                    break

    return accepted, stats, enriched, neighbor_rankings


def edges_with_soft_bridges(
    profiles: list[TitleProfile],
    per_node_limit: int = DEFAULT_MAX_EDGES,
    soft_limit: int = 10,
    min_per_node: int = DEFAULT_MIN_EDGES,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    min_score: float = 0.60,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> tuple[list[dict[str, Any]], BuildStats]:
    strong_edges, stats, enriched, neighbor_rankings = strongest_edges(
        profiles,
        per_node_limit=per_node_limit,
        min_per_node=min_per_node,
        max_candidates=max_candidates,
        min_score=min_score,
        progress_every=progress_every,
        progress_callback=progress_callback,
    )
    connected: dict[int, int] = {}
    existing_pairs = set()
    profiles_by_id = {profile.title_id: profile for profile in enriched}
    for edge in strong_edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        connected[source] = connected.get(source, 0) + 1
        connected[target] = connected.get(target, 0) + 1
        existing_pairs.add(tuple(sorted((source, target))))

    soft_edges: list[dict[str, Any]] = []
    underconnected = [profile for profile in enriched if connected.get(profile.title_id, 0) < min_per_node]
    for index, isolated in enumerate(underconnected, start=1):
        candidates: list[dict[str, Any]] = []
        for other_id in neighbor_rankings.get(isolated.title_id, []):
            if other_id == isolated.title_id:
                continue
            pair = tuple(sorted((isolated.title_id, other_id)))
            if pair in existing_pairs:
                continue
            other = profiles_by_id[other_id]
            stats.candidate_pairs_evaluated += 1
            scored = soft_score_pair(isolated, other, stats=stats)
            if scored:
                candidates.append(
                    {
                        "source_title_id": isolated.title_id,
                        "target_title_id": other.title_id,
                        **scored,
                    }
                )
        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        needed = max(min_per_node - connected.get(isolated.title_id, 0), 0)
        for edge in candidates[:max(soft_limit, needed)]:
            pair = tuple(sorted((int(edge["source_title_id"]), int(edge["target_title_id"]))))
            if pair in existing_pairs:
                continue
            source = int(edge["source_title_id"])
            target = int(edge["target_title_id"])
            if connected.get(source, 0) >= per_node_limit or connected.get(target, 0) >= per_node_limit:
                continue
            soft_edges.append(edge)
            existing_pairs.add(pair)
            connected[source] = connected.get(source, 0) + 1
            connected[target] = connected.get(target, 0) + 1
            stats.soft_edges_accepted += 1
            if connected.get(isolated.title_id, 0) >= min_per_node:
                break
        if progress_callback and (index == len(underconnected) or index % progress_every == 0):
            progress_callback({
                "stage": "soft",
                "processed": index,
                "total": len(underconnected),
                **stats.as_dict(),
            })

    bridged_edges = connect_components_to_main(
        enriched,
        strong_edges + soft_edges,
        per_node_limit=per_node_limit,
        stats=stats,
        progress_callback=progress_callback,
    )
    return strong_edges + soft_edges + bridged_edges, stats


def connect_components_to_main(
    profiles: list[TitleProfile],
    existing_edges: list[dict[str, Any]],
    per_node_limit: int = DEFAULT_MAX_EDGES,
    stats: Optional[BuildStats] = None,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> list[dict[str, Any]]:
    by_id = {profile.title_id: profile for profile in profiles}
    edge_counts = {profile.title_id: 0 for profile in profiles}
    existing_pairs = set()
    for edge in existing_edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        edge_counts[source] = edge_counts.get(source, 0) + 1
        edge_counts[target] = edge_counts.get(target, 0) + 1
        existing_pairs.add(tuple(sorted((source, target))))

    components = connected_components([profile.title_id for profile in profiles], existing_edges)
    if len(components) <= 1:
        return []

    components.sort(key=len, reverse=True)
    main_component = set(components[0])
    bridge_edges: list[dict[str, Any]] = []
    bridge_node_limit = per_node_limit + 2

    for index, component in enumerate(components[1:], start=1):
        component_ids = set(component)
        best_bridge = None
        for source_id in component_ids:
            if edge_counts.get(source_id, 0) >= bridge_node_limit:
                continue
            for target_id in main_component:
                if edge_counts.get(target_id, 0) >= bridge_node_limit:
                    continue
                pair = tuple(sorted((source_id, target_id)))
                if pair in existing_pairs:
                    continue
                if stats:
                    stats.candidate_pairs_evaluated += 1
                scored = bridge_score_pair(by_id[source_id], by_id[target_id])
                if not scored:
                    continue
                candidate = {
                    "source_title_id": source_id,
                    "target_title_id": target_id,
                    **scored,
                }
                if (
                    best_bridge is None
                    or candidate["confidence"] > best_bridge["confidence"]
                    or (
                        candidate["confidence"] == best_bridge["confidence"]
                        and candidate["weight"] > best_bridge["weight"]
                    )
                ):
                    best_bridge = candidate
        if best_bridge is None:
            continue
        bridge_edges.append(best_bridge)
        if stats:
            stats.bridge_edges_accepted += 1
        source = int(best_bridge["source_title_id"])
        target = int(best_bridge["target_title_id"])
        edge_counts[source] = edge_counts.get(source, 0) + 1
        edge_counts[target] = edge_counts.get(target, 0) + 1
        existing_pairs.add(tuple(sorted((source, target))))
        main_component.update(component_ids)
        if progress_callback:
            progress_callback({
                "stage": "bridge",
                "processed": index,
                "total": max(len(components) - 1, 1),
                **(stats.as_dict() if stats else {}),
            })
    return bridge_edges


def connected_components(node_ids: list[int], edges: list[dict[str, Any]]) -> list[set[int]]:
    adjacency = {node_id: set() for node_id in node_ids}
    for edge in edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        if source not in adjacency or target not in adjacency:
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)

    seen: set[int] = set()
    components: list[set[int]] = []
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
        components.append(component)
    return components


def edge_db_payload(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        **edge,
        "confidence": edge.get("confidence", edge["weight"]),
        "edge_type": edge.get("edge_type", "strong"),
        "shared_traits": json.dumps(edge["shared_traits"]),
    }


def signal_vector(profile: TitleProfile) -> tuple[float, float, float, float, float]:
    cached = getattr(profile, "_signal_vector_cache", None)
    if cached is not None:
        return cached
    value = tuple(
        float(profile.profile.get(key, 5) or 5)
        for key in ("weirdness_score", "emotional_weight_score", "intensity_score", "johnny_core_score", "pacing_score")
    )
    setattr(profile, "_signal_vector_cache", value)
    return value


def canonical_term(value: str) -> str:
    normalized = normalise_tag(value or "")
    normalized = re.sub(r"^[\[\]\(\)\{\}\"'`]+|[\[\]\(\)\{\}\"'`]+$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def specific_terms(profile: TitleProfile) -> set[str]:
    cached = getattr(profile, "_specific_terms_cache", None)
    if cached is not None:
        return cached
    terms = set()
    for tag in profile.all_tags:
        normalized = canonical_term(tag)
        if normalized and normalized not in BROAD_ONLY_TERMS:
            terms.add(normalized)
    for tag in profile.hook_tags:
        normalized = canonical_term(tag)
        if normalized and normalized not in BROAD_ONLY_TERMS:
            terms.add(normalized)
    for token in profile.viewing_context_terms:
        normalized = canonical_term(token)
        if normalized and normalized not in STOPWORDS and normalized not in BROAD_ONLY_TERMS:
            terms.add(normalized)
    for genre in json_list(profile.title.get("genres")):
        normalized = canonical_term(genre)
        if normalized and normalized not in BROAD_ONLY_TERMS:
            terms.add(normalized)
    setattr(profile, "_specific_terms_cache", terms)
    return terms


def shared_specific_terms(left: TitleProfile, right: TitleProfile) -> list[str]:
    shared = sorted(specific_terms(left) & specific_terms(right))
    return [term for term in shared if term and term not in BROAD_ONLY_TERMS]


def broad_cluster_only(left: TitleProfile, right: TitleProfile) -> bool:
    left_cluster = canonical_term(left.title.get("primary_cluster") or "")
    right_cluster = canonical_term(right.title.get("primary_cluster") or "")
    return bool(left_cluster and right_cluster and left_cluster == right_cluster and left_cluster in BROAD_CLUSTERS)


def quick_candidate_affinity(
    left: TitleProfile,
    right: TitleProfile,
    left_scores: Optional[tuple[float, float, float, float, float]] = None,
) -> float:
    left_terms = specific_terms(left)
    right_terms = specific_terms(right)
    shared = left_terms & right_terms
    shared_specific_score = min(len(shared) * 0.2, 0.5)
    cluster_score = cluster_proximity_score(left, right, shared_specific_count=len(shared))
    compatibility = 0.0 if incompatibility_penalty(left, right) == 0 else -0.25
    if left_scores is None:
        left_scores = signal_vector(left)
    right_scores = signal_vector(right)
    score_diffs = [abs(a - b) for a, b in zip(left_scores, right_scores)]
    signal_score = max(0.0, 1 - (sum(score_diffs) / (len(score_diffs) * 9)))
    hook_overlap = len(set(left.hook_tags) & set(right.hook_tags))
    return round(shared_specific_score + cluster_score + signal_score * 0.4 + min(hook_overlap * 0.08, 0.16) + compatibility, 3)


def has_pool_exception(left: TitleProfile, right: TitleProfile) -> bool:
    return (
        len(shared_specific_terms(left, right)) >= 1
        or same_narrow_cluster(left, right)
        or high_signal_affinity(left, right)
    )


def signal_similarity_details(left: TitleProfile, right: TitleProfile) -> tuple[list[str], float]:
    traits: list[str] = []
    score = 0.0
    for key, label, weight, threshold in (
        ("weirdness_score", "similar weirdness", 0.12, 0.78),
        ("emotional_weight_score", "similar emotional weight", 0.14, 0.78),
        ("intensity_score", "similar intensity", 0.12, 0.78),
        ("johnny_core_score", "similar Johnny-core pull", 0.1, 0.78),
        ("pacing_score", "similar pacing", 0.06, 0.8),
    ):
        value = closeness(left, right, key)
        score += value * weight
        if value >= threshold:
            traits.append(label)
    return traits, round(score, 3)


def tone_signatures(profile: TitleProfile) -> set[str]:
    cached = getattr(profile, "_tone_signatures_cache", None)
    if cached is not None:
        return cached
    terms = specific_terms(profile) | set(profile.all_tags)
    signatures = set()
    for group_name, group_terms in TONE_GROUPS.items():
        normalized_group = {normalise_tag(term) for term in group_terms}
        if terms & normalized_group:
            signatures.add(group_name)
    setattr(profile, "_tone_signatures_cache", signatures)
    return signatures


def incompatibility_penalty(left: TitleProfile, right: TitleProfile) -> float:
    left_tones = tone_signatures(left)
    right_tones = tone_signatures(right)
    penalty = 0.0
    for a, b in TONE_CONFLICTS:
        if (a in left_tones and b in right_tones) or (b in left_tones and a in right_tones):
            penalty += 0.24
    return penalty


def compatible_tone(left: TitleProfile, right: TitleProfile) -> bool:
    return incompatibility_penalty(left, right) == 0.0


def has_meaningful_edge_evidence(
    left: TitleProfile,
    right: TitleProfile,
    shared_specific: list[str],
    signal_traits: list[str],
    narrow_cluster: bool,
) -> bool:
    if len(shared_specific) >= 2:
        return True
    if len(shared_specific) >= 1 and narrow_cluster:
        return True
    if len(shared_specific) >= 1 and high_signal_affinity(left, right) and compatible_tone(left, right):
        return True
    return False


def generic_explanation(explanation: str, traits: list[str]) -> bool:
    if not traits:
        return True
    lower = explanation.lower()
    if "adventure / wonder" in lower and len(traits) <= 1:
        return True
    if "mixed / transitional" in lower:
        return True
    if "cluster overlap" in lower and len(traits) <= 1:
        return True
    return False
