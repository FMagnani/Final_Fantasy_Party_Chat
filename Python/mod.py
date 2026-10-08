import json
import os
import queue
import threading
import tkinter as tk
from collections import deque
from PIL import Image, ImageTk

from gpt_bridge import GptBridge, read_json_list, write_json_atomic

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHAT_HISTORY_PATH = os.path.join(BASE_DIR, "chat_history.json")
CHARACTERS_FILE_PATH = os.path.join(BASE_DIR, "../Lua/FF_Chat/ff1_characters_new.json")
IMAGE_FOLDER_PATH = os.path.join(BASE_DIR, "Sprites")

PLAYER_SLOT = 1          # party slot (1-4) the typed messages are attributed to at startup;
                         # it can be changed from the menu next to the input box
TYPE_MS = 60             # delay between two typed characters
PAUSE_MS = 1000          # pause after a message is complete, before the next one starts
SPRITE_SIZE = 64
LEFT_COL_WIDTH = 110     # sprite + name column

BG = "#1e1e2e"
BUBBLE_BG = "#313244"
BUBBLE_FG = "#cdd6f4"
MUTED_FG = "#6c7086"
ERROR_FG = "#f38ba8"
# Each distinct name gets one of these colours (stable for the whole session)
NAME_COLORS = ["#89b4fa", "#a6e3a1", "#f9e2af", "#f5c2e7", "#fab387", "#94e2d5"]


class CharacterLookup:
    """Reads the party from the characters json (names are unique).

    The file is re-read only when it changes on disk, so a class change in game
    (e.g. a promotion) is picked up by the next message.
    """

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.party = []        # list of {"slot", "name", "class", ...}

    def _refresh(self):
        try:
            mtime = os.path.getmtime(self.path)
            if mtime != self.mtime:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.party = json.load(f).get("party", [])
                self.mtime = mtime
        except (OSError, json.JSONDecodeError, AttributeError):
            pass               # file missing / mid-write: keep the last known party

    def class_of(self, name):
        self._refresh()
        for c in self.party:
            if str(c.get("name", "")).lower() == name.lower():
                return c.get("class", "")
        return ""

    def name_of_slot(self, slot):
        self._refresh()
        for c in self.party:
            if c.get("slot") == slot:
                return str(c.get("name", ""))
        return ""

    def names(self):
        self._refresh()
        return [str(c.get("name", "")) for c in self.party]


