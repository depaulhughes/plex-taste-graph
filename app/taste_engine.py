import json
from itertools import combinations
from typing import Any
from typing import Optional

from app.models import TitleProfile, normalise_tag

DEFAULT_STRONG_THRESHOLD = 0.34
DEFAULT_SOFT_THRESHOLD = 0.28
DEFAULT_BRIDGE_THRESHOLD = 0.24
DEFAULT_MIN_EDGES = 5
DEFAULT_MAX_EDGES = 8


def score_pair(left: TitleProfile, right: TitleProfile) -> Optional[dict[str, Any]]:
    left_tags = set(left.all_tags)
    right_tags = set(right.all_tags)
    shared_tags = sorted(left_tags & right_tags)

    tag_score = min(len(shared_tags) * 0.08, 0.36)
    cluster_score = cluster_proximity_score(left, right)
    weirdness = closeness(left, right, "weirdness_score") * 0.15
    emotional = closeness(left, right, "emotional_weight_score") * 0.15
    intensity = closeness(left, right, "intensity_score") * 0.12
    johnny = closeness(left, right, "johnny_core_score") * 0.12
    anchor_bonus = 0.08 if left.is_anchor and right.is_anchor else 0
    weight = round(tag_score + cluster_score + weirdness + emotional + intensity + johnny + anchor_bonus, 3)

    if weight < DEFAULT_STRONG_THRESHOLD:
        return None
    if not shared_tags and cluster_score < 0.08 and not high_signal_affinity(left, right):
        return None

    explanation = build_explanation(shared_tags, left, right)
    return {
        "weight": min(weight, 1.0),
        "confidence": min(weight, 1.0),
        "edge_type": "strong",
        "shared_traits": shared_tags,
        "explanation": explanation,
    }


def closeness(left: TitleProfile, right: TitleProfile, key: str) -> float:
    a = int(left.profile.get(key, 5))
    b = int(right.profile.get(key, 5))
    return max(0.0, 1 - (abs(a - b) / 9))


def build_explanation(shared_tags: list[str], left: TitleProfile, right: TitleProfile) -> str:
    lead = " / ".join(shared_tags[:3]) if shared_tags else "signal proximity"
    traits = []
    if same_cluster(left, right):
        traits.append("shared cluster gravity")
    if closeness(left, right, "weirdness_score") > 0.75:
        traits.append("similar weirdness")
    if closeness(left, right, "emotional_weight_score") > 0.75:
        traits.append("matching emotional weight")
    if closeness(left, right, "intensity_score") > 0.75:
        traits.append("parallel intensity")
    tail = f", with {', '.join(traits)}" if traits else ""
    return f"Both titles sit near the {lead} cluster{tail}."


def soft_score_pair(left: TitleProfile, right: TitleProfile) -> Optional[dict[str, Any]]:
    left_tags = set(left.all_tags)
    right_tags = set(right.all_tags)
    shared_tags = sorted(left_tags & right_tags)
    score_traits: list[str] = []

    score = 0.0
    if shared_tags:
        score += min(len(shared_tags) * 0.09, 0.3)
    cluster_match = same_cluster(left, right)
    if cluster_match:
        score += 0.22
        score_traits.append(normalise_tag(left.title.get("primary_cluster") or right.title.get("primary_cluster") or "cluster proximity"))
    else:
        score += cluster_proximity_score(left, right) * 0.4
    for key, label, weight in (
        ("weirdness_score", "similar weirdness", 0.1),
        ("emotional_weight_score", "similar emotional weight", 0.12),
        ("intensity_score", "similar intensity", 0.1),
        ("johnny_core_score", "similar Johnny-core gravity", 0.08),
    ):
        value = closeness(left, right, key)
        if value >= 0.72:
            score += value * weight
            score_traits.append(label)

    distinctive_scores = has_distinctive_score(left) and has_distinctive_score(right)
    if not shared_tags and not cluster_match and not (distinctive_scores and len(score_traits) >= 2):
        return None

    traits = shared_tags[:4] or [trait for trait in score_traits[:3] if trait != "unclustered"]
    if not traits or score < DEFAULT_SOFT_THRESHOLD:
        return None

    confidence = round(min(score, 0.48), 3)
    explanation = build_soft_explanation(traits)
    return {
        "weight": round(confidence * 0.68, 3),
        "confidence": confidence,
        "edge_type": "soft",
        "shared_traits": traits,
        "explanation": explanation,
    }


