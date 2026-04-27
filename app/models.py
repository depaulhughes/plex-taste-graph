import json
from dataclasses import dataclass
from typing import Any


ANCHOR_TITLES = {
    "videodrome",
    "the fly",
    "existenz",
    "scanners",
    "the brood",
    "come and see",
    "the ascent",
    "eraserhead",
    "the social network",
    "battlestar galactica",
    "the sopranos",
    "the wire",
    "fringe",
    "quantum leap",
}

SEED_CLUSTERS = [
    "Tech paranoia",
    "Body horror",
    "Weird sci-fi dread",
    "Systems under pressure",
    "Moral rot / institutional decay",
    "Anti-hero spiral",
    "War trauma / spiritual brutality",
    "Psychological collapse",
    "Puzzle-box mystery",
    "Proto-Matrix / simulation anxiety",
    "Corporate manipulation",
    "Identity breakdown",
    "Surveillance and control",
    "Existential horror",
    "Emotionally devastating cinema",
    "Johnny-core",
]


@dataclass
class TitleProfile:
    title: dict[str, Any]
    profile: dict[str, Any]

    @property
    def title_id(self) -> int:
        return int(self.title["id"])

    @property
    def all_tags(self) -> list[str]:
        tags: list[str] = []
        for key in ("tone_tags", "theme_tags", "style_tags", "mood_tags"):
            value = self.profile.get(key, "[]")
            if isinstance(value, str):
                tags.extend(json.loads(value or "[]"))
            else:
                tags.extend(value or [])
        return [normalise_tag(tag) for tag in tags if tag]

    @property
    def is_anchor(self) -> bool:
        return normalise_title(self.title.get("title", "")) in ANCHOR_TITLES


def normalise_title(value: str) -> str:
    return value.strip().lower()


def normalise_tag(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in str(value).split(",") if item.strip()]
