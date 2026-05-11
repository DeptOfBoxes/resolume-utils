#!/usr/bin/env python3
"""
REST Health Monitor — floating monitor panel.

Hides itself when Arena is not running; auto-shows when Arena starts.
Imports health-check functions from rest_health_monitor.py (same directory).

Usage:
  python3 rest_health_monitor_ui.py              # Arena/Avenue (port 8080)
  python3 rest_health_monitor_ui.py --port 8081  # Wire mode
"""

import tkinter as tk
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rest_health_monitor import (
    IS_WIN,
    arena_pid, arena_version, port_listeners, port_listen_address,
    rest_probe, ws_check, docs_probe, monitors_probe,
    api_folder_collision, arena_log_issues,
    load_plugin_config, plugin_path,
)

# ── Visual constants ───────────────────────────────────────────────────────────
BG      = "#1c1c1e"
SEP     = "#3a3a3c"
FG      = "#e5e5ea"
FG_DIM  = "#636366"
GREEN   = "#32d74b"
YELLOW  = "#ffd60a"
RED     = "#ff453a"
CYAN    = "#64d2ff"

if IS_WIN:
    FONT_TITLE  = ("Segoe UI", 13, "bold")
    FONT_ROW    = ("Segoe UI", 12)
    FONT_SMALL  = ("Segoe UI", 11)
    FONT_FOOTER = ("Consolas", 10)
    FONT_SEC    = ("Segoe UI", 10, "bold")
else:
    FONT_TITLE  = ("Helvetica Neue", 13, "bold")
    FONT_ROW    = ("Helvetica Neue", 12)
    FONT_SMALL  = ("Helvetica Neue", 11)
    FONT_FOOTER = ("Menlo", 10)
    FONT_SEC    = ("Helvetica Neue", 10, "bold")

REFRESH_MS    = 5000
HIDE_DELAY_MS = 3000
WIN_W         = 420


# ── Panel ──────────────────────────────────────────────────────────────────────

