#!/usr/bin/env python3
"""
REST Health Monitor — floating monitor panel.

Hides itself when Arena is not running; auto-shows when Arena starts.
Imports health-check functions from rest_health_monitor.py (same directory).
"""

import tkinter as tk
import threading
import time
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rest_health_monitor import (
    arena_pid, port_8080_info, rest_probe,
    arena_log_issues, plugin_path, PLUGINS,
)

# ── Visual constants ───────────────────────────────────────────────────────────
BG       = "#1c1c1e"
BG_LINE  = "#2c2c2e"
SEP      = "#3a3a3c"
FG       = "#e5e5ea"
FG_DIM   = "#636366"
GREEN    = "#32d74b"
YELLOW   = "#ffd60a"
RED      = "#ff453a"
CYAN     = "#64d2ff"

FONT_TITLE  = ("Helvetica Neue", 13, "bold")
FONT_ROW    = ("Helvetica Neue", 12)
FONT_SMALL  = ("Helvetica Neue", 11)
FONT_FOOTER = ("Menlo", 10)
FONT_SEC    = ("Helvetica Neue", 10, "bold")

REFRESH_MS    = 5000
HIDE_DELAY_MS = 3000   # wait before hiding after Arena stops
WIN_W         = 390


# ── Main panel ─────────────────────────────────────────────────────────────────

