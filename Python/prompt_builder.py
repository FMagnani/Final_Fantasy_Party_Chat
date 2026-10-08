import json
import os

from utils import diff_json

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "..", "Lua", "FF_Chat")
CHARACTERS_OLD = os.path.join(STATE_DIR, "ff1_characters_old.json")
CHARACTERS_NEW = os.path.join(STATE_DIR, "ff1_characters_new.json")
GAME_STATE_OLD = os.path.join(STATE_DIR, "ff1_game_state_old.json")
GAME_STATE_NEW = os.path.join(STATE_DIR, "ff1_game_state_new.json")

IGNORED_DIFF_PATHS = ("frame",)  # always changes, carries no information


def read_json(path):
    """Returns the parsed json, or None if the file is missing / unreadable (e.g. mid-write)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class Baseline:
    """State snapshot taken when the previous prompt was built.

    Each prompt reports the diff between this snapshot and the current state, then the
    current state becomes the new snapshot. Changes that happened and reverted in between
    cancel out.
    """

    def __init__(self, fallback_old_path):
        self.fallback_old_path = fallback_old_path   # used only before the first snapshot
        self.snapshot = None

    def diff_and_advance(self, current):
        if current is None:
            return None                              # unreadable now: keep the old snapshot
        start = self.snapshot
        if start is None:
            start = read_json(self.fallback_old_path)
        self.snapshot = current
        if start is None:
            return None
        return diff_json(start, current, ignore=IGNORED_DIFF_PATHS)


characters_baseline = Baseline(CHARACTERS_OLD)
game_state_baseline = Baseline(GAME_STATE_OLD)


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False) if data is not None else "unavailable"


def build_prompt(player_name, player_text, other_names):
    """Prompt for one turn: situation, what changed, and what the player just said."""
    characters = read_json(CHARACTERS_NEW)
    game_state = read_json(GAME_STATE_NEW)
    characters_diff = characters_baseline.diff_and_advance(characters)
    game_state_diff = game_state_baseline.diff_and_advance(game_state)

    return (
        "This is the current situation.\n"
        f"Characters:\n{dump(characters)}\n\n"
        f"Game state:\n{dump(game_state)}\n\n"
        "These are the recent events (what changed since the previous message of the "
        "player; an empty object means nothing changed).\n"
        f"Characters changes:\n{dump(characters_diff)}\n\n"
        f"Game state changes:\n{dump(game_state_diff)}\n\n"
        f"{player_name} (a Light Warrior, controlled by the player) just said:\n"
        f"{json.dumps(player_text, ensure_ascii=False)}\n\n"
        f"Generate the replies of the other characters ({', '.join(other_names)}) to this "
        f"message, as a brief conversation. {player_name} must NOT speak in your output."
    )
