import random

from app.db import get_connection


GENERIC_FALLBACKS = [
    {"label": "Most Johnny-core", "question": "Most Johnny-core titles"},
    {"label": "Weird but heavy", "question": "Weird but emotionally heavy"},
    {"label": "Lighter picks", "question": "Show me lighter picks"},
    {"label": "Intense picks", "question": "Show me intense picks"},
    {"label": "Surprise me", "question": "Surprise me from this map"},
]

PLEX_PREFERRED_MIN = 12

TITLE_TEMPLATES = [
    ("Closest to {title}", "Closest to {title}"),
    ("Movies like {title}", "Movies like {title}"),
    ("Weirder near {title}", "Weirder picks near {title}"),
    ("Heavier than {title}", "Emotionally heavier than {title}"),
    ("Safer near {title}", "Safer picks near {title}"),
]

TASTE_TEMPLATES = [
    ("Most Johnny-core", "Most Johnny-core titles"),
    ("Weird but heavy", "Weird but emotionally heavy"),
    ("Entry into {cluster}", "Best entry points into {cluster}"),
    ("Lighter picks", "Show me lighter picks"),
    ("Intense picks", "Show me intense picks"),
    ("Surprise me", "Surprise me from this map"),
]


def suggested_asks() -> dict:
    titles = enriched_titles_with_edges(prefer_plex=True)
    if len(titles) < 5:
        return {"suggestions": random.sample(GENERIC_FALLBACKS, k=min(5, len(GENERIC_FALLBACKS)))}

    clusters = [row["primary_cluster"] for row in titles if row.get("primary_cluster") and row["primary_cluster"] != "Outliers"]
    suggestions = []
    title_pool = titles[:]
    random.shuffle(title_pool)
    for row in title_pool[:3]:
        label_template, question_template = random.choice(TITLE_TEMPLATES)
        suggestions.append(
            {
                "label": label_template.format(title=row["title"]),
                "question": question_template.format(title=row["title"]),
            }
        )

    taste_pool = TASTE_TEMPLATES[:]
    random.shuffle(taste_pool)
    for label_template, question_template in taste_pool:
        if len(suggestions) >= 6:
            break
        if "{cluster}" in question_template:
            if not clusters:
                continue
            cluster = random.choice(clusters)
            suggestions.append(
                {
                    "label": label_template.format(cluster=cluster),
                    "question": question_template.format(cluster=cluster),
                }
            )
        else:
            suggestions.append({"label": label_template, "question": question_template})

    random.shuffle(suggestions)
    return {"suggestions": suggestions[:6]}


def selected_title_suggested_asks(selected_title_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT t.id, t.title, t.enrichment_status
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            WHERE t.id = ?
            LIMIT 1
            """,
            (selected_title_id,),
        ).fetchone()

    if not row or row["enrichment_status"] != "enriched":
        return suggested_asks()

    title = row["title"]
    suggestions = [
        {"label": f"Closest to {title}", "question": f"Closest to {title}", "intent": "closest"},
        {"label": f"Weirder than {title}", "question": f"Weirder than {title}", "intent": "weirder"},
        {"label": f"Heavier than {title}", "question": f"Heavier than {title}", "intent": "heavier"},
        {"label": f"Safer near {title}", "question": f"Safer picks near {title}", "intent": "safer"},
        {"label": f"Why {title} connects", "question": f"Why does {title} connect to these?", "intent": "why_connects"},
    ]
    return {"suggestions": suggestions}


def enriched_titles_with_edges(prefer_plex: bool = False) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.title, t.year, t.primary_cluster, t.source,
                   COUNT(e.id) AS edge_count
            FROM titles t
            JOIN taste_profiles p ON p.title_id = t.id
            JOIN edges e ON e.source_title_id = t.id OR e.target_title_id = t.id
            WHERE t.enrichment_status = 'enriched'
            GROUP BY t.id
            ORDER BY
                CASE WHEN t.source = 'plex' THEN 0 ELSE 1 END,
                edge_count DESC,
                RANDOM()
            LIMIT 80
            """
        ).fetchall()
    if not prefer_plex:
        return rows
    plex_rows = [row for row in rows if row.get("source") == "plex"]
    if len(plex_rows) >= PLEX_PREFERRED_MIN:
        return plex_rows
    return rows
