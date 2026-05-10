#!/usr/bin/env python3
"""
REST Health Monitor
Watches Resolume Arena / Avenue / Wire REST API health in real time.

Usage:
  python3 rest_health_monitor.py                 # live watch (5s refresh)
  python3 rest_health_monitor.py --once          # single snapshot, then exit
  python3 rest_health_monitor.py --port 8081     # Wire mode (default: 8080)
  python3 rest_health_monitor.py --dev           # + sha-drift checks vs target/release
  python3 rest_health_monitor.py --root=/path    # explicit project root for --dev
"""

import subprocess, time, sys, os, socket, base64
import urllib.request, urllib.error
import hashlib, json
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────────
EXTRA_EFFECTS = os.path.expanduser("~/Documents/Resolume Arena/Extra Effects")
ARENA_DOCS    = os.path.expanduser("~/Documents/Resolume Arena")
ARENA_LOG     = os.path.expanduser("~/Library/Logs/Resolume Arena/Resolume Arena log.txt")
ARENA_APP     = "/Applications/Resolume Arena/Arena.app"

BUS_LIBS = [
    "libffgl_cpb_bus.dylib",
    "libffgl_texture_bus.dylib",
]

REFRESH_S = 5

# ── ANSI ───────────────────────────────────────────────────────────────────────
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


# ── Checks ─────────────────────────────────────────────────────────────────────

def arena_pid():
    try:
        r = subprocess.run(["pgrep", "-f", "Resolume Arena"],
                           capture_output=True, text=True, timeout=3)
        pids = [p for p in r.stdout.strip().split() if p]
        return pids[0] if pids else None
    except Exception:
        return None


def arena_version():
    """Read version from Arena.app bundle. Returns version string or None."""
    plist = os.path.join(ARENA_APP, "Contents", "Info.plist")
    try:
        r = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", "Print CFBundleShortVersionString", plist],
            capture_output=True, text=True, timeout=3
        )
        v = r.stdout.strip()
        return v if v else None
    except Exception:
        return None


def port_listeners(port=8080):
    """Returns list of (process_name, pid) tuples holding the given port LISTEN."""
    holders = []
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                holders.append((parts[0], parts[1]))
    except Exception:
        pass
    return holders

# backward-compat alias used by older callers
port_8080_info = lambda: port_listeners(8080)


def port_listen_address(port=8080):
    """Returns the bound address string ('0.0.0.0', '127.0.0.1', '*', etc.) or None."""
    try:
        r = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.split()
            # NAME column is last non-empty field; format: addr:port or *:port
            for p in parts:
                if f":{port}" in p:
                    addr = p.rsplit(f":{port}", 1)[0]
                    return addr if addr else "*"
    except Exception:
        pass
    return None


def rest_probe(port=8080):
    """Returns (status_code_or_err, latency_ms, composition_summary)."""
    url = f"http://127.0.0.1:{port}/api/v1/composition"
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            ms = (time.monotonic() - t0) * 1000
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
                layers = data.get("layers", [])
                total_clips = sum(len(lay.get("clips", [])) for lay in layers)
                summary = f"{len(layers)} layers, {total_clips} clips"
            except Exception:
                summary = "composition loaded"
            return resp.status, ms, summary
    except urllib.error.HTTPError as e:
        return e.code, (time.monotonic() - t0) * 1000, None
    except Exception as e:
        return str(e)[:60], (time.monotonic() - t0) * 1000, None