class StatusPanel:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._arena_visible = False   # tracks whether window is shown
        self._hide_pending  = None    # after-id for deferred hide

        self._build_chrome()
        self._build_content_area()
        self._build_footer()

        # Position: bottom-right, 20px from edges
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_reqwidth()
        wh = self.root.winfo_reqheight()
        self.root.geometry(f"+{sw - ww - 20}+{sh - wh - 60}")

        # Start hidden; _poll will show it when Arena comes up
        self.root.withdraw()

        self._poll()

    # ── Window chrome ──────────────────────────────────────────────────────────

    def _build_chrome(self):
        self.root.title("REST Health Monitor")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.wm_attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        bar = tk.Frame(self.root, bg=BG, pady=10, padx=14)
        bar.pack(fill=tk.X)

        tk.Label(bar, text="REST Health Monitor",
                 font=FONT_TITLE, bg=BG, fg=FG).pack(side=tk.LEFT)

        self._sum_dot   = tk.Label(bar, text="●", font=("Helvetica Neue", 15),
                                   bg=BG, fg=FG_DIM)
        self._sum_dot.pack(side=tk.RIGHT, padx=(0, 2))

        self._sum_label = tk.Label(bar, text="—", font=FONT_SMALL,
                                   bg=BG, fg=FG_DIM)
        self._sum_label.pack(side=tk.RIGHT, padx=(0, 8))

        tk.Frame(self.root, bg=SEP, height=1).pack(fill=tk.X)

    def _build_content_area(self):
        self._content = tk.Frame(self.root, bg=BG, padx=14, pady=6)
        self._content.pack(fill=tk.BOTH)

    def _build_footer(self):
        tk.Frame(self.root, bg=SEP, height=1).pack(fill=tk.X)
        self._footer = tk.Label(self.root, text="starting…",
                                font=FONT_FOOTER, bg=BG, fg=FG_DIM, pady=6)
        self._footer.pack()

    def _on_close(self):
        # Hide the window; the process keeps running (LaunchAgent will restart
        # on next login; window reappears next time Arena is launched).
        self._arena_visible = False
        self.root.withdraw()

    # ── Row helpers ────────────────────────────────────────────────────────────

    def _sep(self, parent):
        tk.Frame(parent, bg=SEP, height=1).pack(fill=tk.X, pady=(6, 2))

    def _section_header(self, parent, text):
        tk.Label(parent, text=text, font=FONT_SEC,
                 bg=BG, fg=FG_DIM, anchor="w").pack(fill=tk.X, pady=(4, 1))

    def _row(self, parent, color, text, indent=0, small=False):
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill=tk.X, pady=1)

        dot_size = 10 if small else 12
        tk.Label(frame, text="●", font=("Helvetica Neue", dot_size),
                 bg=BG, fg=color, width=2).pack(side=tk.LEFT, padx=(indent, 4))

        font = FONT_SMALL if small else FONT_ROW
        fg   = FG_DIM if small else FG
        tk.Label(frame, text=text, font=font,
                 bg=BG, fg=fg, anchor="w").pack(side=tk.LEFT)

    # ── Refresh logic ──────────────────────────────────────────────────────────

    def _refresh_content(self):
        for w in self._content.winfo_children():
            w.destroy()

        problems = []

        # ── Arena ──
        pid = arena_pid()
        if pid:
            self._row(self._content, GREEN, f"Arena running  (PID {pid})")
        else:
            self._row(self._content, YELLOW, "Arena not running")
            problems.append("Arena offline")

        self._sep(self._content)

        # ── REST ──
        self._section_header(self._content, "WEBSERVER  :8080")
        holders = port_8080_info()

        if not holders:
            if pid:
                self._row(self._content, RED, "Nothing on :8080")
                self._row(self._content, YELLOW,
                          "Preferences → Webserver → enable",
                          indent=16, small=True)
                problems.append("Webserver off")
            else:
                self._row(self._content, FG_DIM, "Unbound  (Arena not running)")
        else:
            is_arena = any("arena" in p.lower() or "resolume" in p.lower()
                           for p, _ in holders)
            if is_arena:
                code, ms, summary = rest_probe()
                if code == 200:
                    self._row(self._content, GREEN, f"HTTP 200  ({ms:.0f} ms)")
                    if summary:
                        self._row(self._content, CYAN, summary,
                                  indent=16, small=True)
                else:
                    self._row(self._content, YELLOW, f"REST returned {code}")
                    problems.append(f"REST {code}")
            else:
                proc, ppid = holders[0]
                self._row(self._content, RED, f":8080 held by {proc}  (PID {ppid})")
                self._row(self._content, YELLOW, f"kill {ppid}  then re-enable Webserver",
                          indent=16, small=True)
                problems.append(f":8080 blocked ({proc})")

        self._sep(self._content)

        # ── Plugins ──
        self._section_header(self._content, "PLUGINS")
        missing = [b for b, _ in PLUGINS if not os.path.exists(plugin_path(b))]
        n_ok = len(PLUGINS) - len(missing)

        if not missing:
            self._row(self._content, GREEN, f"{n_ok}/{len(PLUGINS)} installed")
        else:
            self._row(self._content, YELLOW, f"{n_ok}/{len(PLUGINS)} installed")
            for b in missing[:4]:
                self._row(self._content, RED, b, indent=16, small=True)
            if len(missing) > 4:
                self._row(self._content, RED, f"…and {len(missing)-4} more",
                          indent=16, small=True)
            problems.append(f"{len(missing)} plugin(s) missing")

        self._sep(self._content)

        # ── Arena log ──
        self._section_header(self._content, "ARENA LOG")
        issues = arena_log_issues()
        if issues:
            self._row(self._content, RED, f"{len(issues)} FFGL error(s) in log")
            for line in issues[:2]:
                snippet = ("…" + line[-46:]) if len(line) > 46 else line
                self._row(self._content, RED, snippet, indent=16, small=True)
            problems.append(f"{len(issues)} FFGL error(s)")
        else:
            self._row(self._content, GREEN, "No FFGL panics")

        # ── Summary bar ──
        import datetime
        now = datetime.datetime.now().strftime("%H:%M:%S")

        if problems:
            self._sum_dot.config(fg=RED)
            self._sum_label.config(fg=RED,
                                   text="  ·  ".join(problems[:2]))
        else:
            self._sum_dot.config(fg=GREEN)
            self._sum_label.config(fg=GREEN, text="All clear")

        self._footer.config(text=f"updated {now}  ·  refreshes every {REFRESH_MS//1000}s")

    # ── Visibility management ──────────────────────────────────────────────────

    def _show(self):
        if self._hide_pending is not None:
            self.root.after_cancel(self._hide_pending)
            self._hide_pending = None
        if not self._arena_visible:
            self._arena_visible = True
            self.root.deiconify()
            self.root.lift()

    def _hide(self):
        if self._arena_visible:
            self._arena_visible = False
            self.root.withdraw()
        self._hide_pending = None

    # ── Poll loop ──────────────────────────────────────────────────────────────

    def _poll(self):
        pid = arena_pid()

        if pid:
            self._show()
            self._refresh_content()
        else:
            # Defer hide so a restart doesn't flash the window closed/open
            if self._arena_visible and self._hide_pending is None:
                self._hide_pending = self.root.after(HIDE_DELAY_MS, self._hide)
            self._refresh_content()

        self.root.after(REFRESH_MS, self._poll)


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    root.minsize(WIN_W, 1)
    root.maxsize(WIN_W, 9999)
    StatusPanel(root)
    root.mainloop()


if __name__ == "__main__":
    main()
