"""Persist the selected Hex typing profile and its fitted statistics."""

import json
import os
from pathlib import Path
import tempfile

from biocadence import DEFAULT


PROFILES = {"thoughts": "Typing from thoughts", "copy": "Copy typing"}


def profile_path():
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state / "hex/typing/profiles.json"


def load_profiles():
    try:
        state = json.loads(profile_path().read_text())
    except FileNotFoundError:
        return {"selected": "thoughts", "profiles": {}}
    if state["selected"] not in PROFILES or not isinstance(state["profiles"], dict):
        raise ValueError("Invalid Hex typing profiles")
    return state


def save_profiles(state):
    path = profile_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".profiles-", delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(state, file, ensure_ascii=False, allow_nan=False)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def selected_model():
    state = load_profiles()
    return state["profiles"].get(state["selected"], DEFAULT)