def ws_check(port=8080, timeout=3):
    """Attempt a WebSocket upgrade handshake. Returns (ok, latency_ms, error_or_None)."""
    t0 = time.monotonic()
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        sock.sendall((
            f"GET /api/v1 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        ).encode())
        resp = sock.recv(512).decode("utf-8", errors="replace")
        sock.close()
        ms = (time.monotonic() - t0) * 1000
        if "101" in resp:
            return True, ms, None
        first_line = resp.split("\r\n")[0][:60]
        return False, ms, first_line
    except Exception as e:
        return False, (time.monotonic() - t0) * 1000, str(e)[:60]


def docs_probe(port=8080, timeout=3):
    """Check that /api/docs/rest/ is reachable. Returns (ok, latency_ms)."""
    url = f"http://127.0.0.1:{port}/api/docs/rest/"
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200, (time.monotonic() - t0) * 1000
    except Exception:
        return False, (time.monotonic() - t0) * 1000


def monitors_probe(port=8080, timeout=3):
    """Check /api/v1/monitors (7.26+). Returns (True/False/None, ms).
    None means the endpoint doesn't exist (pre-7.26 Arena)."""
    url = f"http://127.0.0.1:{port}/api/v1/monitors"
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200, (time.monotonic() - t0) * 1000
    except urllib.error.HTTPError as e:
        ms = (time.monotonic() - t0) * 1000
        return (None, ms) if e.code == 404 else (False, ms)
    except Exception:
        return False, (time.monotonic() - t0) * 1000


def api_folder_collision():
    """Returns True if a folder named 'API' exists in the Resolume docs root.
    This silently breaks the REST API — known forum-reported issue."""
    return os.path.isdir(os.path.join(ARENA_DOCS, "API"))


def arena_log_issues(max_scan=600, max_show=8):
    """Returns recent Arena log lines containing FFGL panic/error keywords."""
    hits = []
    try:
        with open(ARENA_LOG, "r", errors="replace") as f:
            tail = f.readlines()[-max_scan:]
        for line in reversed(tail):
            l = line.rstrip()
            if any(kw in l for kw in
                   ["panic", "PANIC", "FFGL ERROR", "assertion `left", "assert_eq", "FFGL C BOUNDARY"]):
                hits.append(l)
                if len(hits) >= max_show:
                    break
    except Exception:
        pass
    return hits


def load_plugin_config():
    """Load plugin list from plugins.json next to this script.
    Returns list of (bundle_name, dylib_name) or None if no config file."""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins.json")
    if not os.path.exists(cfg):
        return None
    try:
        with open(cfg) as f:
            data = json.load(f)
        return [(p["bundle"], p.get("dylib", "")) for p in data]
    except Exception:
        return None


def plugin_path(bundle_name):
    return os.path.join(EXTRA_EFFECTS,
                        f"{bundle_name}.bundle", "Contents", "MacOS", bundle_name)


def file_sha(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return None


# ── Rendering ──────────────────────────────────────────────────────────────────

def _ok(msg):   return f"  {GREEN}✓{RESET}  {msg}"
def _warn(msg): return f"  {YELLOW}!{RESET}  {msg}"
def _err(msg):  return f"  {RED}✗{RESET}  {msg}"
def _info(msg): return f"  {CYAN}·{RESET}  {msg}"
def _sep():     return f"  {DIM}{'─'*46}{RESET}"


def render(port=8080, dev_root=None):
    out = []
    now = datetime.now().strftime("%H:%M:%S")
    product = "Wire" if port == 8081 else "Arena/Avenue"

    out.append(f"\n{BOLD}{'═'*50}{RESET}")
    out.append(f"{BOLD}  REST Health Monitor              [{now}]{RESET}")
    out.append(f"{BOLD}{'═'*50}{RESET}\n")

    problems = []

    # ── Resolume ──────────────────────────────────────────────────────────────
    out.append(f"{BOLD}  RESOLUME{RESET}")
    pid = arena_pid()
    ver = arena_version()
    ver_str = f" · v{ver}" if ver else ""
    if pid:
        out.append(_ok(f"Arena running  (PID {pid}{ver_str})"))
        if ver:
            parts = ver.split(".")
            minor = int(parts[1]) if len(parts) > 1 else 0
            patch = int(parts[2]) if len(parts) > 2 else 0
            if minor < 24:
                out.append(_warn(f"v{ver} — update to 7.24+ (webserver crash fix)"))
            elif minor < 26:
                out.append(_info(f"v{ver} — 7.26+ adds animation params + monitors endpoint"))
    else:
        out.append(_warn("Arena not running"))
        problems.append("Arena offline")
    out.append("")

    # ── REST / WebSocket ──────────────────────────────────────────────────────
    out.append(f"{BOLD}  WEBSERVER  :{port}  ({product}){RESET}")
    holders = port_listeners(port)

    if not holders:
        if pid:
            out.append(_err(f"Nothing listening on :{port}"))
            out.append(_warn(f"    → Arena: Preferences → Webserver → port {port}"))
            problems.append("Webserver off")
        else:
            out.append(_info(f":{port} unbound  (Arena not running)"))
    else:
        is_arena = any("arena" in p.lower() or "resolume" in p.lower() for p, _ in holders)
        if is_arena:
            # listen address
            addr = port_listen_address(port)
            if addr in ("*", "0.0.0.0", ""):
                out.append(_ok(f"Arena owns :{port} — 0.0.0.0  (network accessible)"))
            elif addr == "127.0.0.1":
                out.append(_ok(f"Arena owns :{port} — 127.0.0.1  (localhost only)"))
                out.append(_info("    Remote tools (Companion, TD, QLab) won't reach this"))
            else:
                out.append(_ok(f"Arena owns :{port} — {addr}"))

            # REST probe
            code, ms, summary = rest_probe(port)
            if code == 200:
                out.append(_ok(f"REST  HTTP 200  ({ms:.0f} ms)"))
                if ms > 500:
                    out.append(_warn("    Response slow — large composition; use /parameter/by-id/ for real-time reads"))
                if summary:
                    out.append(_info(f"Composition: {summary}"))
            else:
                out.append(_warn(f"REST returned: {code}"))
                problems.append(f"REST {code}")

            # WebSocket probe
            ws_ok, ws_ms, ws_err = ws_check(port)
            if ws_ok:
                out.append(_ok(f"WebSocket  101  ({ws_ms:.0f} ms)"))
            else:
                out.append(_err(f"WebSocket failed  — {ws_err}"))
                out.append(_info("    Real-time tools (Companion, custom GUIs) depend on WebSocket"))
                problems.append("WebSocket down")
        else:
            for proc, ppid in holders:
                out.append(_err(f":{port} held by  {BOLD}{proc}{RESET}  PID {ppid}  — NOT Arena"))
            out.append(_warn(f"    Fix:  kill {holders[0][1]}  then restart Arena with Webserver on"))
            problems.append(f":{port} blocked ({holders[0][0]})")
    out.append("")

    # ── API Endpoints ─────────────────────────────────────────────────────────
    if pid and holders and any("arena" in p.lower() or "resolume" in p.lower() for p, _ in holders):
        out.append(f"{BOLD}  API ENDPOINTS{RESET}")
        docs_ok, docs_ms = docs_probe(port)
        if docs_ok:
            out.append(_ok(f"/api/docs/rest/  ({docs_ms:.0f} ms)"))
        else:
            out.append(_warn(f"/api/docs/rest/ not reachable  ({docs_ms:.0f} ms)"))

        mon_ok, mon_ms = monitors_probe(port)
        if mon_ok is True:
            out.append(_ok(f"/monitors/ available  ({mon_ms:.0f} ms)  · v7.26+ features active"))
        elif mon_ok is None:
            out.append(_info("/monitors/ not found  — Arena < 7.26  (update for animation params + output snapshots)"))
        else:
            out.append(_warn(f"/monitors/ error  ({mon_ms:.0f} ms)"))
        out.append("")

    # ── Filesystem ────────────────────────────────────────────────────────────
    if api_folder_collision():
        out.append(f"{BOLD}  FILESYSTEM{RESET}")
        out.append(_err(f'"API" folder found in ~/Documents/Resolume Arena/'))
        out.append(_warn('    This silently breaks the REST API — rename or remove that folder'))
        problems.append('"API" folder collision')
        out.append("")

    # ── Shared Bus Libs (dev mode) ────────────────────────────────────────────
    if dev_root:
        target = os.path.join(dev_root, "target", "release")
        out.append(f"{BOLD}  SHARED BUS LIBS{RESET}")
        any_bus_drift = False
        for lib in BUS_LIBS:
            deployed = os.path.join(EXTRA_EFFECTS, lib)
            src      = os.path.join(target, lib)
            d_sha = file_sha(deployed)
            s_sha = file_sha(src)
            if d_sha is None:
                out.append(_warn(f"{lib}  — not deployed"))
            elif s_sha is None:
                out.append(_info(f"{lib}  — deployed ({d_sha})  [no build to compare]"))
            elif d_sha == s_sha:
                out.append(_ok(f"{lib}  ({d_sha})"))
            else:
                out.append(_err(f"{lib}  DRIFT  deployed={d_sha}  built={s_sha}"))
                any_bus_drift = True
        if any_bus_drift:
            out.append(_warn("    Run  ./deploy_all.sh  to recover"))
        out.append("")

    # ── Plugins ───────────────────────────────────────────────────────────────
    plugins = load_plugin_config()
    if plugins is not None:
        out.append(f"{BOLD}  PLUGINS  ({len(plugins)} configured){RESET}")
        stale, missing_p = [], []
        for bundle, dylib in plugins:
            path = plugin_path(bundle)
            if not os.path.exists(path):
                missing_p.append(bundle)
                continue
            if dev_root and dylib:
                src   = os.path.join(dev_root, "target", "release", dylib)
                d_sha = file_sha(path)
                s_sha = file_sha(src)
                if s_sha and d_sha != s_sha:
                    stale.append(bundle)
                    out.append(_err(f"{bundle}  stale  deployed={d_sha}  built={s_sha}"))
                else:
                    out.append(_ok(f"{bundle}"))
            else:
                out.append(_ok(f"{bundle}"))
        for b in missing_p:
            out.append(_warn(f"{b}  — not installed"))
        if stale and dev_root:
            out.append(_warn(f"  {len(stale)} stale bundle(s) — run  ./deploy_all.sh"))
        if missing_p:
            problems.append(f"{len(missing_p)} plugin(s) missing")
        out.append("")

    # ── Arena Log ────────────────────────────────────────────────────────────
    out.append(f"{BOLD}  ARENA LOG  (recent FFGL errors){RESET}")
    issues = arena_log_issues()
    if issues:
        for line in issues:
            snippet = ("…" + line[-90:]) if len(line) > 90 else line
            out.append(f"  {RED}{snippet}{RESET}")
        problems.append(f"{len(issues)} FFGL error(s)")
    else:
        out.append(_ok("No FFGL panics in recent log"))
    out.append("")

    # ── Summary ───────────────────────────────────────────────────────────────
    out.append(_sep())
    if problems:
        out.append(f"  {RED}{BOLD}ISSUES:{RESET}  " + "  ·  ".join(problems))
    else:
        out.append(f"  {GREEN}{BOLD}All clear{RESET}")
    out.append(_sep())
    out.append(f"\n  {DIM}refreshes every {REFRESH_S}s — Ctrl+C to stop{RESET}\n")

    return "\n".join(out)


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    port     = 8080
    dev_root = None
    once     = "--once" in sys.argv

    for arg in sys.argv[1:]:
        if arg == "--dev":
            dev_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        elif arg.startswith("--root="):
            dev_root = os.path.expanduser(arg[7:])
        elif arg.startswith("--port="):
            port = int(arg[7:])
        elif arg == "--port" and sys.argv.index(arg) + 1 < len(sys.argv):
            port = int(sys.argv[sys.argv.index(arg) + 1])

    if once:
        print(render(port, dev_root))
        return

    try:
        while True:
            os.system("clear")
            print(render(port, dev_root))
            time.sleep(REFRESH_S)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
