# Taste Graph v1

Taste Graph v1 is a visual discovery app for a Plex movie/show library. Every title becomes a node, and edges connect titles that share meaningful taste traits: themes, tone, mood, style, intensity, weirdness, emotional weight, and Johnny-core proximity.

It is meant to feel like a dark, cinematic map of taste, not an admin dashboard.

## What This Is Not

This is not Plex monitoring, server health, diagnostics, streaming observability, transcoding analysis, bandwidth tracking, NAS status, or a Plex Assistant clone. Ask Taste Graph is only for visual discovery and taste-based recommendations.

## Stack

- Python
- FastAPI
- Jinja templates
- SQLite
- Vanilla JavaScript
- Cytoscape.js
- Plex API via `plexapi`
- OpenAI API enrichment and Q&A

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Use Python 3.11 for local development. The repo includes `.python-version` and the Docker image already runs on Python 3.11.

Edit `.env` with your Plex and OpenAI credentials.

## Environment

```bash
PLEX_BASE_URL=http://127.0.0.1:32400
PLEX_TOKEN=your-plex-token
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-4o-mini
DATABASE_URL=sqlite:///data/taste_graph.sqlite
APP_NAME=Taste Graph v1
```

## Initialize Database

```bash
python3.11 scripts/init_db.py
```

## Demo Mode

Demo mode seeds the database with anchor titles and hand-built taste profiles so you can validate the graph before connecting Plex or OpenAI.

```bash
python3 scripts/seed_demo.py
```

Seed titles include `Videodrome`, `The Fly`, `eXistenZ`, `Scanners`, `The Brood`, `Come and See`, `The Ascent`, `Eraserhead`, `The Social Network`, `Battlestar Galactica`, `The Sopranos`, `The Wire`, `Fringe`, and `Quantum Leap`.

The script upserts by title/year, refreshes demo profiles, and rebuilds graph edges using the same v1 taste engine used by enriched Plex titles.

Recommended first run:

```bash
python3 scripts/init_db.py
python3 scripts/seed_demo.py
python3 scripts/build_edges.py
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Sync Plex

Movie libraries are supported first. Show support is a future v1.x path.

```bash
python scripts/sync_plex.py
```

The sync stores title, year, summary, genres, directors, writers, actors, rating key, poster placeholder, and Plex web link. It avoids duplicates by `plex_rating_key`.

Controlled import examples:

```bash
python3 scripts/sync_plex.py --library "Movies" --limit 50 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 50
python3 scripts/sync_plex.py --library "Movies" --limit 50 --include-existing false
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 0 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 500 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 1000 --dry-run
```

For large libraries, import in batches with `--offset` so each run selects the next slice instead of restarting from the top of Plex.

## Enrich Titles

```bash
python3.11 scripts/enrich_titles.py
```

This finds titles without taste profiles, sends their metadata to OpenAI, stores strict JSON taste profiles, and rebuilds graph edges when complete.

Enrichment is schema-validated and retry-safe:

- first attempt uses structured output parsing
- malformed responses are retried once with a stricter JSON-only prompt
- unrecoverable malformed responses are logged to `data/failed_enrichment_responses.log`
- failed titles are marked `enrichment_status='failed'` and the batch keeps going

To rebuild only graph edges:

```bash
python scripts/build_edges.py
```

Controlled enrichment examples:

```bash
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending --dry-run
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending
python3 scripts/enrich_titles.py --title "The Fly" --source all
python3 scripts/enrich_titles.py --source plex --limit 25 --retry-failed
```

Quick health check:

```bash
python3 scripts/health_check.py
python3 scripts/enrich_titles.py --source plex --limit 25 --retry-failed
python3 scripts/build_edges.py
```

## Real Plex Test Workflow

Start small. Import a small Plex slice, inspect it in `/library`, enrich only a limited batch, validate the taste tags and graph shape, then scale up.

Recommended flow:

```bash
python3 scripts/sync_plex.py --library "Movies" --limit 50 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 50
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending --dry-run
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending
python3 scripts/build_edges.py
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Unenriched Plex titles appear in the library as pending enrichment. Enriched titles become full graph nodes with taste scores, clusters, tags, and similarity edges.

## Batch Scaling Guidance

Do not sync or enrich everything on the first pass. Taste Graph is most useful when you can inspect enrichment quality and graph density as it grows.

Recommended next batch:

