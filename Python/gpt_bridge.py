import glob
import json
import os
import re

from openai import OpenAI

from prompt_builder import build_prompt

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_KEY_PATH = os.path.join(BASE_DIR, "OPENAIKEY.txt")
CONTEXT_DIR = os.path.join(BASE_DIR, "Context")
CHAT_HISTORY_PATH = os.path.join(BASE_DIR, "chat_history.json")

MODEL = "gpt-5.4"
RECENT_CHAT_ON_NEW_CONVERSATION = 6   # last messages resent if the conversation is restarted


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def read_json_list(path):
    """A json list from disk; [] if the file is missing, empty or unreadable."""
    try:
        data = json.loads(read_text(path))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_json_atomic(path, data):
    # Temp file + swap, so a reader never sees a half-written file
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp_path, path)


def load_init_context():
    """Everything under Context/ (setting, personalities...), sent once per conversation.

    Empty files are skipped.
    """
    parts = []
    for path in sorted(glob.glob(os.path.join(CONTEXT_DIR, "**", "*.txt"), recursive=True)):
        text = read_text(path).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_response(text):
    """Extracts the list of {"name", "msg"} from the model output.

    Tolerates markdown fences or text around the json. Returns None if nothing valid is found.
    """
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    chat = [{"name": str(m["name"]).strip(), "msg": str(m["msg"]).strip()}
            for m in data
            if isinstance(m, dict) and m.get("name") and m.get("msg")]
    return chat or None


class GptBridge:
    def __init__(self):
        self.client = OpenAI(api_key=read_text(API_KEY_PATH).strip())
        self.previous_response_id = None    # None = a new conversation has to be started

    def respond(self, player_name, player_text, other_names, recent_chat=()):
        """Sends the player's message, returns the replies as a list of {"name", "msg"}.

        Raises RuntimeError if the call fails or the output cannot be parsed.
        recent_chat: past messages, only used when a new conversation is started.
        """
        prompt = build_prompt(player_name, player_text, other_names)
        kwargs = {"model": MODEL}
        if self.previous_response_id is None:
            # New conversation: the model needs the setting/personalities first
            parts = [load_init_context()]
            recent = list(recent_chat)[-RECENT_CHAT_ON_NEW_CONVERSATION:]
            if recent:
                parts.append("This is the most recent part of the conversation so far:\n"
                             + json.dumps(recent, indent=2, ensure_ascii=False))
            parts.append(prompt)
            prompt = "\n\n".join(parts)
        else:
            kwargs["previous_response_id"] = self.previous_response_id

        try:
            output = self.client.responses.create(input=prompt, **kwargs)
        except Exception as e:
            raise RuntimeError(f"OpenAI call failed: {e}") from e

        chat = parse_response(output.output_text)
        if chat is None:
            raise RuntimeError(f"Could not parse the response: {output.output_text[:200]}")

        # Whatever the model may do, the player's character does not speak twice
        chat = [m for m in chat if m["name"].lower() != player_name.lower()]
        if not chat:
            raise RuntimeError("The response contained no message from the other characters")

        self.previous_response_id = output.id
        return chat