def same_cluster(left: TitleProfile, right: TitleProfile) -> bool:
    left_cluster = normalise_tag(left.title.get("primary_cluster") or "")
    right_cluster = normalise_tag(right.title.get("primary_cluster") or "")
    ignored = {"unclustered", "pending enrichment", "unknown"}
    if not left_cluster or not right_cluster or left_cluster in ignored or right_cluster in ignored:
        return False
    if left_cluster == right_cluster:
        return True
    left_parts = cluster_parts(left_cluster)
    right_parts = cluster_parts(right_cluster)
    return bool(left_parts & right_parts)


def cluster_parts(value: str) -> set[str]:
    parts = {value}
    for delimiter in ("/", "&", ","):
        for part in value.split(delimiter):
            cleaned = normalise_tag(part)
            if cleaned and cleaned not in {"moral", "rot", "and"}:
                parts.add(cleaned)
    return parts


def has_distinctive_score(profile: TitleProfile) -> bool:
    return any(
        int(profile.profile.get(key, 5)) >= 7
        for key in ("weirdness_score", "emotional_weight_score", "intensity_score", "johnny_core_score")
    )


def high_signal_affinity(left: TitleProfile, right: TitleProfile) -> bool:
    keys = ("weirdness_score", "emotional_weight_score", "intensity_score", "johnny_core_score")
    close_count = sum(1 for key in keys if closeness(left, right, key) >= 0.72)
    return close_count >= 3


def cluster_proximity_score(left: TitleProfile, right: TitleProfile) -> float:
    left_cluster = normalise_tag(left.title.get("primary_cluster") or "")
    right_cluster = normalise_tag(right.title.get("primary_cluster") or "")
    if not left_cluster or not right_cluster:
        return 0.0
    if left_cluster == right_cluster:
        return 0.16
    overlap = cluster_parts(left_cluster) & cluster_parts(right_cluster)
    return 0.08 if overlap else 0.0


def build_soft_explanation(traits: list[str]) -> str:
    lead = " / ".join(traits[:3])
    return f"Soft connection: both titles share {lead}, but this is a looser bridge rather than a strong taste match."


