"""
MacroRecorder - A cross-platform macro recorder and player
Features: Record mouse/keyboard, Playback, Save/Load, Schedule,
          Always-on-top, Editable event log
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import json
import time
import threading
import os
from datetime import datetime

try:
    from pynput import mouse, keyboard
    from pynput.mouse import Button, Controller as MouseController
    from pynput.keyboard import Key, Controller as KeyboardController
    PYNPUT_OK = True
except ImportError:
    PYNPUT_OK = False

try:
    import schedule
    SCHEDULE_OK = True
except ImportError:
    SCHEDULE_OK = False


# ─────────────────────────────────────────────
#  CORE RECORDER ENGINE
# ─────────────────────────────────────────────

class MacroEngine:
    def __init__(self):
        self.events = []
        self.recording = False
        self.playing = False
        self._start_time = None
        self._mouse_listener = None
        self._keyboard_listener = None
        self._mouse_ctrl = MouseController() if PYNPUT_OK else None
        self._keyboard_ctrl = KeyboardController() if PYNPUT_OK else None

    def start_recording(self):
        if not PYNPUT_OK:
            raise RuntimeError("pynput not available")
        self.events = []
        self.recording = True
        self._start_time = time.time()
        self._mouse_listener = mouse.Listener(
            on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press, on_release=self._on_key_release)
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop_recording(self):
        self.recording = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()

    def _ts(self):
        return round(time.time() - self._start_time, 4)

    def _on_move(self, x, y):
        self.events.append({"type": "move", "x": x, "y": y, "t": self._ts()})

    def _on_click(self, x, y, button, pressed):
        self.events.append({"type": "click", "x": x, "y": y,
                             "button": button.name, "pressed": pressed, "t": self._ts()})

    def _on_scroll(self, x, y, dx, dy):
        self.events.append({"type": "scroll", "x": x, "y": y, "dx": dx, "dy": dy, "t": self._ts()})

    def _on_key_press(self, key):
        self.events.append({"type": "key_press", "key": self._key_name(key), "t": self._ts()})

    def _on_key_release(self, key):
        self.events.append({"type": "key_release", "key": self._key_name(key), "t": self._ts()})

    def _key_name(self, key):
        try:
            return key.char
        except AttributeError:
            return str(key)

    def play(self, speed=1.0, repeat=1, on_done=None):
        if not PYNPUT_OK or not self.events:
            return
        self.playing = True

        def _run():
            for _ in range(repeat):
                if not self.playing:
                    break
                prev_t = 0
                for ev in self.events:
                    if not self.playing:
                        break
                    delay = (ev["t"] - prev_t) / speed
                    if delay > 0:
                        time.sleep(delay)
                    prev_t = ev["t"]
                    self._replay_event(ev)
            self.playing = False
            if on_done:
                on_done()

        threading.Thread(target=_run, daemon=True).start()

    def stop_playback(self):
        self.playing = False

    def _replay_event(self, ev):
        t = ev["type"]
        m = self._mouse_ctrl
        k = self._keyboard_ctrl
        if t == "move":
            m.position = (ev["x"], ev["y"])
        elif t == "click":
            btn = Button.left if ev["button"] == "left" else Button.right
            m.position = (ev["x"], ev["y"])
            if ev["pressed"]:
                m.press(btn)
            else:
                m.release(btn)
        elif t == "scroll":
            m.scroll(ev["dx"], ev["dy"])
        elif t == "key_press":
            self._press_key(k, ev["key"])
        elif t == "key_release":
            self._release_key(k, ev["key"])

    def _press_key(self, ctrl, key_str):
        try:
            special = self._parse_special(key_str)
            ctrl.press(special if special else key_str)
        except Exception:
            pass

    def _release_key(self, ctrl, key_str):
        try:
            special = self._parse_special(key_str)
            ctrl.release(special if special else key_str)
        except Exception:
            pass

    def _parse_special(self, key_str):
        mapping = {
            "Key.space": Key.space, "Key.enter": Key.enter,
            "Key.backspace": Key.backspace, "Key.tab": Key.tab,
            "Key.shift": Key.shift, "Key.ctrl": Key.ctrl,
            "Key.alt": Key.alt, "Key.esc": Key.esc,
            "Key.up": Key.up, "Key.down": Key.down,
            "Key.left": Key.left, "Key.right": Key.right,
            "Key.delete": Key.delete, "Key.home": Key.home,
            "Key.end": Key.end, "Key.page_up": Key.page_up,
            "Key.page_down": Key.page_down,
        }
        return mapping.get(key_str)

    def save(self, path, name="", description=""):
        data = {
            "name": name or os.path.basename(path),
            "description": description,
            "created": datetime.now().isoformat(),
            "event_count": len(self.events),
            "duration": self.events[-1]["t"] if self.events else 0,
            "events": self.events,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self, path):
        with open(path) as f:
            data = json.load(f)
        self.events = data.get("events", [])
        return data


# ─────────────────────────────────────────────
#  SCHEDULER
# ─────────────────────────────────────────────

class MacroScheduler:
    def __init__(self):
        self._jobs = []
        self._running = False

    def add_job(self, engine, interval_sec, repeat_times, label=""):
        job = {
            "label": label or f"Every {interval_sec}s",
            "interval": interval_sec,
            "repeat": repeat_times,
            "engine": engine,
            "next_run": time.time() + interval_sec,
        }
        self._jobs.append(job)
        self._ensure_running()
        return job

    def remove_all(self):
        self._jobs.clear()

    def _ensure_running(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            now = time.time()
            for job in list(self._jobs):
                if now >= job["next_run"] and not job["engine"].playing:
                    job["engine"].play(repeat=job["repeat"])
                    job["next_run"] = now + job["interval"]
            time.sleep(0.5)

    def stop(self):
        self._running = False


# ─────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────

DARK_BG   = "#1a1d27"
PANEL_BG  = "#22263a"
ACCENT    = "#6c63ff"
ACCENT2   = "#ff6584"
TEXT      = "#e8e8f0"
MUTED     = "#7b7fa8"
SUCCESS   = "#43d98c"
DANGER    = "#ff4d6d"
BORDER    = "#2e3250"
WARN      = "#f5a623"
FONT_MONO = ("Consolas", 10)
FONT_UI   = ("Segoe UI", 10)
FONT_HEAD = ("Segoe UI", 13, "bold")


def _button(parent, text, cmd, color=ACCENT, fg="white", width=14):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg,
                     activebackground=color, activeforeground=fg,
                     font=("Segoe UI", 10, "bold"), bd=0,
                     padx=10, pady=8, width=width, cursor="hand2", relief=tk.FLAT)


# ─────────────────────────────────────────────
#  EDIT EVENT DIALOG
# ─────────────────────────────────────────────

class EditEventDialog(tk.Toplevel):
    """Dialog to edit a single macro event."""

    def __init__(self, parent, event, on_save):
        super().__init__(parent)
        self.event = dict(event)
        self.on_save = on_save
        self.title("Edit Event")
        self.configure(bg=DARK_BG)
        self.resizable(False, False)
        self.grab_set()
        self._fields = {}
        self._build()
        self.geometry("400x380")

    def _build(self):
        tk.Label(self, text="Edit Event", bg=DARK_BG, fg=TEXT,
                 font=FONT_HEAD, pady=12).pack(anchor="w", padx=20)

        tk.Label(self, text=f"Type:  {self.event.get('type','').upper()}",
                 bg=DARK_BG, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=8)

        form = tk.Frame(self, bg=DARK_BG)
        form.pack(fill=tk.X, padx=20)

        # Show editable fields depending on event type
        etype = self.event.get("type", "")
        editable = ["t"]  # timestamp always editable

        if etype in ("move", "click", "scroll"):
            editable += ["x", "y"]
        if etype == "click":
            editable += ["button"]
        if etype == "scroll":
            editable += ["dx", "dy"]
        if etype in ("key_press", "key_release"):
            editable += ["key"]

        for i, key in enumerate(editable):
            tk.Label(form, text=key, bg=DARK_BG, fg=MUTED,
                     font=FONT_UI, width=10, anchor="w").grid(row=i, column=0, pady=4, sticky="w")
            var = tk.StringVar(value=str(self.event.get(key, "")))
            entry = tk.Entry(form, textvariable=var, bg=PANEL_BG, fg=TEXT,
                             font=FONT_MONO, bd=0, insertbackground=TEXT, width=24)
            entry.grid(row=i, column=1, pady=4, padx=8, sticky="w")
            self._fields[key] = (var, type(self.event.get(key, "")))

        # Special: checkbox for "pressed" on click events
        if etype == "click":
            self._pressed_var = tk.BooleanVar(value=self.event.get("pressed", True))
            tk.Label(form, text="pressed", bg=DARK_BG, fg=MUTED,
                     font=FONT_UI, width=10, anchor="w").grid(row=len(editable), column=0, pady=4, sticky="w")
            tk.Checkbutton(form, variable=self._pressed_var, bg=DARK_BG,
                           fg=TEXT, selectcolor=ACCENT,
                           activebackground=DARK_BG).grid(row=len(editable), column=1, sticky="w")
        else:
            self._pressed_var = None

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=20, pady=12)

        btn_row = tk.Frame(self, bg=DARK_BG)
        btn_row.pack(pady=4)
        _button(btn_row, "✓  Save", self._save, SUCCESS, width=10).grid(row=0, column=0, padx=6)
        _button(btn_row, "✕  Cancel", self.destroy, MUTED, width=10).grid(row=0, column=1, padx=6)

    def _save(self):
        updated = dict(self.event)
        for key, (var, orig_type) in self._fields.items():
            raw = var.get().strip()
            try:
                if orig_type == int:
                    updated[key] = int(raw)
                elif orig_type == float:
                    updated[key] = float(raw)
                else:
                    updated[key] = raw
            except ValueError:
                messagebox.showerror("Invalid value", f"'{raw}' is not valid for field '{key}'")
                return
        if self._pressed_var is not None:
            updated["pressed"] = self._pressed_var.get()
        self.on_save(updated)
        self.destroy()


# ─────────────────────────────────────────────
#  MAIN APP
# ─────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MacroRecorder")
        self.geometry("960x660")
        self.minsize(780, 520)
        self.configure(bg=DARK_BG)

        self.engine = MacroEngine()
        self.scheduler = MacroScheduler()
        self._status_var = tk.StringVar(value="Ready")
        self._record_start = None
        self._timer_id = None
        self._always_on_top = tk.BooleanVar(value=False)

        self._build_ui()
        self._update_buttons()

        if not PYNPUT_OK:
            messagebox.showwarning(
                "Missing dependency",
                "Install pynput:\n  pip install pynput\n\nRecording/playback disabled.")

    def _build_ui(self):
        self._build_sidebar()
        self._build_main()
        self._build_statusbar()

    def _build_sidebar(self):
        sb = tk.Frame(self, bg=PANEL_BG, width=210)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        sb.pack_propagate(False)

        tk.Label(sb, text="⬡  MacroRecorder", bg=PANEL_BG, fg=ACCENT,
                 font=("Segoe UI", 12, "bold"), pady=20).pack(fill=tk.X, padx=12)

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12)

        nav_items = [
            ("🔴  Recorder",  self._show_recorder),
            ("▶  Library",    self._show_library),
            ("🕐  Scheduler", self._show_scheduler),
            ("⚙  Settings",  self._show_settings),
        ]
        self._nav_buttons = []
        for label, cmd in nav_items:
            btn = tk.Button(sb, text=label, bg=PANEL_BG, fg=TEXT,
                            activebackground=ACCENT, activeforeground="white",
                            font=FONT_UI, bd=0, padx=16, pady=10, anchor="w",
                            cursor="hand2", command=cmd)
            btn.pack(fill=tk.X, pady=1)
            self._nav_buttons.append(btn)

        self._nav_buttons[0].configure(bg=ACCENT, fg="white")

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=8)

        # ── Always on Top toggle ──
        aot_frame = tk.Frame(sb, bg=PANEL_BG)
        aot_frame.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(aot_frame, text="Always on Top", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._aot_btn = tk.Button(
            aot_frame, text="OFF", bg=BORDER, fg=MUTED,
            font=("Segoe UI", 8, "bold"), bd=0, padx=8, pady=2,
            cursor="hand2", command=self._toggle_always_on_top)
        self._aot_btn.pack(side=tk.RIGHT)

        tk.Frame(sb, bg=BORDER, height=1).pack(fill=tk.X, padx=12, pady=4)

        # Timer
        self._timer_var = tk.StringVar(value="00:00.0")
        tk.Label(sb, textvariable=self._timer_var, bg=PANEL_BG, fg=ACCENT2,
                 font=("Consolas", 22, "bold")).pack(pady=4)
        tk.Label(sb, text="recording time", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack()

        tk.Frame(sb, bg=PANEL_BG).pack(expand=True, fill=tk.Y)
        tk.Label(sb, text="v1.1.0", bg=PANEL_BG, fg=MUTED,
                 font=("Segoe UI", 8)).pack(pady=8)

    def _toggle_always_on_top(self):
        val = not self._always_on_top.get()
        self._always_on_top.set(val)
        self.wm_attributes("-topmost", val)
        if val:
            self._aot_btn.configure(text="ON", bg=ACCENT, fg="white")
        else:
            self._aot_btn.configure(text="OFF", bg=BORDER, fg=MUTED)
        self.set_status("Always on Top: " + ("ON ✓" if val else "OFF"))

    def _build_main(self):
        self._main = tk.Frame(self, bg=DARK_BG)
        self._main.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self._frames = {}
        for name, cls in [
            ("recorder",  RecorderPanel),
            ("library",   LibraryPanel),
            ("scheduler", SchedulerPanel),
            ("settings",  SettingsPanel),
        ]:
            frame = cls(self._main, self)
            frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = frame

        self._show_panel("recorder")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL_BG, height=28)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, textvariable=self._status_var, bg=PANEL_BG,
                 fg=MUTED, font=("Segoe UI", 9), padx=12).pack(side=tk.LEFT)

    def _show_panel(self, name):
        self._frames[name].tkraise()

    def _highlight_nav(self, idx):
        for i, btn in enumerate(self._nav_buttons):
            btn.configure(bg=ACCENT if i == idx else PANEL_BG,
                          fg="white" if i == idx else TEXT)

    def _show_recorder(self):
        self._show_panel("recorder"); self._highlight_nav(0)

    def _show_library(self):
        self._frames["library"].refresh()
        self._show_panel("library"); self._highlight_nav(1)

    def _show_scheduler(self):
        self._show_panel("scheduler"); self._highlight_nav(2)

    def _show_settings(self):
        self._show_panel("settings"); self._highlight_nav(3)

    def start_recording(self):
        try:
            self.engine.start_recording()
            self._record_start = time.time()
            self._tick_timer()
            self.set_status("🔴 Recording…")
            self._update_buttons()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def stop_recording(self):
        self.engine.stop_recording()
        if self._timer_id:
            self.after_cancel(self._timer_id)
        self.set_status(f"Recorded {len(self.engine.events)} events  ·  {self._timer_var.get()}")
        self._update_buttons()

    def start_playback(self):
        rp = self._frames["recorder"]
        speed = float(rp.speed_var.get())
        repeat = int(rp.repeat_var.get())
        self.engine.play(speed=speed, repeat=repeat, on_done=self._on_play_done)
        self.set_status(f"▶ Playing  ×{repeat}  at {speed}×")
        self._update_buttons()

    def stop_playback(self):
        self.engine.stop_playback()
        self.set_status("Stopped.")
        self._update_buttons()

    def _on_play_done(self):
        self.after(0, self._update_buttons)
        self.after(0, lambda: self.set_status("Playback complete."))

    def _tick_timer(self):
        if not self.engine.recording:
            return
        elapsed = time.time() - self._record_start
        mins = int(elapsed // 60)
        secs = elapsed % 60
        self._timer_var.set(f"{mins:02d}:{secs:04.1f}")
        self._timer_id = self.after(100, self._tick_timer)

    def _update_buttons(self):
        self.after(0, self._frames["recorder"].sync_buttons)

    def set_status(self, msg):
        self._status_var.set(msg)

    def save_macro(self, path, name, desc):
        self.engine.save(path, name, desc)

    def load_macro(self, path):
        meta = self.engine.load(path)
        self.set_status(f"Loaded: {meta.get('name','')}  ({len(self.engine.events)} events)")
        self._update_buttons()
        return meta


# ─────────────────────────────────────────────
#  RECORDER PANEL  (with editable event table)
# ─────────────────────────────────────────────

class RecorderPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="Macro Recorder", bg=DARK_BG, fg=TEXT,
                 font=FONT_HEAD, pady=16).pack(anchor="w", padx=24)

        # ── Transport ──
        btns = tk.Frame(self, bg=DARK_BG)
        btns.pack(padx=24, anchor="w")

        self.btn_record   = _button(btns, "⏺  Record",    self.app.start_recording, DANGER)
        self.btn_stop     = _button(btns, "⏹  Stop",      self._stop,               MUTED)
        self.btn_play     = _button(btns, "▶  Play",      self.app.start_playback,  SUCCESS)
        self.btn_stop_play= _button(btns, "⏹  Stop Play", self.app.stop_playback,   MUTED)

        for i, b in enumerate([self.btn_record, self.btn_stop, self.btn_play, self.btn_stop_play]):
            b.grid(row=0, column=i, padx=4)

        # ── Options ──
        opts = tk.Frame(self, bg=DARK_BG)
        opts.pack(padx=24, pady=10, anchor="w")

        tk.Label(opts, text="Speed:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=0, padx=(0,4))
        self.speed_var = tk.StringVar(value="1.0")
        ttk.Combobox(opts, textvariable=self.speed_var, width=6,
                     values=["0.25","0.5","0.75","1.0","1.5","2.0","4.0"]).grid(row=0, column=1, padx=(0,16))

        tk.Label(opts, text="Repeat:", bg=DARK_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=2, padx=(0,4))
        self.repeat_var = tk.StringVar(value="1")
        tk.Spinbox(opts, textvariable=self.repeat_var, from_=1, to=9999,
                   width=6, bg=PANEL_BG, fg=TEXT, bd=0, font=FONT_UI).grid(row=0, column=3, padx=(0,16))

        # ── Save / Load ──
        io_frame = tk.Frame(self, bg=DARK_BG)
        io_frame.pack(padx=24, anchor="w", pady=(0, 8))
        _button(io_frame, "💾  Save Macro", self._save, ACCENT,   width=14).grid(row=0, column=0, padx=4)
        _button(io_frame, "📂  Load Macro", self._load, PANEL_BG, width=14).grid(row=0, column=1, padx=4)

        # ── Event Table label + edit buttons ──
        tbl_header = tk.Frame(self, bg=DARK_BG)
        tbl_header.pack(fill=tk.X, padx=24, pady=(4, 2))
        tk.Label(tbl_header, text="Event Log  (double-click a row to edit)",
                 bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        edit_btns = tk.Frame(tbl_header, bg=DARK_BG)
        edit_btns.pack(side=tk.RIGHT)
        _button(edit_btns, "✏ Edit",    self._edit_selected,   ACCENT,  width=8).grid(row=0, column=0, padx=2)
        _button(edit_btns, "＋ Add",    self._add_event,       PANEL_BG,width=8).grid(row=0, column=1, padx=2)
        _button(edit_btns, "🗑 Delete", self._delete_selected, DANGER,  width=8).grid(row=0, column=2, padx=2)
        _button(edit_btns, "⬆",        self._move_up,         PANEL_BG, width=3).grid(row=0, column=3, padx=2)
        _button(edit_btns, "⬇",        self._move_down,       PANEL_BG, width=3).grid(row=0, column=4, padx=2)

        # ── Event Table (Treeview) ──
        cols = ("#", "Time(s)", "Type", "Details")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                  selectmode="extended", height=12)
        for col, w in zip(cols, [50, 80, 110, 500]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="w")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=PANEL_BG, fieldbackground=PANEL_BG,
                        foreground=TEXT, rowheight=24, font=FONT_MONO)
        style.configure("Treeview.Heading", background=BORDER, foreground=MUTED,
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", ACCENT)])

        # color tags per event type
        self.tree.tag_configure("move",        foreground=MUTED)
        self.tree.tag_configure("click",       foreground=SUCCESS)
        self.tree.tag_configure("scroll",      foreground=WARN)
        self.tree.tag_configure("key_press",   foreground=ACCENT)
        self.tree.tag_configure("key_release", foreground="#a89bff")

        vsb = tk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True, fill=tk.BOTH, padx=(24, 0), pady=(0, 12))
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 12), padx=(0, 8))

        self.tree.bind("<Double-1>", lambda e: self._edit_selected())
        self._schedule_refresh()

    # ── Table helpers ──────────────────────────

    def _event_detail(self, ev):
        t = ev.get("type", "")
        if t == "move":
            return f"x={ev['x']}  y={ev['y']}"
        elif t == "click":
            state = "DOWN" if ev.get("pressed") else "UP"
            return f"x={ev['x']}  y={ev['y']}  button={ev.get('button','?')}  {state}"
        elif t == "scroll":
            return f"x={ev['x']}  y={ev['y']}  dx={ev.get('dx',0)}  dy={ev.get('dy',0)}"
        elif t in ("key_press", "key_release"):
            return f"key={ev.get('key','?')}"
        return str(ev)

    def refresh_table(self):
        sel_indices = {self.tree.index(s) for s in self.tree.selection()}
        self.tree.delete(*self.tree.get_children())
        for i, ev in enumerate(self.app.engine.events):
            detail = self._event_detail(ev)
            tag = ev.get("type", "")
            iid = self.tree.insert("", tk.END,
                                   values=(i + 1, f"{ev.get('t', 0):.3f}", ev.get("type", ""), detail),
                                   tags=(tag,))
            if i in sel_indices:
                self.tree.selection_add(iid)

    def _schedule_refresh(self):
        if self.app.engine.recording:
            self.refresh_table()
            # auto-scroll to bottom while recording
            children = self.tree.get_children()
            if children:
                self.tree.see(children[-1])
        self.after(400, self._schedule_refresh)

    def _selected_indices(self):
        items = self.tree.selection()
        return [self.tree.index(i) for i in items]

    # ── Edit actions ───────────────────────────

    def _edit_selected(self):
        indices = self._selected_indices()
        if not indices:
            messagebox.showinfo("Select an event", "Click a row first.")
            return
        idx = indices[0]
        ev = self.app.engine.events[idx]

        def on_save(updated):
            self.app.engine.events[idx] = updated
            self.refresh_table()
            self.app.set_status(f"Event #{idx+1} updated.")

        EditEventDialog(self, ev, on_save)

    def _delete_selected(self):
        indices = sorted(self._selected_indices(), reverse=True)
        if not indices:
            messagebox.showinfo("Select events", "Click one or more rows first.")
            return
        if not messagebox.askyesno("Delete?", f"Delete {len(indices)} event(s)?"):
            return
        for i in indices:
            del self.app.engine.events[i]
        self.refresh_table()
        self.app.set_status(f"Deleted {len(indices)} event(s).")

    def _add_event(self):
        """Insert a blank click event at the end."""
        new_ev = {
            "type": "click", "x": 0, "y": 0,
            "button": "left", "pressed": True,
            "t": round(self.app.engine.events[-1]["t"] + 0.1, 4) if self.app.engine.events else 0.0
        }
        self.app.engine.events.append(new_ev)

        def on_save(updated):
            self.app.engine.events[-1] = updated
            self.refresh_table()

        EditEventDialog(self, new_ev, on_save)

    def _move_up(self):
        indices = self._selected_indices()
        if not indices or min(indices) == 0:
            return
        evs = self.app.engine.events
        for i in sorted(indices):
            evs[i - 1], evs[i] = evs[i], evs[i - 1]
        self.refresh_table()

    def _move_down(self):
        indices = self._selected_indices()
        evs = self.app.engine.events
        if not indices or max(indices) >= len(evs) - 1:
            return
        for i in sorted(indices, reverse=True):
            evs[i], evs[i + 1] = evs[i + 1], evs[i]
        self.refresh_table()

    # ── Save / Load ────────────────────────────

    def _stop(self):
        if self.app.engine.recording:
            self.app.stop_recording()
            self.refresh_table()
        elif self.app.engine.playing:
            self.app.stop_playback()

    def _save(self):
        if not self.app.engine.events:
            messagebox.showwarning("Nothing to save", "Record a macro first.")
            return
        win = tk.Toplevel(self)
        win.title("Save Macro")
        win.geometry("360x220")
        win.configure(bg=DARK_BG)
        win.resizable(False, False)

        tk.Label(win, text="Macro Name:", bg=DARK_BG, fg=TEXT, font=FONT_UI).pack(anchor="w", padx=20, pady=(16,2))
        name_entry = tk.Entry(win, bg=PANEL_BG, fg=TEXT, font=FONT_UI, bd=0, insertbackground=TEXT)
        name_entry.pack(fill=tk.X, padx=20)
        name_entry.insert(0, f"Macro_{datetime.now().strftime('%H%M%S')}")

        tk.Label(win, text="Description:", bg=DARK_BG, fg=TEXT, font=FONT_UI).pack(anchor="w", padx=20, pady=(10,2))
        desc_entry = tk.Entry(win, bg=PANEL_BG, fg=TEXT, font=FONT_UI, bd=0, insertbackground=TEXT)
        desc_entry.pack(fill=tk.X, padx=20)

        def do_save():
            path = filedialog.asksaveasfilename(
                defaultextension=".macro",
                filetypes=[("Macro files", "*.macro"), ("JSON", "*.json")],
                initialfile=name_entry.get())
            if path:
                self.app.save_macro(path, name_entry.get(), desc_entry.get())
                self.app.set_status(f"Saved: {os.path.basename(path)}")
                win.destroy()

        _button(win, "Save", do_save, ACCENT, width=10).pack(pady=12)

    def _load(self):
        path = filedialog.askopenfilename(
            filetypes=[("Macro files", "*.macro"), ("JSON", "*.json"), ("All", "*.*")])
        if path:
            try:
                self.app.load_macro(path)
                self.refresh_table()
            except Exception as e:
                messagebox.showerror("Load error", str(e))

    def sync_buttons(self):
        eng = self.app.engine
        rec, playing, has = eng.recording, eng.playing, bool(eng.events)
        self.btn_record.configure(state=tk.DISABLED if rec or playing else tk.NORMAL,
                                   bg=DANGER if not (rec or playing) else MUTED)
        self.btn_stop.configure(state=tk.NORMAL if rec or playing else tk.DISABLED)
        self.btn_play.configure(state=tk.NORMAL if has and not rec and not playing else tk.DISABLED)
        self.btn_stop_play.configure(state=tk.NORMAL if playing else tk.DISABLED)


# ─────────────────────────────────────────────
#  LIBRARY PANEL
# ─────────────────────────────────────────────

class LibraryPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app
        self._build()

    def _build(self):
        header = tk.Frame(self, bg=DARK_BG)
        header.pack(fill=tk.X, padx=24, pady=(20, 8))
        tk.Label(header, text="Macro Library", bg=DARK_BG, fg=TEXT, font=FONT_HEAD).pack(side=tk.LEFT)
        _button(header, "📂 Open Folder", self._open_folder, PANEL_BG, width=12).pack(side=tk.RIGHT)

        cols = ("Name", "Events", "Duration", "Created", "Path")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for col, w in zip(cols, [200, 80, 90, 160, 300]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=w, anchor="w")

        vsb = tk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(expand=True, fill=tk.BOTH, padx=24)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        btn_row = tk.Frame(self, bg=DARK_BG)
        btn_row.pack(padx=24, pady=10, anchor="w")
        _button(btn_row, "▶  Load & Play", self._load_play, SUCCESS, width=13).grid(row=0, column=0, padx=4)
        _button(btn_row, "📥  Load",       self._load_sel,  ACCENT,  width=10).grid(row=0, column=1, padx=4)
        _button(btn_row, "🗑  Delete",     self._delete,    DANGER,  width=10).grid(row=0, column=2, padx=4)

    def refresh(self, folder=None):
        folder = folder or os.path.expanduser("~")
        self.tree.delete(*self.tree.get_children())
        for f in os.listdir(folder):
            if f.endswith(".macro") or (f.endswith(".json") and "macro" in f.lower()):
                path = os.path.join(folder, f)
                try:
                    with open(path) as fp:
                        data = json.load(fp)
                    dur = round(data.get("duration", 0), 1)
                    self.tree.insert("", tk.END, values=(
                        data.get("name", f), data.get("event_count", "?"),
                        f"{dur}s", data.get("created", "")[:16], path))
                except Exception:
                    pass

    def _open_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.refresh(folder)

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select a macro", "Click a macro in the list first.")
            return None
        return self.tree.item(sel[0])["values"][4]

    def _load_sel(self):
        path = self._selected_path()
        if path:
            self.app.load_macro(path)
            self.app._show_recorder()
            self.app._frames["recorder"].refresh_table()

    def _load_play(self):
        path = self._selected_path()
        if path:
            self.app.load_macro(path)
            self.app._show_recorder()
            self.app._frames["recorder"].refresh_table()
            self.after(300, self.app.start_playback)

    def _delete(self):
        path = self._selected_path()
        if path and messagebox.askyesno("Delete?", f"Delete {os.path.basename(path)}?"):
            os.remove(path)
            self.refresh()


# ─────────────────────────────────────────────
#  SCHEDULER PANEL
# ─────────────────────────────────────────────

class SchedulerPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="Scheduler", bg=DARK_BG, fg=TEXT, font=FONT_HEAD, pady=20).pack(anchor="w", padx=24)

        form = tk.Frame(self, bg=PANEL_BG)
        form.pack(fill=tk.X, padx=24, pady=(0, 12))
        inner = tk.Frame(form, bg=PANEL_BG)
        inner.pack(padx=16, pady=12)

        for col, label in enumerate(["Run every (seconds)", "Repeat times", "Label"]):
            tk.Label(inner, text=label, bg=PANEL_BG, fg=MUTED, font=FONT_UI).grid(row=0, column=col, padx=8, sticky="w")

        self.interval_var = tk.StringVar(value="60")
        self.repeat_var   = tk.StringVar(value="1")
        self.label_var    = tk.StringVar(value="Scheduled Macro")

        for col, (var, w) in enumerate([(self.interval_var,10),(self.repeat_var,8),(self.label_var,20)]):
            tk.Entry(inner, textvariable=var, width=w, bg=DARK_BG, fg=TEXT,
                     font=FONT_UI, bd=0, insertbackground=TEXT).grid(row=1, column=col, padx=8, pady=4, sticky="w")

        _button(inner, "＋ Add Job", self._add_job, ACCENT, width=12).grid(row=1, column=3, padx=12)

        tk.Label(self, text="Active Jobs", bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9), pady=4).pack(anchor="w", padx=24)
        self.jobs_box = tk.Listbox(self, bg=PANEL_BG, fg=TEXT, font=FONT_UI, bd=0,
                                    selectbackground=ACCENT, height=12)
        self.jobs_box.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 8))
        _button(self, "🗑  Clear All Jobs", self._clear_jobs, DANGER, width=16).pack(anchor="w", padx=24, pady=4)

    def _add_job(self):
        if not self.app.engine.events:
            messagebox.showwarning("No macro loaded", "Record or load a macro first.")
            return
        try:
            interval = float(self.interval_var.get())
            repeat   = int(self.repeat_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Enter valid numbers.")
            return
        label = self.label_var.get()
        self.app.scheduler.add_job(self.app.engine, interval, repeat, label)
        self.jobs_box.insert(tk.END, f"  ⏰  {label}  —  every {interval}s  ×{repeat}")

    def _clear_jobs(self):
        self.app.scheduler.remove_all()
        self.jobs_box.delete(0, tk.END)


# ─────────────────────────────────────────────
#  SETTINGS PANEL
# ─────────────────────────────────────────────

class SettingsPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=DARK_BG)
        self.app = app
        self._build()

    def _build(self):
        tk.Label(self, text="Settings", bg=DARK_BG, fg=TEXT, font=FONT_HEAD, pady=20).pack(anchor="w", padx=24)

        for text, default in [
            ("Record mouse movements", True),
            ("Record keyboard events", True),
            ("Show event log while recording", True),
            ("Confirm before playback", False),
        ]:
            var = tk.BooleanVar(value=default)
            tk.Checkbutton(self, text=text, variable=var,
                           bg=DARK_BG, fg=TEXT, selectcolor=ACCENT,
                           activebackground=DARK_BG, activeforeground=TEXT,
                           font=FONT_UI).pack(anchor="w", padx=28, pady=4)

        tk.Label(self, text="\nHotkeys", bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24)
        for key, action in [("F9","Start/Stop Recording"),("F10","Start Playback"),("Esc","Stop Playback")]:
            row = tk.Frame(self, bg=PANEL_BG)
            row.pack(fill=tk.X, padx=24, pady=2)
            tk.Label(row, text=f"  {key}", bg=PANEL_BG, fg=ACCENT, font=FONT_MONO, width=8).pack(side=tk.LEFT)
            tk.Label(row, text=action, bg=PANEL_BG, fg=TEXT, font=FONT_UI).pack(side=tk.LEFT, padx=8)

        tk.Label(self, text="\nDependencies", bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24)
        for lib, ok in [("pynput", PYNPUT_OK), ("schedule", SCHEDULE_OK)]:
            status = "✓  installed" if ok else "✗  missing  —  pip install " + lib
            tk.Label(self, text=f"  {lib}:  {status}", bg=DARK_BG,
                     fg=SUCCESS if ok else DANGER, font=FONT_MONO).pack(anchor="w", padx=28)

        tk.Label(self, text="\nEvent Type Colors", bg=DARK_BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=24)
        for label, color in [("move","#7b7fa8"),("click","#43d98c"),("scroll","#f5a623"),
                              ("key_press","#6c63ff"),("key_release","#a89bff")]:
            row = tk.Frame(self, bg=PANEL_BG)
            row.pack(fill=tk.X, padx=24, pady=1)
            tk.Label(row, text=f"  {label}", bg=PANEL_BG, fg=color, font=FONT_MONO, width=16).pack(side=tk.LEFT)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
