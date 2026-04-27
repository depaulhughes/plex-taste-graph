from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.graph_builder import rebuild_edges


if __name__ == "__main__":
    count = rebuild_edges()
    print(f"Rebuilt {count} graph edges.")
