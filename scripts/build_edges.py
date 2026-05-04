from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.graph_builder import rebuild_edges_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild Taste Graph connections.")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate and summarize edges without writing to SQLite.")
    parser.add_argument("--verbose", action="store_true", help="Print more detailed progress updates.")
    parser.add_argument("--max-candidates", type=int, default=150, help="Maximum candidate pool per title before detailed scoring.")
    parser.add_argument("--top-edges-per-title", type=int, default=8, help="Maximum kept edges per title.")
    parser.add_argument("--min-score", type=float, default=0.60, help="Minimum quick candidate score before detailed evaluation.")
    parser.add_argument("--progress-every", type=int, default=50, help="Progress update frequency in source titles.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    start = time.perf_counter()
    last_stage = None

    def progress(update: dict[str, object]) -> None:
        nonlocal last_stage
        stage = str(update.get("stage") or "build")
        processed = int(update.get("processed") or 0)
        total = int(update.get("total") or 0)
        elapsed = time.perf_counter() - start
        prefix = f"[{stage}] {processed}/{total}" if total else f"[{stage}]"
        line = (
            f"{prefix} · {elapsed:.1f}s · pairs {int(update.get('candidate_pairs_evaluated') or 0)} "
            f"· accepted strong {int(update.get('strong_edges_accepted') or 0)} "
            f"soft {int(update.get('soft_edges_accepted') or 0)} "
            f"bridge {int(update.get('bridge_edges_accepted') or 0)} "
            f"· rejected broad {int(update.get('rejected_broad_only_pairs') or 0)} "
            f"· rejected incompatible {int(update.get('rejected_incompatible_pairs') or 0)}"
        )
        if args.verbose:
            line += f" · rejected generic {int(update.get('rejected_generic_pairs') or 0)}"
        print(line, flush=True)
        last_stage = stage

    print("Loading enriched titles and rebuilding graph connections...", flush=True)
    summary = rebuild_edges_summary(
        dry_run=args.dry_run,
        max_candidates=args.max_candidates,
        top_edges_per_title=args.top_edges_per_title,
        min_score=args.min_score,
        progress_every=args.progress_every,
        progress_callback=progress,
    )
    elapsed = time.perf_counter() - start
    mode = "Dry run complete" if args.dry_run else "Edge rebuild complete"
    print(mode, flush=True)
    print(f"- Total titles: {summary['total_titles']}", flush=True)
    print(f"- Candidate pairs evaluated: {summary['candidate_pairs_evaluated']}", flush=True)
    print(f"- Total edges written: {summary['total_edges_written']}", flush=True)
    print(f"- Strong / soft / bridge: {summary['strong_edges']} / {summary['soft_edges']} / {summary['bridge_edges']}", flush=True)
    print(f"- Titles with zero edges: {summary['titles_with_zero_edges']}", flush=True)
    print(f"- Rejected broad-only pairs: {summary['rejected_broad_only_pairs']}", flush=True)
    print(f"- Rejected incompatible pairs: {summary['rejected_incompatible_pairs']}", flush=True)
    print(f"- Rejected generic explanations: {summary['rejected_generic_pairs']}", flush=True)
    print(f"- Elapsed seconds: {elapsed:.2f}", flush=True)


if __name__ == "__main__":
    main()
