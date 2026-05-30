#!/usr/bin/env python3
"""osc_rest_bridge.py — drive Resolume FFGL params from RecursionEngine audio OSC.

Subscribes to RecursionEngine's analysis OSC namespace (/borealis/*) and writes
the values onto Resolume FFGL params via the REST API. This is the beat-reactive
CubePort/FeedBox bridge proven WITHOUT touching any plugin internals: audio →
OSC → (this driver) → REST param writes → the model/effect reacts.

WHY REST, NOT OSC-INTO-RESOLUME:
  resolume_bridge.py in RecursionEngine writes to Resolume's own OSC input
  (port 7000). FFGL params driven that way SNAP BACK — Arena's OSC server holds
  an authoritative copy and re-broadcasts it, rolling the write back. REST PUT
  by-id goes through a separate HTTP handler that commits directly and is immune
  to snapback. (resolume-ai guardian, runtime-verified v7.23.2, 2026-05-26.)

VERIFIED CONTRACTS (disk, 2026-05-29):
  OSC in  — apps/RecursionEngine/borealis/output/osc_output.py:
    /borealis/tempo/phase  float 0..1   /borealis/tempo/bpm  float
    /borealis/energy/low|mid|high  floats   (publisher rate-limited 30 Hz)
  REST out — resolume-utils/scripts/ffgl_sweep_helper.py:
    base http://127.0.0.1:8080/api/v1 ; PUT /parameter/by-id/{id} {"value": v}
  Param table — produced by ffgl_param_table.py: {"params":[{"key","id","type","min","max"}]}

PARAM-ID STABILITY: Arena regenerates param ids on every restart / clip reload.
  Re-introspect each session (ffgl_param_table.py --out table.json), then this
  driver resolves each mapping's param_key -> current id + [min,max] at startup.
  Never cache ids across Arena sessions.

SILENT NO-OP GUARDS (resolume-ai): the target clip must be CONNECTED (not just
  previewed) or writes land but nothing renders; ParamChoice params take an int
  index; value mapping uses the param's declared [min,max] from the table.

Usage:
  # 1. introspect the live target plugin (Arena running, clip connected):
  python3 ffgl_param_table.py --plugin "FeedBox" --auto --out /tmp/feedbox.json
  # 2. write a mapping config (see osc_rest_bridge.example.json), then run:
  python3 osc_rest_bridge.py --config bridge.json
  # verify the mapping math with no Arena and no OSC sender:
  python3 osc_rest_bridge.py --config bridge.json --selftest
  # watch intended writes without touching Arena:
  python3 osc_rest_bridge.py --config bridge.json --dry-run

Config (JSON):
  {
    "osc":   {"host": "127.0.0.1", "port": 7100},
    "rest":  {"api": "http://127.0.0.1:8080/api/v1"},
    "param_table": "/tmp/feedbox.json",
    "rate_limit_hz": 20.0,
    "mappings": [
      {"osc": "/borealis/tempo/phase", "param_key": "FB Opacity"},
      {"osc": "/borealis/energy/low",  "param_key": "FB Scale", "in": [0.0, 1.0], "out": [0.5, 2.0]}
    ]
  }
  - "in"  defaults to [0.0, 1.0] (the /borealis/* values are already normalized).
  - "out" defaults to the param's [min,max] from the table.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REST_TIMEOUT = 4
TOLERANCE = 1e-3  # param-space epsilon, mirrors ffgl_sweep_helper.py


# ── REST (mirrors ffgl_sweep_helper.rest_put/rest_get; kept local to stay self-contained) ──
def rest_put(url: str, body: dict, timeout: int = REST_TIMEOUT) -> int | None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="PUT", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except urllib.error.URLError as e:
        print(f"[REST_ERROR] PUT {url}: {e}", file=sys.stderr)
        return None


# ── value mapping ──
def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def map_value(raw: float, in_range, out_range, is_int: bool):
    """Clamp raw to in_range, normalize, scale to out_range, clamp, int-round if needed."""
    in_lo, in_hi = in_range
    out_lo, out_hi = out_range
    raw = _clamp(float(raw), min(in_lo, in_hi), max(in_lo, in_hi))
    span = (in_hi - in_lo) or 1.0
    t = (raw - in_lo) / span
    out = out_lo + t * (out_hi - out_lo)
    out = _clamp(out, min(out_lo, out_hi), max(out_lo, out_hi))
    return int(round(out)) if is_int else out


# ── resolve mappings against the param table ──
def load_param_table(path: Path) -> dict:
    table = json.loads(Path(path).read_text(encoding="utf-8"))
    by_key = {}
    for p in table.get("params", []):
        if p.get("key") is not None and p.get("id") is not None:
            by_key[p["key"]] = p
    return by_key


def build_routes(config: dict, by_key: dict) -> list[dict]:
    routes = []
    for m in config.get("mappings", []):
        key = m["param_key"]
        p = by_key.get(key)
        if p is None:
            raise SystemExit(
                f"[ERROR] param_key {key!r} not in table. Available: {list(by_key)[:8]}..."
            )
        ptype = str(p.get("type", "")).lower()
        is_int = "choice" in ptype or "int" in ptype
        pmin = float(p.get("min", 0.0))
        pmax = float(p.get("max", 1.0))
        routes.append(
            {
                "osc": m["osc"],
                "param_key": key,
                "param_id": p["id"],
                "in_range": tuple(m.get("in", [0.0, 1.0])),
                "out_range": tuple(m.get("out", [pmin, pmax])),
                "is_int": is_int,
            }
        )
    return routes


# ── the bridge ──
class OscRestBridge:
    def __init__(self, routes, api: str, rate_limit_hz: float, *, dry_run: bool):
        self.api = api.rstrip("/")
        self.dry_run = dry_run
        self._min_interval = 1.0 / rate_limit_hz if rate_limit_hz > 0 else 0.0
        self._by_addr = {}
        for r in routes:
            self._by_addr.setdefault(r["osc"], []).append(r)
        self._last_sent = {}   # param_id -> ts
        self._last_val = {}    # param_id -> last written param-space value
        self.writes = 0
        self.skipped_rate = 0
        self.skipped_eps = 0

    def now(self) -> float:
        return time.monotonic()

    def handle(self, address: str, *args) -> list[tuple]:
        """Process one OSC message; returns the list of (param_id, value) writes performed."""
        routes = self._by_addr.get(address)
        if not routes or not args:
            return []
        raw = args[0]
        done = []
        for r in routes:
            val = map_value(raw, r["in_range"], r["out_range"], r["is_int"])
            pid = r["param_id"]
            # epsilon gate (param-space)
            prev = self._last_val.get(pid)
            if prev is not None and abs(val - prev) < TOLERANCE:
                self.skipped_eps += 1
                continue
            # rate gate
            now = self.now()
            if self._min_interval and (now - self._last_sent.get(pid, 0.0)) < self._min_interval:
                self.skipped_rate += 1
                continue
            self._last_sent[pid] = now
            self._last_val[pid] = val
            if self.dry_run:
                print(f"[DRY] {address} {raw} -> param {pid} ({r['param_key']}) = {val}", flush=True)
            else:
                url = f"{self.api}/parameter/by-id/{pid}"
                status = rest_put(url, {"value": val})
                if status is not None and status >= 400:
                    print(f"[WARN] PUT {url} value={val} -> http {status}", file=sys.stderr, flush=True)
            self.writes += 1
            done.append((pid, val))
        return done


def run_server(bridge: OscRestBridge, host: str, port: int) -> None:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer

    disp = Dispatcher()

    # NB: python-osc >=1.10 treats a non-None handler return as an OSC reply to
    # send back to the sender — so the handler MUST return None or the server
    # tries to build a reply message from our writes list and errors per packet.
    def _on_osc(addr, *a):
        bridge.handle(addr, *a)

    disp.set_default_handler(_on_osc)
    server = ThreadingOSCUDPServer((host, port), disp)
    print(f"[osc_rest_bridge] listening OSC udp://{host}:{port} -> REST {bridge.api} "
          f"({'DRY-RUN' if bridge.dry_run else 'LIVE'})", flush=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print(f"\n[osc_rest_bridge] writes={bridge.writes} "
              f"rate-skipped={bridge.skipped_rate} eps-skipped={bridge.skipped_eps}")
        server.shutdown()   # clean flush — avoids Arena crash-on-exit from queued writes


def selftest(bridge: OscRestBridge, routes) -> int:
    """Inject synthetic OSC values through the mapping pipeline — no Arena, no sender."""
    print("[selftest] feeding synthetic values per mapped address:")
    samples = [0.0, 0.25, 0.5, 0.75, 1.0]
    addrs = sorted({r["osc"] for r in routes})
    for addr in addrs:
        for s in samples:
            bridge._last_val.clear()
            bridge._last_sent.clear()
            done = bridge.handle(addr, s)
            for pid, val in done:
                print(f"  {addr} in={s:<4} -> param {pid} = {val}")
    print(f"[selftest] OK — {bridge.writes} writes computed (dry={bridge.dry_run})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drive Resolume FFGL params from RecursionEngine OSC")
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--dry-run", action="store_true", help="Log intended writes, do not PUT")
    ap.add_argument("--selftest", action="store_true", help="Run mapping math on synthetic values and exit")
    args = ap.parse_args(argv)

    config = json.loads(args.config.read_text(encoding="utf-8"))
    by_key = load_param_table(Path(config["param_table"]))
    routes = build_routes(config, by_key)
    api = config.get("rest", {}).get("api", "http://127.0.0.1:8080/api/v1")
    rate = float(config.get("rate_limit_hz", 20.0))
    dry = args.dry_run or args.selftest

    bridge = OscRestBridge(routes, api, rate, dry_run=dry)
    print(f"[osc_rest_bridge] {len(routes)} route(s) resolved from {config['param_table']}")
    for r in routes:
        print(f"  {r['osc']} -> '{r['param_key']}' id={r['param_id']} "
              f"in={r['in_range']} out={r['out_range']} int={r['is_int']}")

    if args.selftest:
        return selftest(bridge, routes)

    osc = config.get("osc", {})
    run_server(bridge, osc.get("host", "127.0.0.1"), int(osc.get("port", 7100)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