def bridge_score_pair(left: TitleProfile, right: TitleProfile) -> Optional[dict[str, Any]]:
    left_tags = set(left.all_tags)
    right_tags = set(right.all_tags)
    shared_tags = sorted(left_tags & right_tags)

    cluster_score = cluster_proximity_score(left, right)
    tag_score = min(len(shared_tags) * 0.06, 0.24)
    signal_score = (
        closeness(left, right, "weirdness_score") * 0.12
        + closeness(left, right, "emotional_weight_score") * 0.12
        + closeness(left, right, "intensity_score") * 0.09
        + closeness(left, right, "johnny_core_score") * 0.08
    )
    type_bonus = 0.05 if (left.title.get("type") or "") == (right.title.get("type") or "") else 0.0
    year_bonus = year_proximity_score(left, right)
    score = round(tag_score + cluster_score + signal_score + type_bonus + year_bonus, 3)

    if score < DEFAULT_BRIDGE_THRESHOLD:
        return None
    if not shared_tags and cluster_score < 0.08 and not high_signal_affinity(left, right):
        return None

    traits = shared_tags[:4]
    if not traits:
        if cluster_score >= 0.08:
            traits.append("cluster proximity")
        if closeness(left, right, "weirdness_score") >= 0.72:
            traits.append("similar weirdness")
        if closeness(left, right, "emotional_weight_score") >= 0.72:
            traits.append("similar emotional weight")
        if closeness(left, right, "intensity_score") >= 0.72:
            traits.append("similar intensity")
    explanation = build_bridge_explanation(traits[:3], left, right)
    confidence = min(score, 0.42)
    return {
        "weight": round(confidence * 0.6, 3),
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
        return 0.06
    if distance <= 12:
        return 0.04
    if distance <= 20:
        return 0.02
    return 0.0


def build_bridge_explanation(traits: list[str], left: TitleProfile, right: TitleProfile) -> str:
    lead = " / ".join(traits[:3]) if traits else "a loose taste overlap"
    return (
        f"Bridge connection: these titles share {lead}, enough to pull their neighborhoods into the same taste map "
        "without claiming a strong direct match."
    )


def strongest_edges(
    profiles: list[TitleProfile],
    per_node_limit: int = DEFAULT_MAX_EDGES,
    min_per_node: int = DEFAULT_MIN_EDGES,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for left, right in combinations(profiles, 2):
        scored = score_pair(left, right)
        if scored:
            candidates.append(
                {
                    "source_title_id": left.title_id,
                    "target_title_id": right.title_id,
                    **scored,
                }
            )

    candidates.sort(key=lambda item: item["weight"], reverse=True)
    counts: dict[int, int] = {}
    accepted: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for edge in candidates:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        if counts.get(source, 0) >= per_node_limit or counts.get(target, 0) >= per_node_limit:
            remaining.append(edge)
            continue
        accepted.append(edge)
        counts[source] = counts.get(source, 0) + 1
        counts[target] = counts.get(target, 0) + 1
    underconnected = {
        profile.title_id
        for profile in profiles
        if counts.get(profile.title_id, 0) < min_per_node
    }
    for edge in remaining:
        if not underconnected:
            break
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        if counts.get(source, 0) >= per_node_limit or counts.get(target, 0) >= per_node_limit:
            continue
        if source not in underconnected and target not in underconnected:
            continue
        accepted.append(edge)
        counts[source] = counts.get(source, 0) + 1
        counts[target] = counts.get(target, 0) + 1
        if counts.get(source, 0) >= min_per_node:
            underconnected.discard(source)
        if counts.get(target, 0) >= min_per_node:
            underconnected.discard(target)
    return accepted


def edges_with_soft_bridges(
    profiles: list[TitleProfile],
    per_node_limit: int = DEFAULT_MAX_EDGES,
    soft_limit: int = 10,
    min_per_node: int = DEFAULT_MIN_EDGES,
) -> list[dict[str, Any]]:
    strong_edges = strongest_edges(profiles, per_node_limit=per_node_limit, min_per_node=min_per_node)
    connected: dict[int, int] = {}
    existing_pairs = set()
    for edge in strong_edges:
        source = int(edge["source_title_id"])
        target = int(edge["target_title_id"])
        connected[source] = connected.get(source, 0) + 1
        connected[target] = connected.get(target, 0) + 1
        existing_pairs.add(tuple(sorted((source, target))))

    soft_edges: list[dict[str, Any]] = []
    enriched = [
        profile
        for profile in profiles
        if profile.title.get("enrichment_status", "enriched") == "enriched"
    ]
    for isolated in enriched:
        if connected.get(isolated.title_id, 0) >= min_per_node:
            continue
        candidates: list[dict[str, Any]] = []
        for other in enriched:
            if other.title_id == isolated.title_id:
                continue
            pair = tuple(sorted((isolated.title_id, other.title_id)))
            if pair in existing_pairs:
                continue
            scored = soft_score_pair(isolated, other)
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
            if connected.get(isolated.title_id, 0) >= min_per_node:
                break

    bridged_edges = connect_components_to_main(enriched, strong_edges + soft_edges, per_node_limit=per_node_limit)
    return strong_edges + soft_edges + bridged_edges


def connect_components_to_main(
    profiles: list[TitleProfile],
    existing_edges: list[dict[str, Any]],
    per_node_limit: int = DEFAULT_MAX_EDGES,
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

    for component in components[1:]:
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
        source = int(best_bridge["source_title_id"])
        target = int(best_bridge["target_title_id"])
        edge_counts[source] = edge_counts.get(source, 0) + 1
        edge_counts[target] = edge_counts.get(target, 0) + 1
        existing_pairs.add(tuple(sorted((source, target))))
        main_component.update(component_ids)
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
