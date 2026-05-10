#!/usr/bin/env python3
"""
REST Health Monitor
Watches Resolume Arena + CubePort bus health in real time.

Usage:
  python3 rest_health_monitor.py           # live watch (refreshes every 5s)
  python3 rest_health_monitor.py --once    # single snapshot, then exit
  python3 rest_health_monitor.py --dev     # + sha-drift checks vs target/release
"""

import subprocess, time, sys, os, urllib.request, urllib.error, hashlib, json
from datetime import datetime

# ── Paths ──────────────────────────────────────────────────────────────────────
EXTRA_EFFECTS = os.path.expanduser("~/Documents/Resolume Arena/Extra Effects")
ARENA_LOG     = os.path.expanduser("~/Library/Logs/Resolume Arena/Resolume Arena log.txt")
REST_BASE     = "http://127.0.0.1:8080/api/v1"
COMP_URL      = f"{REST_BASE}/composition"

# ── Plugin manifest (must stay in sync with deploy_all.sh) ────────────────────
PLUGINS = [
    ("CubePortModel",         "libcubeport_model.dylib"),
    ("CubePortTransform",     "libcubeport_transform.dylib"),
    ("CubePortSurface",       "libcubeport_surface.dylib"),
    ("CubePortStructure",     "libcubeport_structure.dylib"),
    ("CubePortLighting",      "libcubeport_lighting.dylib"),
    ("CubePort Publisher",    "libffgl_cubeport_publisher.dylib"),
    ("CubePort Mirror",       "libcubeport_mirror.dylib"),
    ("CubePort Mirror Model", "libcubeport_mirror_model.dylib"),
    ("CubePortStage",         "libcubeport_stage.dylib"),
    ("CubePortKinetic",       "libcubeport_kinetic.dylib"),
    ("CubePort Ripple",       "libcubeport_ripple.dylib"),
]

BUS_LIBS = [
    "libffgl_cpb_bus.dylib",
    "libffgl_texture_bus.dylib",
]

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


def port_8080_info():
    """Returns list of (process_name, pid) tuples holding :8080 LISTEN."""
    holders = []
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP:8080", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5
        )
        for line in r.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                holders.append((parts[0], parts[1]))
    except Exception:
        pass
    return holders


