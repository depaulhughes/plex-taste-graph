from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import get_connection
from app.graph_builder import apply_resolved_clusters
from app.models import json_list
from app.openai_client import FAILED_ENRICHMENT_LOG


def load_failed_log() -> dict[tuple[str, str], dict]:
    if not FAILED_ENRICHMENT_LOG.exists():
        return {}
    failures: dict[tuple[str, str], dict] = {}
    with FAILED_ENRICHMENT_LOG.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = str(payload.get("title") or "").strip()
            year = str(payload.get("year") or "").strip()
            if not title:
                continue
            failures[(title.lower(), year)] = payload
    return failures


def main() -> None:
    failed_log = load_failed_log()
    with get_connection() as conn:
        total_titles = conn.execute("SELECT COUNT(*) AS count FROM titles").fetchone()["count"]
        enriched_titles = conn.execute(
            "SELECT COUNT(*) AS count FROM titles WHERE enrichment_status = 'enriched'"
        ).fetchone()["count"]
        pending_titles = conn.execute(
            "SELECT COUNT(*) AS count FROM titles WHERE enrichment_status = 'pending'"
        ).fetchone()["count"]
        failed_titles = conn.execute(
            "SELECT COUNT(*) AS count FROM titles WHERE enrichment_status = 'failed'"
        ).fetchone()["count"]
        total_connections = conn.execute("SELECT COUNT(*) AS count FROM edges").fetchone()["count"]
        missing_primary_cluster = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM titles
            WHERE primary_cluster IS NULL OR trim(primary_cluster) = ''
            """
        ).fetchone()["count"]
        title_rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.source, t.enrichment_status, t.primary_cluster,
                   t.summary, t.genres,
                   p.tone_tags, p.theme_tags, p.style_tags, p.mood_tags,
                   p.weirdness_score, p.emotional_weight_score, p.intensity_score, p.johnny_core_score
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
        zero_connection_rows = conn.execute(
            """
            SELECT t.title, t.year, t.enrichment_status
            FROM titles t
            LEFT JOIN (
                SELECT title_id, COUNT(*) AS edge_count
                FROM (
                    SELECT source_title_id AS title_id FROM edges
                    UNION ALL
                    SELECT target_title_id AS title_id FROM edges
                )
                GROUP BY title_id
            ) ec ON ec.title_id = t.id
            WHERE COALESCE(ec.edge_count, 0) = 0
            ORDER BY t.title COLLATE NOCASE
            """
        ).fetchall()
        failed_rows = conn.execute(
            """
            SELECT id, title, year, source, enrichment_status, updated_at
            FROM titles
            WHERE enrichment_status = 'failed'
            ORDER BY updated_at DESC, title COLLATE NOCASE
            """
        ).fetchall()

    resolved_rows = apply_resolved_clusters(title_rows)
    top_clusters = Counter()
    missing_tags = 0
    for row in resolved_rows:
        if row.get("enrichment_status") == "enriched":
            top_clusters[row.get("resolved_cluster") or row.get("primary_cluster") or "Mixed / Transitional"] += 1
            tags = []
            for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
                tags.extend(json_list(row.get(key)))
            if not [tag for tag in tags if str(tag).strip()]:
                missing_tags += 1

    print("Taste Graph Data Health")
    print("=======================")
    print(f"Total titles: {total_titles}")
    print(f"Enriched titles: {enriched_titles}")
    print(f"Pending titles: {pending_titles}")
    print(f"Failed titles: {failed_titles}")
    print(f"Total connections: {total_connections}")
    print(f"Titles missing primary_cluster: {missing_primary_cluster}")
    print(f"Titles missing tags: {missing_tags}")
    print(f"Titles with zero connections: {len(zero_connection_rows)}")
    print("")
    print("Top clusters:")
    for cluster, count in top_clusters.most_common(8):
        print(f"- {cluster}: {count}")
    if not top_clusters:
        print("- No enriched clusters yet.")
    print("")
    print("Failed titles:")
    if failed_rows:
        for row in failed_rows:
            key = (str(row["title"]).lower(), str(row.get("year") or ""))
            failure = failed_log.get(key)
            error_text = None
            if failure:
                error_text = failure.get("retry_error") or failure.get("original_error")
            detail = f": {error_text}" if error_text else ""
            print(f"- {row['title']} ({row['year'] or 'n/a'}){detail}")
    else:
        print("- No failed titles.")


if __name__ == "__main__":
    main()
