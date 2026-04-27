from pathlib import Path
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import get_connection, now_iso
from app.graph_builder import rebuild_edges


DEMO_TITLES = [
    {
        "title": "Videodrome",
        "year": 1983,
        "type": "movie",
        "summary": "A sleazy cable programmer discovers a pirate broadcast that mutates media, flesh, desire, and control into the same signal.",
        "genres": ["Horror", "Science Fiction"],
        "directors": ["David Cronenberg"],
        "writers": ["David Cronenberg"],
        "actors": ["James Woods", "Debbie Harry", "Sonja Smits"],
        "profile": {
            "tone_tags": ["tech paranoia", "surreal dread", "existential horror"],
            "theme_tags": ["media infection", "body horror", "identity breakdown", "surveillance and control"],
            "style_tags": ["analog nightmare", "hallucinatory conspiracy", "flesh-tech grotesque"],
            "mood_tags": ["feverish", "corrupting", "uneasy"],
            "intensity_score": 8,
            "weirdness_score": 10,
            "emotional_weight_score": 7,
            "pacing_score": 7,
            "johnny_core_score": 10,
            "ai_summary": "A central node for tech paranoia, body horror, and identity breakdown: pure signal-to-flesh Johnny-core.",
        },
    },
    {
        "title": "The Fly",
        "year": 1986,
        "type": "movie",
        "summary": "A brilliant scientist's teleportation experiment fuses him with an insect and turns romance, ambition, and embodiment into tragedy.",
        "genres": ["Horror", "Science Fiction", "Drama"],
        "directors": ["David Cronenberg"],
        "writers": ["Charles Edward Pogue", "David Cronenberg"],
        "actors": ["Jeff Goldblum", "Geena Davis", "John Getz"],
        "profile": {
            "tone_tags": ["body horror", "tragic intimacy", "existential horror"],
            "theme_tags": ["scientific hubris", "identity breakdown", "flesh betrayal", "emotional devastation"],
            "style_tags": ["practical grotesque", "romantic tragedy", "contained nightmare"],
            "mood_tags": ["devastating", "tender", "repulsive"],
            "intensity_score": 9,
            "weirdness_score": 9,
            "emotional_weight_score": 10,
            "pacing_score": 8,
            "johnny_core_score": 10,
            "ai_summary": "A body-horror tragedy where transformation becomes emotional annihilation, tying weirdness to heartbreak.",
        },
    },
    {
        "title": "eXistenZ",
        "year": 1999,
        "type": "movie",
        "summary": "A game designer and her bodyguard descend through bio-organic virtual realities where allegiance and reality refuse to stabilize.",
        "genres": ["Science Fiction", "Horror", "Thriller"],
        "directors": ["David Cronenberg"],
        "writers": ["David Cronenberg"],
        "actors": ["Jennifer Jason Leigh", "Jude Law", "Ian Holm"],
        "profile": {
            "tone_tags": ["simulation anxiety", "tech paranoia", "surreal dread"],
            "theme_tags": ["identity breakdown", "corporate manipulation", "reality instability", "body horror"],
            "style_tags": ["bio-tech noir", "puzzle-box mystery", "deadpan absurdity"],
            "mood_tags": ["slippery", "paranoid", "uncanny"],
            "intensity_score": 7,
            "weirdness_score": 10,
            "emotional_weight_score": 6,
            "pacing_score": 7,
            "johnny_core_score": 10,
            "ai_summary": "A proto-Matrix nerve cluster: simulation anxiety, corporate manipulation, and body-tech discomfort braided together.",
        },
    },
    {
        "title": "Scanners",
        "year": 1981,
        "type": "movie",
        "summary": "Telepathic outsiders are weaponized by corporate and state forces in a conspiracy of mental violence and control.",
        "genres": ["Science Fiction", "Horror", "Thriller"],
        "directors": ["David Cronenberg"],
        "writers": ["David Cronenberg"],
        "actors": ["Stephen Lack", "Jennifer O'Neill", "Michael Ironside"],
        "profile": {
            "tone_tags": ["tech paranoia", "psychic warfare", "institutional menace"],
            "theme_tags": ["surveillance and control", "corporate manipulation", "identity breakdown", "systems under pressure"],
            "style_tags": ["cold conspiracy", "explosive body horror", "clinical thriller"],
            "mood_tags": ["hostile", "controlled", "volatile"],
            "intensity_score": 8,
            "weirdness_score": 8,
            "emotional_weight_score": 6,
            "pacing_score": 7,
            "johnny_core_score": 9,
            "ai_summary": "A corporate-control thriller where minds become infrastructure and psychic difference becomes weaponized.",
        },
    },
    {
        "title": "The Brood",
        "year": 1979,
        "type": "movie",
        "summary": "A psychotherapist's experimental treatment externalizes trauma as monstrous offspring and domestic horror.",
        "genres": ["Horror"],
        "directors": ["David Cronenberg"],
        "writers": ["David Cronenberg"],
        "actors": ["Oliver Reed", "Samantha Eggar", "Art Hindle"],
        "profile": {
            "tone_tags": ["body horror", "psychological collapse", "domestic dread"],
            "theme_tags": ["trauma made flesh", "institutional decay", "family rupture", "emotional devastation"],
            "style_tags": ["clinical grotesque", "cold melodrama", "therapy nightmare"],
            "mood_tags": ["bitter", "wounded", "horrifying"],
            "intensity_score": 8,
            "weirdness_score": 9,
            "emotional_weight_score": 8,
            "pacing_score": 6,
            "johnny_core_score": 9,
            "ai_summary": "Trauma becomes literal biology, making it a key bridge between Cronenberg body horror and psychological collapse.",
        },
    },
    {
        "title": "Come and See",
        "year": 1985,
        "type": "movie",
        "summary": "A Belarusian boy joins the resistance and witnesses the spiritual and physical annihilation of war.",
        "genres": ["War", "Drama"],
        "directors": ["Elem Klimov"],
        "writers": ["Ales Adamovich", "Elem Klimov"],
        "actors": ["Aleksei Kravchenko", "Olga Mironova"],
        "profile": {
            "tone_tags": ["war trauma", "spiritual brutality", "existential horror"],
            "theme_tags": ["innocence destroyed", "systems under pressure", "moral rot", "emotional devastation"],
            "style_tags": ["hallucinatory realism", "apocalyptic soundscape", "face-forward suffering"],
            "mood_tags": ["devastating", "merciless", "haunted"],
            "intensity_score": 10,
            "weirdness_score": 7,
            "emotional_weight_score": 10,
            "pacing_score": 6,
            "johnny_core_score": 10,
            "ai_summary": "An emotionally devastating war-trauma anchor, less weird than Cronenberg but equally apocalyptic in the soul.",
        },
    },
    {
        "title": "The Ascent",
        "year": 1977,
        "type": "movie",
        "summary": "Two Soviet partisans face capture, betrayal, sacrifice, and spiritual extremity in a frozen wartime landscape.",
        "genres": ["War", "Drama"],
        "directors": ["Larisa Shepitko"],
        "writers": ["Vasil Bykov", "Yuri Klepikov", "Larisa Shepitko"],
        "actors": ["Boris Plotnikov", "Vladimir Gostyukhin"],
        "profile": {
            "tone_tags": ["war trauma", "spiritual brutality", "moral trial"],
            "theme_tags": ["sacrifice", "betrayal", "existential horror", "emotional devastation"],
            "style_tags": ["stark transcendence", "snowbound ordeal", "sacred suffering"],
            "mood_tags": ["austere", "devastating", "reverent"],
            "intensity_score": 9,
            "weirdness_score": 5,
            "emotional_weight_score": 10,
            "pacing_score": 5,
            "johnny_core_score": 9,
            "ai_summary": "A spiritual-brutality companion to Come and See, centered on moral pressure, sacrifice, and transcendence through ruin.",
        },
    },
    {
        "title": "Eraserhead",
        "year": 1977,
        "type": "movie",
        "summary": "A man drifts through industrial nightmare, paternal terror, and bodily disgust in a world of pure anxiety.",
        "genres": ["Horror", "Fantasy"],
        "directors": ["David Lynch"],
        "writers": ["David Lynch"],
        "actors": ["Jack Nance", "Charlotte Stewart"],
        "profile": {
            "tone_tags": ["surreal dread", "existential horror", "psychological collapse"],
            "theme_tags": ["body horror", "identity breakdown", "domestic terror", "industrial alienation"],
            "style_tags": ["industrial nightmare", "black-and-white fever dream", "anti-logic"],
            "mood_tags": ["oppressive", "uncanny", "nauseous"],
            "intensity_score": 7,
            "weirdness_score": 10,
            "emotional_weight_score": 8,
            "pacing_score": 4,
            "johnny_core_score": 10,
            "ai_summary": "A surreal-dread anchor where family, flesh, and industrial anxiety become one long psychic pressure chamber.",
        },
    },
    {
        "title": "The Social Network",
        "year": 2010,
        "type": "movie",
        "summary": "The founding of Facebook becomes a story of ambition, betrayal, social engineering, and corporate mythmaking.",
        "genres": ["Drama"],
        "directors": ["David Fincher"],
        "writers": ["Aaron Sorkin"],
        "actors": ["Jesse Eisenberg", "Andrew Garfield", "Justin Timberlake"],
        "profile": {
            "tone_tags": ["corporate manipulation", "moral rot", "systems under pressure"],
            "theme_tags": ["institutional decay", "identity performance", "social control", "anti-hero spiral"],
            "style_tags": ["razor-edged procedure", "cold digital propulsion", "litigation mosaic"],
            "mood_tags": ["bruising", "precise", "alienated"],
            "intensity_score": 7,
            "weirdness_score": 4,
            "emotional_weight_score": 7,
            "pacing_score": 9,
            "johnny_core_score": 9,
            "ai_summary": "A non-horror tech-paranoia node: corporate manipulation, social systems, and identity performance under pressure.",
        },
    },
    {
        "title": "Battlestar Galactica",
        "year": 2004,
        "type": "show",
        "summary": "Human survivors flee annihilation while political, religious, military, and identity crises fracture the fleet from within.",
        "genres": ["Science Fiction", "Drama"],
        "directors": ["Michael Rymer"],
        "writers": ["Ronald D. Moore"],
        "actors": ["Edward James Olmos", "Mary McDonnell", "Katee Sackhoff"],
        "profile": {
            "tone_tags": ["systems under pressure", "simulation anxiety", "existential horror"],
            "theme_tags": ["identity breakdown", "surveillance and control", "institutional decay", "war trauma"],
            "style_tags": ["military pressure cooker", "religious sci-fi dread", "serialized moral crisis"],
            "mood_tags": ["desperate", "paranoid", "apocalyptic"],
            "intensity_score": 9,
            "weirdness_score": 7,
            "emotional_weight_score": 9,
            "pacing_score": 8,
            "johnny_core_score": 10,
            "ai_summary": "A huge systems-under-pressure anchor where survival, faith, identity, and paranoia grind against each other.",
        },
    },
    {
        "title": "The Sopranos",
        "year": 1999,
        "type": "show",
        "summary": "A mob boss tries therapy while family, crime, masculinity, and self-delusion spiral into moral exhaustion.",
        "genres": ["Crime", "Drama"],
        "directors": ["David Chase"],
        "writers": ["David Chase"],
        "actors": ["James Gandolfini", "Edie Falco", "Lorraine Bracco"],
        "profile": {
            "tone_tags": ["anti-hero spiral", "moral rot", "psychological collapse"],
            "theme_tags": ["family systems", "identity performance", "institutional decay", "emotional devastation"],
            "style_tags": ["suburban noir", "therapy confession", "dream rupture"],
            "mood_tags": ["funny", "bleak", "suffocating"],
            "intensity_score": 8,
            "weirdness_score": 6,
            "emotional_weight_score": 9,
            "pacing_score": 7,
            "johnny_core_score": 10,
            "ai_summary": "A defining anti-hero spiral, where comedy, dread, family, and moral rot keep collapsing into each other.",
        },
    },
    {
        "title": "The Wire",
        "year": 2002,
        "type": "show",
        "summary": "Baltimore's institutions reveal how police, schools, politics, labor, media, and the street reproduce failure.",
        "genres": ["Crime", "Drama"],
        "directors": ["David Simon"],
        "writers": ["David Simon", "Ed Burns"],
        "actors": ["Dominic West", "Lance Reddick", "Sonja Sohn"],
        "profile": {
            "tone_tags": ["institutional decay", "systems under pressure", "moral rot"],
            "theme_tags": ["surveillance and control", "failed reform", "bureaucratic tragedy", "emotional devastation"],
            "style_tags": ["novelistic realism", "slow-burn systems map", "procedural tragedy"],
            "mood_tags": ["clear-eyed", "furious", "mourning"],
            "intensity_score": 8,
            "weirdness_score": 3,
            "emotional_weight_score": 10,
            "pacing_score": 6,
            "johnny_core_score": 10,
            "ai_summary": "The institutional-decay anchor: a systems map of moral rot where everyone is trapped inside broken machinery.",
        },
    },
    {
        "title": "Fringe",
        "year": 2008,
        "type": "show",
        "summary": "An FBI team investigates fringe science cases that unfold into parallel worlds, family wounds, and reality instability.",
        "genres": ["Science Fiction", "Mystery", "Drama"],
        "directors": ["J. J. Abrams"],
        "writers": ["J. J. Abrams", "Alex Kurtzman", "Roberto Orci"],
        "actors": ["Anna Torv", "Joshua Jackson", "John Noble"],
        "profile": {
            "tone_tags": ["weird sci-fi dread", "puzzle-box mystery", "emotional sci-fi"],
            "theme_tags": ["identity breakdown", "simulation anxiety", "family rupture", "systems under pressure"],
            "style_tags": ["case-of-the-week uncanny", "parallel-world melodrama", "fringe science grotesque"],
            "mood_tags": ["curious", "melancholy", "uncanny"],
            "intensity_score": 7,
            "weirdness_score": 8,
            "emotional_weight_score": 8,
            "pacing_score": 7,
            "johnny_core_score": 9,
            "ai_summary": "A warmer weird-sci-fi node, joining puzzle-box mystery and body-adjacent science to real emotional stakes.",
        },
    },
    {
        "title": "Quantum Leap",
        "year": 1989,
        "type": "show",
        "summary": "A scientist jumps through lives and eras, repairing intimate moral failures while losing hold of his own identity.",
        "genres": ["Science Fiction", "Drama", "Adventure"],
        "directors": ["Donald P. Bellisario"],
        "writers": ["Donald P. Bellisario"],
        "actors": ["Scott Bakula", "Dean Stockwell"],
        "profile": {
            "tone_tags": ["identity breakdown", "humanist sci-fi", "systems under pressure"],
            "theme_tags": ["moral repair", "time displacement", "emotional devastation", "selfhood erosion"],
            "style_tags": ["episodic empathy machine", "soft sci-fi melancholy", "identity-of-the-week"],
            "mood_tags": ["hopeful", "bittersweet", "yearning"],
            "intensity_score": 5,
            "weirdness_score": 6,
            "emotional_weight_score": 8,
            "pacing_score": 7,
            "johnny_core_score": 8,
            "ai_summary": "The gentler identity-breakdown anchor: sentimental, strange, and built around moral repair through borrowed lives.",
        },
    },
]