class StatusPanel:
    def __init__(self, root: tk.Tk, port: int = 8080):
        self.root = root
        self.port = port
        self._arena_visible = False
        self._hide_pending  = None
        self._collapsed     = False

        self._build_chrome()
        self._build_content_area()
        self._build_footer()

        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = self.root.winfo_reqwidth()
        wh = self.root.winfo_reqheight()
        self.root.geometry(f"+{sw - ww - 20}+{sh - wh - 60}")

        self.root.withdraw()
        self._poll()

    # ── Chrome ─────────────────────────────────────────────────────────────────

    def _build_chrome(self):
        product = "Wire" if self.port == 8081 else "Arena / Avenue"
        self.root.title(f"REST Health Monitor — {product}")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        bar = tk.Frame(self.root, bg=BG, pady=10, padx=14)
        bar.pack(fill=tk.X)

        self._collapse_btn = tk.Label(bar, text="▼", font=FONT_SMALL,
                                       bg=BG, fg=FG_DIM, cursor="hand2")
        self._collapse_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._collapse_btn.bind("<Button-1>", lambda e: self._toggle_collapse())

        tk.Label(bar, text="REST Health Monitor",
                 font=FONT_TITLE, bg=BG, fg=FG).pack(side=tk.LEFT)

        self._sum_dot = tk.Label(bar, text="●", font=("Helvetica Neue", 15),
                                  bg=BG, fg=FG_DIM)
        self._sum_dot.pack(side=tk.RIGHT, padx=(0, 2))

        self._sum_label = tk.Label(bar, text="—", font=FONT_SMALL,
                                    bg=BG, fg=FG_DIM)
        self._sum_label.pack(side=tk.RIGHT, padx=(0, 8))

        self._sep_line = tk.Frame(self.root, bg=SEP, height=1)
        self._sep_line.pack(fill=tk.X)

    def _build_content_area(self):
        self._content = tk.Frame(self.root, bg=BG, padx=14, pady=6)
        self._content.pack(fill=tk.BOTH)

    def _build_footer(self):
        self._footer_sep = tk.Frame(self.root, bg=SEP, height=1)
        self._footer_sep.pack(fill=tk.X)
        self._footer = tk.Label(self.root, text="starting…",
                                 font=FONT_FOOTER, bg=BG, fg=FG_DIM, pady=6)
        self._footer.pack()

    def _on_close(self):
        self._arena_visible = False
        self.root.withdraw()

    def _toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._sep_line.pack_forget()
            self._content.pack_forget()
            self._footer_sep.pack_forget()
            self._footer.pack_forget()
            self._collapse_btn.config(text="▶")
        else:
            self._sep_line.pack(fill=tk.X)
            self._content.pack(fill=tk.BOTH)
            self._footer_sep.pack(fill=tk.X)
            self._footer.pack()
            self._collapse_btn.config(text="▼")
        self.root.update_idletasks()

    # ── Row helpers ────────────────────────────────────────────────────────────

    def _sep(self, p):
        tk.Frame(p, bg=SEP, height=1).pack(fill=tk.X, pady=(6, 2))

    def _hdr(self, p, text):
        tk.Label(p, text=text, font=FONT_SEC, bg=BG, fg=FG_DIM,
                 anchor="w").pack(fill=tk.X, pady=(4, 1))

    def _row(self, p, color, text, indent=0, small=False):
        f = tk.Frame(p, bg=BG)
        f.pack(fill=tk.X, pady=1)
        tk.Label(f, text="●", font=("Helvetica Neue", 10 if small else 12),
                 bg=BG, fg=color, width=2).pack(side=tk.LEFT, padx=(indent, 4))
        tk.Label(f, text=text,
                 font=FONT_SMALL if small else FONT_ROW,
                 bg=BG, fg=FG_DIM if small else FG,
                 anchor="w").pack(side=tk.LEFT)

    # ── Content refresh ────────────────────────────────────────────────────────

    def _refresh_content(self):
        for w in self._content.winfo_children():
            w.destroy()

        problems = []
        port = self.port

        # ── Arena ──
        pid = arena_pid()
        ver = arena_version()
        ver_str = f"  · v{ver}" if ver else ""
        if pid:
            self._row(self._content, GREEN, f"Arena running  (PID {pid}{ver_str})")
            if ver:
                parts = ver.split(".")
                minor = int(parts[1]) if len(parts) > 1 else 0
                if minor < 24:
                    self._row(self._content, YELLOW, f"v{ver} — update to 7.24+",
                              indent=16, small=True)
                elif minor < 26:
                    self._row(self._content, CYAN, "7.26+ adds animation params",
                              indent=16, small=True)
        else:
            self._row(self._content, YELLOW, "Arena not running")
            problems.append("Arena offline")

        self._sep(self._content)

        # ── Webserver ──
        product = "Wire" if port == 8081 else "Arena/Avenue"
        self._hdr(self._content, f"WEBSERVER  :{port}  ({product})")
        holders = port_listeners(port)

        if not holders:
            if pid:
                self._row(self._content, RED, f"Nothing on :{port}")
                self._row(self._content, YELLOW, "Preferences → Webserver → enable",
                          indent=16, small=True)
                problems.append("Webserver off")
            else:
                self._row(self._content, FG_DIM, f":{port} unbound  (Arena not running)")
        else:
            is_arena = any("arena" in p.lower() or "resolume" in p.lower()
                           for p, _ in holders)
            if is_arena:
                addr = port_listen_address(port)
                if addr in ("*", "0.0.0.0", ""):
                    self._row(self._content, GREEN,
                              f"Arena owns :{port} — 0.0.0.0  (network accessible)")
                elif addr == "127.0.0.1":
                    self._row(self._content, GREEN,
                              f"Arena owns :{port} — localhost only")
                    self._row(self._content, CYAN,
                              "Remote tools (Companion, TD, QLab) won't reach this",
                              indent=16, small=True)
                else:
                    self._row(self._content, GREEN, f"Arena owns :{port} — {addr}")

                code, ms, summary = rest_probe(port)
                if code == 200:
                    self._row(self._content, GREEN, f"REST  HTTP 200  ({ms:.0f} ms)")
                    if ms > 500:
                        self._row(self._content, YELLOW,
                                  "Slow — use /parameter/by-id/ for real-time reads",
                                  indent=16, small=True)
                    if summary:
                        self._row(self._content, CYAN, summary, indent=16, small=True)
                else:
                    self._row(self._content, YELLOW, f"REST returned {code}")
                    problems.append(f"REST {code}")

                ws_ok, ws_ms, ws_err = ws_check(port)
                if ws_ok:
                    self._row(self._content, GREEN, f"WebSocket  101  ({ws_ms:.0f} ms)")
                else:
                    self._row(self._content, RED, f"WebSocket failed — {ws_err}")
                    problems.append("WebSocket down")
            else:
                proc, ppid = holders[0]
                self._row(self._content, RED, f":{port} held by {proc}  (PID {ppid})")
                self._row(self._content, YELLOW,
                          f"kill {ppid}  then re-enable Webserver",
                          indent=16, small=True)
                problems.append(f":{port} blocked ({proc})")

        self._sep(self._content)

        # ── API Endpoints ──
        if pid and holders and any("arena" in p.lower() or "resolume" in p.lower()
                                    for p, _ in holders):
            self._hdr(self._content, "API ENDPOINTS")
            docs_ok, docs_ms = docs_probe(port)
            self._row(self._content,
                      GREEN if docs_ok else YELLOW,
                      f"/api/docs/rest/  ({docs_ms:.0f} ms)" if docs_ok
                      else f"/api/docs/rest/ not reachable")

            mon_ok, mon_ms = monitors_probe(port)
            if mon_ok is True:
                self._row(self._content, GREEN,
                          f"/monitors/ available  ({mon_ms:.0f} ms)")
            elif mon_ok is None:
                self._row(self._content, CYAN,
                          "/monitors/ — Arena < 7.26  (update for output snapshots)")
            else:
                self._row(self._content, YELLOW, "/monitors/ error")

            self._sep(self._content)

        # ── API folder collision ──
        if api_folder_collision():
            self._hdr(self._content, "FILESYSTEM")
            self._row(self._content, RED, '"API" folder found in Resolume Arena docs')
            self._row(self._content, YELLOW,
                      "Rename/remove it — silently breaks REST",
                      indent=16, small=True)
            problems.append('"API" folder collision')
            self._sep(self._content)

        # ── Plugins ──
        plugins = load_plugin_config()
        if plugins is not None:
            self._hdr(self._content, f"PLUGINS  ({len(plugins)} configured)")
            missing_p = [b for b, _ in plugins if not os.path.exists(plugin_path(b))]
            n_ok = len(plugins) - len(missing_p)
            if not missing_p:
                self._row(self._content, GREEN, f"{n_ok}/{len(plugins)} installed")
            else:
                self._row(self._content, YELLOW, f"{n_ok}/{len(plugins)} installed")
                for b in missing_p[:4]:
                    self._row(self._content, RED, b, indent=16, small=True)
                if len(missing_p) > 4:
                    self._row(self._content, RED, f"…and {len(missing_p)-4} more",
                              indent=16, small=True)
                problems.append(f"{len(missing_p)} plugin(s) missing")
            self._sep(self._content)

        # ── Arena log ──
        self._hdr(self._content, "ARENA LOG")
        issues = arena_log_issues()
        if issues:
            self._row(self._content, RED, f"{len(issues)} FFGL error(s) in log")
            for line in issues[:2]:
                snippet = ("…" + line[-44:]) if len(line) > 44 else line
                self._row(self._content, RED, snippet, indent=16, small=True)
            problems.append(f"{len(issues)} FFGL error(s)")
        else:
            self._row(self._content, GREEN, "No FFGL panics")

        # ── Summary ──
        now = datetime.datetime.now().strftime("%H:%M:%S")
        if problems:
            self._sum_dot.config(fg=RED)
            self._sum_label.config(fg=RED, text="  ·  ".join(problems[:2]))
        else:
            self._sum_dot.config(fg=GREEN)
            self._sum_label.config(fg=GREEN, text="All clear")

        self._footer.config(
            text=f":{port}  ·  updated {now}  ·  refreshes every {REFRESH_MS//1000}s")

    # ── Visibility ─────────────────────────────────────────────────────────────

    def _show(self):
        if self._hide_pending is not None:
            self.root.after_cancel(self._hide_pending)
            self._hide_pending = None
        if not self._arena_visible:
            self._arena_visible = True
            self.root.deiconify()
            # do not lift — stay behind Resolume until user clicks the window

    def _hide(self):
        if self._arena_visible:
            self._arena_visible = False
            self.root.withdraw()
        self._hide_pending = None

    # ── Poll ───────────────────────────────────────────────────────────────────

    def _poll(self):
        if arena_pid():
            self._show()
        else:
            if self._arena_visible and self._hide_pending is None:
                self._hide_pending = self.root.after(HIDE_DELAY_MS, self._hide)
        self._refresh_content()
        self.root.after(REFRESH_MS, self._poll)


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    port = 8080
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("--port="):
            port = int(arg[7:])
        elif arg == "--port" and i < len(sys.argv):
            port = int(sys.argv[i + 1])

    root = tk.Tk()
    root.minsize(WIN_W, 1)
    root.maxsize(WIN_W, 9999)
    StatusPanel(root, port=port)
    root.mainloop()


if __name__ == "__main__":
    main()