class ChatWindow:
    def __init__(self, root):
        self.root = root
        self.sprite_cache = {}
        self.characters = CharacterLookup(CHARACTERS_FILE_PATH)
        self.name_colors = {}
        self.history = read_json_list(CHAT_HISTORY_PATH)   # [{"name", "msg"}, ...]
        self.queue = deque()       # messages waiting for their typewriter turn
        self.current = None        # (bubble_label, full_text, chars_typed)
        self.replies = queue.Queue()   # results coming back from the GPT thread
        self.busy = False          # True while waiting for GPT

        try:
            self.bridge = GptBridge()
            bridge_error = ""
        except Exception as e:
            self.bridge = None
            bridge_error = f"GPT unavailable: {e}"

        root.title("FF1 Party Chat")
        root.configure(bg=BG)
        root.geometry("560x600")
        root.minsize(380, 300)

        tk.Label(root, text="Party Chat", font=("Helvetica", 16, "bold"),
                 fg=BUBBLE_FG, bg=BG, pady=10).pack()
        self.status = tk.Label(root, text=bridge_error, font=("Helvetica", 9, "italic"),
                               fg=ERROR_FG, bg=BG)
        self.status.pack()

        # Input bar (packed before the log so it stays at the bottom)
        bar = tk.Frame(root, bg=BG)
        bar.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        tk.Label(bar, text="Speak as slot", fg=MUTED_FG, bg=BG).pack(side="left")
        self.slot_var = tk.IntVar(value=PLAYER_SLOT)
        tk.OptionMenu(bar, self.slot_var, 1, 2, 3, 4).pack(side="left", padx=(4, 8))
        self.entry = tk.Entry(bar, font=("Helvetica", 12), bg=BUBBLE_BG, fg=BUBBLE_FG,
                              insertbackground=BUBBLE_FG, relief="flat")
        self.entry.pack(side="left", expand=True, fill="x", ipady=6)
        self.entry.bind("<Return>", lambda _e: self.send())
        tk.Button(bar, text="Send", command=self.send).pack(side="left", padx=(8, 0))

        # Scrollable log: canvas + inner frame
        container = tk.Frame(root, bg=BG)
        container.pack(expand=True, fill="both", padx=(12, 4), pady=(4, 8))
        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        scroll = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", expand=True, fill="both")

        self.log = tk.Frame(self.canvas, bg=BG)
        self.log_window = self.canvas.create_window((0, 0), window=self.log, anchor="nw")
        self.log.bind("<Configure>", self._on_log_resize)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        root.bind_all("<MouseWheel>", self._on_wheel)

        # Past conversation is shown at once, without typewriter
        for m in self.history:
            self.queue.append((m["name"], m["msg"], False))

        self.entry.focus_set()
        self.pump()
        self.check_replies()

    # ---------- user input ----------
    def send(self):
        text = self.entry.get().strip()
        if not text:
            return
        if self.bridge is None:
            return
        if self.busy:
            self.status.config(text="Wait for the replies before writing again", fg=MUTED_FG)
            return

        slot = self.slot_var.get()
        name = self.characters.name_of_slot(slot)
        if not name:
            self.status.config(text=f"No character found in slot {slot} "
                                    f"(is the characters json available?)", fg=ERROR_FG)
            return
        others = [n for n in self.characters.names() if n and n.lower() != name.lower()]

        recent = list(self.history)          # before this message: for a restarted conversation
        self.entry.delete(0, "end")
        self.add_message(name, text, animate=False)
        self.busy = True
        self.status.config(text="...", fg=MUTED_FG)

        threading.Thread(target=self._ask_gpt, args=(name, text, others, recent),
                         daemon=True).start()

    def _ask_gpt(self, name, text, others, recent):
        """Runs in a worker thread: never touches tkinter, only posts to the queue."""
        try:
            self.replies.put(("ok", self.bridge.respond(name, text, others, recent)))
        except Exception as e:
            self.replies.put(("error", str(e)))

    def check_replies(self):
        try:
            kind, payload = self.replies.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            if kind == "ok":
                self.status.config(text="")
                for m in payload:
                    self.add_message(m["name"], m["msg"], animate=True)
            else:
                self.status.config(text=payload, fg=ERROR_FG)
        self.root.after(200, self.check_replies)

    def add_message(self, name, text, animate):
        """Queues a message for display and saves it in the history file."""
        self.queue.append((name, text, animate))
        self.history.append({"name": name, "msg": text})
        write_json_atomic(CHAT_HISTORY_PATH, self.history)

    # ---------- scrolling / layout ----------
    def _on_log_resize(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_resize(self, event):
        self.canvas.itemconfigure(self.log_window, width=event.width)
        wrap = max(event.width - LEFT_COL_WIDTH - 50, 60)
        for child in self.log.winfo_children():
            child.bubble.config(wraplength=wrap)
        self.wraplength = wrap

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _at_bottom(self):
        return self.canvas.yview()[1] >= 0.99

    def _scroll_to_bottom(self):
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    # ---------- widgets ----------
    wraplength = 380

    def _get_sprite(self, char_class):
        if char_class not in self.sprite_cache:
            tk_img = None
            path = os.path.join(IMAGE_FOLDER_PATH, f"{char_class}.jpg")
            if char_class and os.path.exists(path):
                try:
                    pil = Image.open(path).convert("RGB")
                    # Sprites are tiny pixel art: scale by a whole factor, keep the aspect
                    # ratio, no smoothing
                    w, h = pil.size
                    scale = max(1, min(SPRITE_SIZE // w, SPRITE_SIZE // h))
                    pil = pil.resize((w * scale, h * scale), Image.Resampling.NEAREST)
                    tk_img = ImageTk.PhotoImage(pil)
                except Exception:
                    tk_img = None
            self.sprite_cache[char_class] = tk_img
        return self.sprite_cache[char_class]

    def _name_color(self, name):
        if name not in self.name_colors:
            self.name_colors[name] = NAME_COLORS[len(self.name_colors) % len(NAME_COLORS)]
        return self.name_colors[name]

    def _add_row(self, name, char_class):
        """Creates an empty row (sprite + name + empty bubble) at the bottom of the log."""
        row = tk.Frame(self.log, bg=BG)
        row.pack(fill="x", pady=6)

        # Column 0 = sprite + name (fixed width), column 1 = bubble
        row.grid_columnconfigure(0, minsize=LEFT_COL_WIDTH)
        left = tk.Frame(row, bg=BG)
        left.grid(row=0, column=0, sticky="n")

        # Fixed-size box so every sprite (or the missing-sprite placeholder) takes the same space
        box = tk.Frame(left, bg=BG, width=SPRITE_SIZE, height=SPRITE_SIZE)
        box.pack()
        box.pack_propagate(False)
        sprite = self._get_sprite(char_class)
        if sprite:
            img = tk.Label(box, image=sprite, bg=BG, bd=0)
            img.image = sprite  # keep a reference
        else:
            img = tk.Label(box, text=f"[{char_class or '?'}]", fg=MUTED_FG, bg=BUBBLE_BG,
                           font=("Helvetica", 8, "italic"), wraplength=SPRITE_SIZE - 4)
        img.pack(expand=True, fill="both")

        tk.Label(left, text=name, font=("Consolas", 11, "bold"),
                 fg=self._name_color(name), bg=BG,
                 wraplength=LEFT_COL_WIDTH - 4).pack(pady=(2, 0))

        bubble = tk.Label(row, text="", font=("Helvetica", 12), fg=BUBBLE_FG, bg=BUBBLE_BG,
                          justify="left", anchor="w", padx=12, pady=8,
                          wraplength=self.wraplength)
        bubble.grid(row=0, column=1, sticky="nw", padx=(8, 0))
        row.bubble = bubble
        return bubble

    # ---------- typewriter ----------
    def pump(self):
        """Advances the typewriter by one character per tick."""
        delay = TYPE_MS
        stick = self._at_bottom()

        if self.current is not None:
            bubble, text, n = self.current
            n += 1
            bubble.config(text=text[:n])
            if n >= len(text):
                self.current = None
                delay = PAUSE_MS
            else:
                self.current = (bubble, text, n)
            if stick:
                self._scroll_to_bottom()
        elif self.queue:
            name, text, animate = self.queue.popleft()
            bubble = self._add_row(name, self.characters.class_of(name))
            if animate:
                self.current = (bubble, text, 0)
            else:
                bubble.config(text=text)
                delay = 1
            if stick:
                self._scroll_to_bottom()

        self.root.after(delay, self.pump)


def create_gui():
    root = tk.Tk()
    ChatWindow(root)
    root.mainloop()


if __name__ == "__main__":
    create_gui()
