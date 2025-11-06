import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def load_config(path: Path | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)
