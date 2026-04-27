from pathlib import Path
import argparse
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import get_connection, now_iso
from app.graph_builder import rebuild_edges
from app.openai_client import OpenAITasteClient, store_taste_profile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled OpenAI enrichment for Taste Graph titles.")
    parser.add_argument("--limit", type=int, help="Maximum number of titles to enrich.")
    parser.add_argument("--source", choices=["plex", "demo", "manual", "all"], default="all")
    parser.add_argument("--only-pending", action="store_true", help="Only enrich titles marked pending or without a profile.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry titles previously marked failed.")
    parser.add_argument("--title", help="Only enrich titles matching this text.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected titles without calling OpenAI or writing.")
    return parser.parse_args()


def selected_titles(args: argparse.Namespace) -> list[dict]:
    clauses = []
    params = []
    if args.source != "all":
        clauses.append("t.source = ?")
        params.append(args.source)
    if args.only_pending and args.retry_failed:
        clauses.append("(t.enrichment_status = 'pending' OR t.enrichment_status = 'failed')")
    elif args.only_pending:
        clauses.append("t.enrichment_status = 'pending'")
    elif args.retry_failed:
        clauses.append("t.enrichment_status = 'failed'")
    if args.title:
        clauses.append("lower(t.title) LIKE lower(?)")
        params.append(f"%{args.title}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = "LIMIT ?" if args.limit else ""
    if args.limit:
        params.append(args.limit)
    with get_connection() as conn:
        return conn.execute(
            f"""
            SELECT t.*
            FROM titles t
            LEFT JOIN taste_profiles p ON p.title_id = t.id
            {where}
            ORDER BY
                CASE t.enrichment_status
                    WHEN 'pending' THEN 0
                    WHEN 'failed' THEN 1
                    ELSE 2
                END,
                t.title COLLATE NOCASE
            {limit}
            """,
            params,
        ).fetchall()


def mark_failed(title_id: int) -> None:
    stamp = now_iso()
    with get_connection() as conn:
        conn.execute(
            "UPDATE titles SET enrichment_status = 'failed', updated_at = ? WHERE id = ?",
            (stamp, title_id),
        )


if __name__ == "__main__":
    args = parse_args()
    rows = selected_titles(args)
    print(f"Selected {len(rows)} title(s) for enrichment.")
    for index, row in enumerate(rows, start=1):
        print(f"{index:>3}. {row['title']} ({row['year'] or 'n/a'}) [{row.get('source') or 'manual'}:{row.get('enrichment_status') or 'pending'}]")

    if args.dry_run:
        print("Dry run complete. No OpenAI calls or SQLite writes made.")
        raise SystemExit(0)

    client = OpenAITasteClient()
    enriched = 0
    failed = 0
    for index, row in enumerate(rows, start=1):
        label = f"{row['title']} ({row['year'] or 'n/a'})"
        try:
            profile = client.enrich_title(row)
            store_taste_profile(row["id"], profile)
            enriched += 1
            print(f"[{index}/{len(rows)}] enriched {label}")
        except Exception as exc:
            mark_failed(row["id"])
            failed += 1
            print(f"[{index}/{len(rows)}] failed {label}: {exc}")

    edge_count = rebuild_edges()
    print(f"Enrichment complete. Enriched/failed: {enriched}/{failed}")
    print(f"Rebuilt {edge_count} graph edges.")
