from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.openai_client import OpenAITasteClient


def main() -> None:
    settings = get_settings()
    print("OPENAI_API_KEY present:", bool(settings.openai_api_key))
    print("OPENAI_MODEL:", settings.openai_model or "gpt-4o-mini")
    if not settings.openai_api_key:
        print("Missing OPENAI_API_KEY.")
        return
    try:
        client = OpenAITasteClient()
        response = client.explain_graph_answer(
            "Closest to The Outpost",
            '{"question":"Closest to The Outpost","selected_title_profile":{"title":"The Outpost"},"relevant_edges":[]}',
            timeout_seconds=4.0,
        )
        print("OpenAI check succeeded.")
        print("Title:", response.get("title"))
        print("Explanation:", response.get("explanation"))
    except Exception as exc:
        print("OpenAI check failed.")
        print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