def demo_rating_key(title: str, year: int) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in title).strip("-")
    return f"DEMO:{slug}:{year}"


def upsert_demo_title(conn, item: dict, stamp: str) -> tuple[int, bool]:
    existing = conn.execute(
        """
        SELECT id
        FROM titles
        WHERE plex_rating_key = ?
           OR (lower(title) = lower(?) AND year = ?)
        ORDER BY CASE WHEN plex_rating_key = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (item["plex_rating_key"], item["title"], item["year"], item["plex_rating_key"]),
    ).fetchone()
    payload = {
        **item,
        "genres": json.dumps(item["genres"]),
        "directors": json.dumps(item["directors"]),
        "writers": json.dumps(item["writers"]),
        "actors": json.dumps(item["actors"]),
        "created_at": stamp,
        "updated_at": stamp,
    }
    if existing:
        payload["id"] = existing["id"]
        conn.execute(
            """
            UPDATE titles
            SET plex_rating_key = COALESCE(plex_rating_key, :plex_rating_key),
                title = :title,
                year = :year,
                type = :type,
                summary = :summary,
                genres = :genres,
                directors = :directors,
                writers = :writers,
                actors = :actors,
                poster_url = :poster_url,
                plex_url = :plex_url,
                source = :source,
                enrichment_status = :enrichment_status,
                is_anchor = :is_anchor,
                primary_cluster = :primary_cluster,
                last_enriched_at = :last_enriched_at,
                added_at = :added_at,
                updated_at = :updated_at
            WHERE id = :id
            """,
            payload,
        )
        return existing["id"], False

    cursor = conn.execute(
        """
        INSERT INTO titles (
            plex_rating_key, title, year, type, summary, genres, directors, writers,
            actors, poster_url, plex_url, source, enrichment_status, is_anchor,
            primary_cluster, last_enriched_at, added_at, created_at, updated_at
        )
        VALUES (
            :plex_rating_key, :title, :year, :type, :summary, :genres, :directors, :writers,
            :actors, :poster_url, :plex_url, :source, :enrichment_status, :is_anchor,
            :primary_cluster, :last_enriched_at, :added_at, :created_at, :updated_at
        )
        """,
        payload,
    )
    return int(cursor.lastrowid), True


def upsert_profile(conn, title_id: int, profile: dict, stamp: str) -> bool:
    existing = conn.execute(
        "SELECT id FROM taste_profiles WHERE title_id = ?",
        (title_id,),
    ).fetchone()
    payload = {
        "title_id": title_id,
        "tone_tags": json.dumps(profile["tone_tags"]),
        "theme_tags": json.dumps(profile["theme_tags"]),
        "style_tags": json.dumps(profile["style_tags"]),
        "mood_tags": json.dumps(profile["mood_tags"]),
        "intensity_score": profile["intensity_score"],
        "weirdness_score": profile["weirdness_score"],
        "emotional_weight_score": profile["emotional_weight_score"],
        "pacing_score": profile["pacing_score"],
        "johnny_core_score": profile["johnny_core_score"],
        "ai_summary": profile["ai_summary"],
        "created_at": stamp,
        "updated_at": stamp,
    }
    conn.execute(
        """
        INSERT INTO taste_profiles (
            title_id, tone_tags, theme_tags, style_tags, mood_tags, intensity_score,
            weirdness_score, emotional_weight_score, pacing_score, johnny_core_score,
            ai_summary, created_at, updated_at
        )
        VALUES (
            :title_id, :tone_tags, :theme_tags, :style_tags, :mood_tags, :intensity_score,
            :weirdness_score, :emotional_weight_score, :pacing_score, :johnny_core_score,
            :ai_summary, :created_at, :updated_at
        )
        ON CONFLICT(title_id) DO UPDATE SET
            tone_tags = excluded.tone_tags,
            theme_tags = excluded.theme_tags,
            style_tags = excluded.style_tags,
            mood_tags = excluded.mood_tags,
            intensity_score = excluded.intensity_score,
            weirdness_score = excluded.weirdness_score,
            emotional_weight_score = excluded.emotional_weight_score,
            pacing_score = excluded.pacing_score,
            johnny_core_score = excluded.johnny_core_score,
            ai_summary = excluded.ai_summary,
            updated_at = excluded.updated_at
        """,
        payload,
    )
    return existing is None


def seed_demo() -> tuple[int, int, int, int, int]:
    stamp = now_iso()
    titles_inserted = 0
    titles_updated = 0
    profiles_inserted = 0
    profiles_updated = 0
    with get_connection() as conn:
        demo_ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM titles WHERE plex_rating_key LIKE 'DEMO:%'"
            ).fetchall()
        ]
        if demo_ids:
            placeholders = ",".join("?" for _ in demo_ids)
            conn.execute(
                f"DELETE FROM edges WHERE source_title_id IN ({placeholders}) OR target_title_id IN ({placeholders})",
                demo_ids + demo_ids,
            )

        for item in DEMO_TITLES:
            title_payload = {
                **item,
                "plex_rating_key": demo_rating_key(item["title"], item["year"]),
                "poster_url": None,
                "plex_url": None,
                "source": "demo",
                "enrichment_status": "enriched",
                "is_anchor": 1,
                "primary_cluster": None,
                "last_enriched_at": stamp,
                "added_at": stamp,
            }
            profile = title_payload.pop("profile")
            title_payload["primary_cluster"] = profile["theme_tags"][0] if profile["theme_tags"] else profile["tone_tags"][0]
            title_id, inserted = upsert_demo_title(conn, title_payload, stamp)
            if inserted:
                titles_inserted += 1
            else:
                titles_updated += 1
            if upsert_profile(conn, title_id, profile, stamp):
                profiles_inserted += 1
            else:
                profiles_updated += 1

    edges_created = rebuild_edges()
    return titles_inserted, titles_updated, profiles_inserted, profiles_updated, edges_created


if __name__ == "__main__":
    ti, tu, pi, pu, edges = seed_demo()
    print("Seeded Taste Graph demo mode.")
    print(f"Titles inserted/updated: {ti}/{tu}")
    print(f"Profiles inserted/updated: {pi}/{pu}")
    print(f"Edges created: {edges}")
