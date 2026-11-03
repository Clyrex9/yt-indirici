"""Persists user preferences to a small JSON file."""
import json
import locale
import os

CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"), "YT-Indirici")
CONFIG_FILE = os.path.join(CONFIG_DIR, "settings.json")


def _default_language():
    try:
        name = (locale.getlocale()[0] or "").lower()
    except ValueError:
        name = ""
    return "tr" if name.startswith(("tr", "turkish")) else "en"


DEFAULTS = {
    "language": _default_language(),
    "theme": None,  # None = follow the system
    "quality": "1080",
    "folder": os.path.join(os.path.expanduser("~"), "Downloads", "YT İndirici"),
    "playlist": True,
    "mp4": True,
    "subtitles": False,
    "embed": True,
    "geometry": "780x640",
}


def load():
    data = dict(DEFAULTS)
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            saved = json.load(f)
        data.update({k: v for k, v in saved.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save(data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
