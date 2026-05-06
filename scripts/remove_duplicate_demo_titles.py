from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import get_connection


def duplicate_demo_ids() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.id, d.title, d.year, d.type, d.source
            FROM titles d
            WHERE d.source = 'demo'
              AND EXISTS (
                SELECT 1
                FROM titles p
                WHERE p.source = 'plex'
                  AND lower(p.title) = lower(d.title)
                  AND COALESCE(p.year, -1) = COALESCE(d.year, -1)
                  AND p.type = d.type
              )
            ORDER BY lower(d.title), d.year, d.type, d.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def main() -> None:
    doomed = duplicate_demo_ids()
    print(f"duplicate demo title rows: {len(doomed)}")
    for row in doomed:
        print(f"- delete demo id={row['id']} {row['title']} ({row['year']}) [{row['type']}]")

    if not doomed:
        return

    ids = [row["id"] for row in doomed]
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        conn.execute(f"DELETE FROM titles WHERE id IN ({placeholders})", ids)

    print(f"deleted demo duplicate rows: {len(ids)}")


if __name__ == "__main__":
    main()
