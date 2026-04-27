from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import init_db, get_db_path


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {get_db_path()}")