```bash
python3 scripts/sync_plex.py --library "Movies" --limit 100 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 100
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending --dry-run
python3 scripts/enrich_titles.py --source plex --limit 25 --only-pending
python3 scripts/build_edges.py
```

Then open `/graph`. By default the graph shows enriched titles only; use “Show pending titles” when you want to see imported Plex titles waiting for OpenAI enrichment.

## Scaling Up Your Catalog

Once the demo graph and first real batch look good, scale in visible steps:

```bash
python3 scripts/sync_plex.py --library "Movies" --limit 200 --dry-run
python3 scripts/sync_plex.py --library "Movies" --limit 200
python3 scripts/enrich_titles.py --source plex --limit 50 --only-pending
python3 scripts/build_edges.py
```

Start with around 50 enriched real titles, inspect clusters and title pages, then repeat enrichment in batches. A full catalog is fine once the tags, clusters, and recommendation hooks feel consistently useful.

For a larger Plex library, page through it in chunks:

```bash
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 0
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 500
python3 scripts/sync_plex.py --library "Movies" --limit 500 --offset 1000
```

Each run reports:

- total items found in the Plex library
- offset
- limit
- number selected for the batch
- number already existing
- number new

## Run The App

```bash
uvicorn app.main:app --reload
```

Open:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/graph`
- `http://127.0.0.1:8000/library`
- `http://127.0.0.1:8000/ask`

If `uvicorn` is only available as a module:

```bash
python -m uvicorn app.main:app --reload
```

Local development with an explicit host and port:

```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## Mobile Notes

Taste Graph is designed to collapse into a stacked mobile layout:

1. Browse/search titles
2. Graph
3. Taste context
4. Suggested asks and Ask results

On phones and tablets, the graph keeps a bounded viewport height for touch pan/zoom, title rows stay tappable, and Ask results render directly below the input instead of opening a separate page.

## Docker

There is a minimal Dockerfile for later iteration:

```bash
docker build -t taste-graph-v1 .
docker run --env-file .env -p 8000:8000 taste-graph-v1
```

For Docker or NAS use:

- run the app on `0.0.0.0`, not `127.0.0.1`
- mount `/app/data` so the SQLite database persists
- preserve your `.env` values for Plex and OpenAI
- expose the port you want to reach from your LAN or reverse proxy

Example:

```bash
docker run \
  --env-file .env \
  -p 8000:8000 \
  -v /path/on/host/taste-graph-data:/app/data \
  taste-graph-v1 \
  python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The frontend uses relative paths like `/api/graph` and `/api/ask`, so it works cleanly behind Docker port mappings and typical reverse proxies without hardcoded localhost URLs.

## JSON Endpoints

- `GET /api/graph`
- `GET /api/titles`
- `GET /api/title/{id}`
- `POST /api/ask`

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What should I watch next if I want weird sci-fi dread but not too slow?"}'
```

## Taste Model

Seed clusters include:

- Tech paranoia
- Body horror
- Weird sci-fi dread
- Systems under pressure
- Moral rot / institutional decay
- Anti-hero spiral
- War trauma / spiritual brutality
- Psychological collapse
- Puzzle-box mystery
- Proto-Matrix / simulation anxiety
- Corporate manipulation
- Identity breakdown
- Surveillance and control
- Existential horror
- Emotionally devastating cinema
- Johnny-core

Anchor titles include `Videodrome`, `The Fly`, `eXistenZ`, `Scanners`, `The Brood`, `Come and See`, `The Ascent`, `Eraserhead`, `The Social Network`, `Battlestar Galactica`, `The Sopranos`, `The Wire`, `Fringe`, and `Quantum Leap`.

## Graph Edges

Taste Graph uses two edge types:

- `strong` edges are high-confidence taste relationships. They come from shared tags plus close score profiles and are the main evidence for similarity.
- `soft` edges are weaker thematic bridges. They are added only for enriched titles that would otherwise be isolated, so those titles can sit near the most relevant neighborhood without pretending the match is strong.

Soft edges are dashed and lower-opacity in the graph. They can inform Ask Taste Graph as secondary evidence, but strong edges rank higher.

## Future Ideas

- Watchlist import
- Letterboxd import
- Manual ratings
- Mood mode
- Random walk through taste graph
- Plex playback launch links
- Show support
- Taste drift over time
- Anti-recommendations for things I probably will not like