def rest_probe():
    """Returns (status_code_or_error_str, latency_ms, composition_summary)."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(COMP_URL, headers={"Accept": "application/json"})
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


def file_sha(path):
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]
    except Exception:
        return None


def plugin_path(bundle_name):
    return os.path.join(EXTRA_EFFECTS,
                        f"{bundle_name}.bundle", "Contents", "MacOS", bundle_name)


def arena_log_issues(max_scan=600, max_show=8):
    """Returns recent lines containing panic/FFGL error/cubeport keywords."""
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


# ── Rendering ─────────────────────────────────────────────────────────────────

def _ok(msg):   return f"  {GREEN}✓{RESET}  {msg}"
def _warn(msg): return f"  {YELLOW}!{RESET}  {msg}"
def _err(msg):  return f"  {RED}✗{RESET}  {msg}"
def _info(msg): return f"  {CYAN}·{RESET}  {msg}"
def _sep():     return f"  {DIM}{'─'*46}{RESET}"


def render(dev_root=None):
    out = []
    now = datetime.now().strftime("%H:%M:%S")

    out.append(f"\n{BOLD}{'═'*50}{RESET}")
    out.append(f"{BOLD}  REST Health Monitor              [{now}]{RESET}")
    out.append(f"{BOLD}{'═'*50}{RESET}\n")

    # ── Resolume ──────────────────────────────────────────────────────────────
    out.append(f"{BOLD}  RESOLUME{RESET}")
    pid = arena_pid()
    if pid:
        out.append(_ok(f"Arena is running  (PID {pid})"))
    else:
        out.append(_warn("Arena is not running"))
    out.append("")

    # ── REST / Port 8080 ──────────────────────────────────────────────────────
    out.append(f"{BOLD}  REST WEBSERVER  (:8080){RESET}")
    holders = port_8080_info()

    if not holders:
        if pid:
            out.append(_err("Nothing listening on :8080"))
            out.append(_warn("    → Enable in Arena: Preferences → Webserver → port 8080"))
            out.append(_warn("    → Auto-mode CubePort modules will be dark until this is on"))
        else:
            out.append(_info(":8080 unbound  (Arena not running)"))
    else:
        arena_owns = any("arena" in p.lower() or "resolume" in p.lower() for p, _ in holders)
        if arena_owns:
            code, ms, summary = rest_probe()
            if code == 200:
                out.append(_ok(f"Arena owns :8080 — HTTP 200  ({ms:.0f} ms)"))
                if summary:
                    out.append(_info(f"Composition: {summary}"))
            else:
                out.append(_warn(f"Arena owns :8080 but REST returned: {code}"))
        else:
            for proc, ppid in holders:
                out.append(_err(f":8080 held by  {BOLD}{proc}{RESET}  PID {ppid}  — NOT Arena"))
            out.append(_warn("    Auto-mode CubePort modules are dark"))
            out.append(_warn(f"    Fix:  kill {holders[0][1]}  then restart Arena with Webserver on"))
    out.append("")

    # ── Shared Bus Libs (dev mode only) ───────────────────────────────────────
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
                out.append(_info(f"{lib}  — deployed  ({d_sha})  [no build to compare]"))
            elif d_sha == s_sha:
                out.append(_ok(f"{lib}  ({d_sha})"))
            else:
                out.append(_err(f"{lib}  DRIFT  deployed={d_sha}  built={s_sha}"))
                any_bus_drift = True
        if any_bus_drift:
            out.append(_warn("    Run  ./deploy_all.sh  to recover"))
        out.append("")

    # ── Plugin Bundles ────────────────────────────────────────────────────────
    out.append(f"{BOLD}  PLUGINS{RESET}")
    stale_bundles = []
    missing_bundles = []
    ok_count = 0

    for bundle, dylib in PLUGINS:
        path = plugin_path(bundle)
        installed = os.path.exists(path)

        if not installed:
            missing_bundles.append(bundle)
            continue

        if dev_root:
            src   = os.path.join(dev_root, "target", "release", dylib)
            d_sha = file_sha(path)
            s_sha = file_sha(src)
            if s_sha and d_sha != s_sha:
                stale_bundles.append(bundle)
                out.append(_err(f"{bundle}  stale  deployed={d_sha}  built={s_sha}"))
            else:
                ok_count += 1
                out.append(_ok(f"{bundle}"))
        else:
            ok_count += 1
            out.append(_ok(f"{bundle}"))

    for b in missing_bundles:
        out.append(_warn(f"{b}  — not installed"))

    if stale_bundles and dev_root:
        out.append("")
        out.append(_warn(f"  {len(stale_bundles)} stale bundle(s) — run  ./deploy_all.sh"))
    out.append("")

    # ── Arena Log ─────────────────────────────────────────────────────────────
    out.append(f"{BOLD}  ARENA LOG  (recent FFGL panics){RESET}")
    issues = arena_log_issues()
    if issues:
        for line in issues:
            snippet = ("…" + line[-90:]) if len(line) > 90 else line
            out.append(f"  {RED}{snippet}{RESET}")
    else:
        out.append(_ok("No FFGL panics in recent log"))
    out.append("")

    # ── Summary ───────────────────────────────────────────────────────────────
    problems = []
    if not pid:
        problems.append("Arena not running")
    if holders and not any("arena" in p.lower() or "resolume" in p.lower() for p, _ in holders):
        problems.append(f":8080 held by foreign process ({holders[0][0]})")
    if not holders and pid:
        problems.append("Webserver off")
    if stale_bundles:
        problems.append(f"{len(stale_bundles)} stale bundle(s)")
    if missing_bundles:
        problems.append(f"{len(missing_bundles)} plugin(s) not installed")
    if issues:
        problems.append(f"{len(issues)} FFGL error(s) in log")

    out.append(_sep())
    if problems:
        out.append(f"  {RED}{BOLD}ISSUES:{RESET}  " + "  ·  ".join(problems))
    else:
        out.append(f"  {GREEN}{BOLD}All clear{RESET}")
    out.append(_sep())
    out.append(f"\n  {DIM}refreshes every 5s — Ctrl+C to stop{RESET}\n")

    return "\n".join(out)


# ── Entry ──────────────────────────────────────────────────────────────────────

def main():
    dev_root = None
    once = "--once" in sys.argv

    for arg in sys.argv[1:]:
        if arg == "--dev":
            # auto-detect root from script location: scripts/ is one level inside
            dev_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        elif arg.startswith("--root="):
            dev_root = os.path.expanduser(arg[7:])

    if once:
        print(render(dev_root))
        return

    try:
        while True:
            output = render(dev_root)
            os.system("clear")
            print(output)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


if __name__ == "__main__":
    main()
