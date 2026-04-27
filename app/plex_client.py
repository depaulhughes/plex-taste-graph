import json
from typing import Any
from typing import Optional
from urllib.parse import quote

from plexapi.server import PlexServer

from app.config import get_settings
from app.db import get_connection, now_iso
from app.models import ANCHOR_TITLES, normalise_title


class PlexTasteClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.plex_base_url or not settings.plex_token:
            raise RuntimeError("PLEX_BASE_URL and PLEX_TOKEN are required for Plex sync.")
        self.base_url = settings.plex_base_url.rstrip("/")
        self.plex = PlexServer(settings.plex_base_url, settings.plex_token)

    def fetch_items(
        self,
        library_name: Optional[str] = None,
        media_type: str = "movie",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        batch = self.fetch_items_batch(
            library_name=library_name,
            media_type=media_type,
            limit=limit,
            offset=offset,
        )
        return batch["rows"]

    def fetch_items_batch(
        self,
        library_name: Optional[str] = None,
        media_type: str = "movie",
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        offset = max(0, int(offset or 0))
        rows: list[dict[str, Any]] = []
        total_items = 0
        for section in self.plex.library.sections():
            if library_name and section.title != library_name:
                continue
            if section.type != media_type:
                continue
            items = section.all()
            total_items += len(items)
            section_slice = items[offset:]
            if limit is not None:
                section_slice = section_slice[:limit]
            for item in section_slice:
                rows.append(self._item_to_payload(item, section.type))
            if library_name:
                break
        if limit is not None:
            rows = rows[:limit]
        return {
            "rows": rows,
            "total_items": total_items,
            "offset": offset,
            "limit": limit,
            "selected_count": len(rows),
        }

    def fetch_movies(self) -> list[dict[str, Any]]:
        return self.fetch_items(media_type="movie")

    def _item_to_payload(self, item: Any, media_type: str) -> dict[str, Any]:
        rating_key = str(item.ratingKey)
        title = item.title
        return {
            "plex_rating_key": rating_key,
            "title": title,
            "year": getattr(item, "year", None),
            "type": "show" if media_type == "show" else "movie",
            "summary": getattr(item, "summary", "") or "",
            "genres": json.dumps([g.tag for g in getattr(item, "genres", [])]),
            "directors": json.dumps([d.tag for d in getattr(item, "directors", [])]),
            "writers": json.dumps([w.tag for w in getattr(item, "writers", [])]),
            "actors": json.dumps([r.tag for r in getattr(item, "roles", [])[:10]]),
            "poster_url": f"/api/poster/{quote(rating_key)}",
            "plex_url": f"{self.base_url}/web/index.html#!/server/{self.plex.machineIdentifier}/details?key=%2Flibrary%2Fmetadata%2F{rating_key}",
            "source": "plex",
            "enrichment_status": "pending",
            "is_anchor": 1 if normalise_title(title) in ANCHOR_TITLES else 0,
            "primary_cluster": None,
            "last_enriched_at": None,
            "added_at": str(getattr(item, "addedAt", "") or ""),
        }


def existing_plex_rating_keys() -> set[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT plex_rating_key FROM titles WHERE plex_rating_key IS NOT NULL"
        ).fetchall()
    return {str(row["plex_rating_key"]) for row in rows}


def upsert_plex_rows(rows: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    updated = 0
    stamp = now_iso()
    with get_connection() as conn:
        for row in rows:
            existing = conn.execute(
                "SELECT id, enrichment_status, primary_cluster, last_enriched_at FROM titles WHERE plex_rating_key = ?",
                (row["plex_rating_key"],),
            ).fetchone()
            if existing:
                updated += 1
            else:
                inserted += 1
            conn.execute(
                """
                INSERT INTO titles (
                    plex_rating_key, title, year, type, summary, genres, directors,
                    writers, actors, poster_url, plex_url, source, enrichment_status,
                    is_anchor, primary_cluster, last_enriched_at, added_at, created_at, updated_at
                )
                VALUES (
                    :plex_rating_key, :title, :year, :type, :summary, :genres, :directors,
                    :writers, :actors, :poster_url, :plex_url, :source, :enrichment_status,
                    :is_anchor, :primary_cluster, :last_enriched_at, :added_at, :created_at, :updated_at
                )
                ON CONFLICT(plex_rating_key) DO UPDATE SET
                    title=excluded.title,
                    year=excluded.year,
                    type=excluded.type,
                    summary=excluded.summary,
                    genres=excluded.genres,
                    directors=excluded.directors,
                    writers=excluded.writers,
                    actors=excluded.actors,
                    poster_url=excluded.poster_url,
                    plex_url=excluded.plex_url,
                    source='plex',
                    is_anchor=excluded.is_anchor,
                    added_at=excluded.added_at,
                    updated_at=excluded.updated_at
                """,
                {
                    **row,
                    "enrichment_status": existing["enrichment_status"] if existing else "pending",
                    "primary_cluster": existing["primary_cluster"] if existing else row.get("primary_cluster"),
                    "last_enriched_at": existing["last_enriched_at"] if existing else None,
                    "created_at": stamp,
                    "updated_at": stamp,
                },
            )
    return inserted, updated


def upsert_titles_from_plex(
    library_name: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    media_type: str = "movie",
    include_existing: bool = True,
) -> tuple[int, int, list[dict[str, Any]]]:
    client = PlexTasteClient()
    rows = client.fetch_items(
        library_name=library_name,
        media_type=media_type,
        limit=limit,
        offset=offset,
    )
    if not include_existing:
        existing = existing_plex_rating_keys()
        rows = [row for row in rows if row["plex_rating_key"] not in existing]
    inserted, updated = upsert_plex_rows(rows)
    return inserted, updated, rows
