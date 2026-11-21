"""Storage for use cases and routing settings. JSON file for local dev.

ponytail: whole-file read/write, single process. Swap these four functions for
DynamoDB when merged into Rekon-AI; the router does not change.
"""

import json
import os
from pathlib import Path

from .seed import SEED

_PATH = Path(os.getenv("AI_CALLING_DATA", Path(__file__).parent / "data.json"))


def _load() -> dict:
    if not _PATH.exists():
        _save(SEED)
    return json.loads(_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_usecases() -> list[dict]:
    return sorted(_load()["usecases"], key=lambda u: u["priority"])


def get_usecase(uid: str) -> dict | None:
    return next((u for u in _load()["usecases"] if u["usecaseId"] == uid), None)


def put_usecase(uc: dict) -> dict:
    data = _load()
    data["usecases"] = [u for u in data["usecases"] if u["usecaseId"] != uc["usecaseId"]] + [uc]
    _save(data)
    return uc


def delete_usecase(uid: str) -> bool:
    data = _load()
    kept = [u for u in data["usecases"] if u["usecaseId"] != uid]
    _save({**data, "usecases": kept})
    return len(kept) != len(data["usecases"])


def reorder(ids: list[str]) -> list[dict]:
    """Set priority 1..n in the given order (drag-to-reorder in the UI)."""
    data = _load()
    pos = {uid: i + 1 for i, uid in enumerate(ids)}
    for u in data["usecases"]:
        u["priority"] = pos.get(u["usecaseId"], len(ids) + u["priority"])
    _save(data)
    return list_usecases()


def get_settings() -> dict:
    return _load()["settings"]


def put_settings(s: dict) -> dict:
    data = _load()
    _save({**data, "settings": s})
    return s


def list_conversations() -> list[dict]:
    return _load()["conversations"]  # dummy seed only, no real user data
