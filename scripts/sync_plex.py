from pathlib import Path
import argparse
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.plex_client import PlexTasteClient, existing_plex_rating_keys, upsert_plex_rows


def bool_arg(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "y"}:
        return True
    if lowered in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("Expected true or false.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled Plex metadata import for Taste Graph.")
    parser.add_argument("--limit", type=int, help="Maximum number of Plex items to fetch.")
    parser.add_argument("--offset", type=int, default=0, help="Number of Plex items to skip before selecting a batch.")
    parser.add_argument("--library", help='Plex library name, for example "Movies".')
    parser.add_argument("--dry-run", action="store_true", help="Print what would import without writing.")
    parser.add_argument("--type", choices=["movie", "show"], default="movie", help="Plex section type to import.")
    parser.add_argument(
        "--include-existing",
        type=bool_arg,
        default=True,
        help="Include items already present in SQLite. Use false to preview/import only new titles.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    client = PlexTasteClient()
    batch = client.fetch_items_batch(
        library_name=args.library,
        media_type=args.type,
        limit=args.limit,
        offset=args.offset,
    )
    rows = batch["rows"]
    existing = existing_plex_rating_keys()
    existing_in_batch = [row for row in rows if row["plex_rating_key"] in existing]
    new_in_batch = [row for row in rows if row["plex_rating_key"] not in existing]
    if not args.include_existing:
        rows = new_in_batch

    mode = "DRY RUN" if args.dry_run else "IMPORT"
    library_label = args.library or f"all {args.type} libraries"
    print(f"{mode}: {args.type} titles from {library_label}")
    print(f"Total items found in Plex library: {batch['total_items']}")
    print(f"Offset: {batch['offset']}")
    print(f"Limit: {batch['limit'] if batch['limit'] is not None else 'all'}")
    print(f"Number selected for this batch: {batch['selected_count']}")
    print(f"Number already existing: {len(existing_in_batch)}")
    print(f"Number new: {len(new_in_batch)}")
    if not args.include_existing:
        print(f"Include existing: false -> processing {len(rows)} new title(s) only")
    else:
        print(f"Include existing: true -> processing {len(rows)} title(s)")
    for index, row in enumerate(rows, start=1):
        print(f"{index:>3}. {row['title']} ({row['year'] or 'n/a'}) [{row['plex_rating_key']}]")

    if args.dry_run:
        print("Dry run complete. No SQLite writes made.")
    else:
        inserted, updated = upsert_plex_rows(rows)
        print(f"Synced Plex titles. Inserted/updated: {inserted}/{updated}")
