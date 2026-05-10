#!/usr/bin/env python3
"""
FFGL Sweep Helper — atomic write+verify and screenshot-diff for deep-analysis sub-agents.

The sweep agent is REQUIRED to use this for every param write. It makes
fabrication mechanically impossible: writes go through REST (not OSC, which
snaps back on FFGL params), and read-back verification halts the run if a
write does not stick.

Subcommands:

  write    Atomic PUT + GET + verify. Halts on mismatch.
  verify   Confirm a screenshot file exists and compute dhash delta vs previous.
  read     Read current value of a param (no write).
  capture  Screenshot a specific window by id (macOS screencapture). Required
           when multiple Arena windows share a title — MCP get_resolume_screenshot
           cannot disambiguate by id. Pre-flight discovers the right window id
           via the MCP list_resolume_windows tool.

WRITE example:
  python3 ffgl_sweep_helper.py write \\
      --table /tmp/feedbox_params.json \\
      --name "FB Opacity" \\
      --value 0.5

  → Output (success):
    [WRITE_OK] name="FB Opacity" id=1778406152796 wrote=0.500 read=0.500 delta=0.000

  → Output (failure — value snapped back):
    [WRITE_FAILED] name="FB Opacity" id=1778406152796 wrote=0.500 read=1.000 delta=0.500
    HALT: param did not accept the write. Likely OSC-snapback or wrong endpoint.
    Do not continue the sweep. Report this to the parent session.

VERIFY example:
  python3 ffgl_sweep_helper.py verify \\
      --current /path/to/feedback_fb_opacity_050.png \\
      --previous /path/to/feedback_fb_opacity_025.png

  → Output:
    [VERIFIED] frame=feedback_fb_opacity_050.png phash_delta_from_prev=0.187
  or:
    [NO_CHANGE] frame=feedback_fb_opacity_050.png phash_delta_from_prev=0.000
    WARNING: visual identical to previous frame. Either param has no effect or
    the screenshot was not refreshed. Note this in the report; do not silently
    continue.

Exit codes:
  0  success / clean comparison
  2  WRITE_FAILED — value did not stick
  3  file missing or invalid
  4  REST unreachable
"""

import argparse, json, os, subprocess, sys, urllib.request, urllib.error
from PIL import Image

API_DEFAULT = "http://127.0.0.1:8080/api/v1"
TOLERANCE = 1e-3  # absolute tolerance for read-back float compare


def rest_get(url, timeout=4):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        print(f"[REST_ERROR] GET {url}: {e}", file=sys.stderr)
        sys.exit(4)


def rest_put(url, body, timeout=4):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except urllib.error.URLError as e:
        print(f"[REST_ERROR] PUT {url}: {e}", file=sys.stderr)
        sys.exit(4)


def lookup_param(table_path, name=None, param_id=None):
    if not os.path.isfile(table_path):
        sys.exit(f"[ERROR] table file not found: {table_path}")
    with open(table_path) as f:
        table = json.load(f)
    if param_id is not None:
        for p in table["params"]:
            if p["id"] == param_id:
                return p, table.get("api", API_DEFAULT)
        sys.exit(f"[ERROR] id {param_id} not in table {table_path}")
    if name is not None:
        for p in table["params"]:
            if p["key"].strip().lower() == name.strip().lower():
                return p, table.get("api", API_DEFAULT)
        sys.exit(f"[ERROR] name {name!r} not in table {table_path}. "
                 f"Available: {[p['key'] for p in table['params'][:8]]}...")
    sys.exit("[ERROR] must provide --id or --name")


def to_float(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_bool_arg(s):
    if isinstance(s, bool):
        return s
    s = str(s).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"cannot parse {s!r} as bool — use true/false")


def cmd_write(args):
    p, api = lookup_param(args.table, name=args.name, param_id=args.id)
    pid = p["id"]
    valuetype = p.get("valuetype", "")
    url = f"{api}/parameter/by-id/{pid}"

    if valuetype == "ParamChoice":
        choice = str(args.value)
        opts = p.get("options") or []
        if opts and choice not in opts:
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} valuetype=ParamChoice "
                  f"value={choice!r} not in options={opts}", file=sys.stderr)
            sys.exit(2)
        status = rest_put(url, {"value": choice})
        if status not in (200, 204):
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={choice!r} "
                  f"http_status={status}", file=sys.stderr)
            sys.exit(2)
        got = rest_get(url)
        read_back = str(got.get("value"))
        if read_back != choice:
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={choice!r} "
                  f"read={read_back!r}", file=sys.stderr)
            sys.exit(2)
        print(f"[WRITE_OK] name={p['key']!r} id={pid} wrote={choice!r} "
              f"read={read_back!r} valuetype=ParamChoice")
        return

    if valuetype == "ParamBoolean":
        try:
            written_bool = parse_bool_arg(args.value)
        except ValueError as e:
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} valuetype=ParamBoolean "
                  f"value={args.value!r} parse_error={e}", file=sys.stderr)
            sys.exit(2)
        status = rest_put(url, {"value": written_bool})
        if status not in (200, 204):
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={written_bool} "
                  f"http_status={status}", file=sys.stderr)
            sys.exit(2)
        got = rest_get(url)
        read_back = got.get("value")
        if not isinstance(read_back, bool):
            # Resolume returns Python bool here per observed behavior; be defensive
            try:
                read_back = parse_bool_arg(read_back)
            except ValueError:
                print(f"[WRITE_AMBIGUOUS] name={p['key']!r} id={pid} wrote={written_bool} "
                      f"read={got.get('value')!r} valuetype=ParamBoolean — cannot parse",
                      file=sys.stderr)
                sys.exit(2)
        if read_back != written_bool:
            print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={written_bool} "
                  f"read={read_back}", file=sys.stderr)
            print("HALT: bool param did not accept the write.", file=sys.stderr)
            sys.exit(2)
        print(f"[WRITE_OK] name={p['key']!r} id={pid} wrote={written_bool} "
              f"read={read_back} valuetype=ParamBoolean")
        return

    # Numeric path (ParamRange and similar)
    try:
        written = float(args.value)
    except ValueError:
        print(f"[WRITE_FAILED] name={p['key']!r} id={pid} valuetype={valuetype!r} "
              f"value={args.value!r} not numeric", file=sys.stderr)
        sys.exit(2)
    status = rest_put(url, {"value": written})
    if status not in (200, 204):
        print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={written:.3f} "
              f"http_status={status}", file=sys.stderr)
        sys.exit(2)

    got = rest_get(url)
    read_back = to_float(got.get("value"))
    if read_back is None:
        print(f"[WRITE_AMBIGUOUS] name={p['key']!r} id={pid} wrote={written:.3f} "
              f"read={got.get('value')!r} valuetype={got.get('valuetype')!r} "
              f"non-numeric — cannot verify", file=sys.stderr)
        sys.exit(2)

    delta = abs(read_back - written)
    if delta > TOLERANCE:
        print(f"[WRITE_FAILED] name={p['key']!r} id={pid} wrote={written:.3f} "
              f"read={read_back:.3f} delta={delta:.3f}", file=sys.stderr)
        print("HALT: param did not accept the write. Likely OSC-snapback, wrong "
              "endpoint, or value out of range. Do not continue the sweep. "
              "Report this to the parent session.", file=sys.stderr)
        sys.exit(2)

    print(f"[WRITE_OK] name={p['key']!r} id={pid} wrote={written:.3f} "
          f"read={read_back:.3f} delta={delta:.3f}")


def cmd_read(args):
    p, api = lookup_param(args.table, name=args.name, param_id=args.id)
    pid = p["id"]
    url = f"{api}/parameter/by-id/{pid}"
    got = rest_get(url)
    print(f"[READ] name={p['key']!r} id={pid} value={got.get('value')!r} "
          f"valuetype={got.get('valuetype')!r}")


def dhash(path, size=8):
    if not os.path.isfile(path):
        return None, f"file missing: {path}"
    try:
        img = Image.open(path).convert("L").resize((size + 1, size), Image.LANCZOS)
    except Exception as e:
        return None, f"image load failed ({path}): {e}"
    px = list(img.tobytes())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits, None


def hamming(a, b):
    return bin(a ^ b).count("1")


def cmd_capture(args):
    out = args.out
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    cmd = ["screencapture", "-l", str(args.window_id), "-o", "-t", "png", "-x", out]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"[CAPTURE_FAILED] window_id={args.window_id} returncode={result.returncode} "
              f"stderr={result.stderr.decode().strip()!r}", file=sys.stderr)
        sys.exit(5)
    if not os.path.isfile(out):
        print(f"[CAPTURE_FAILED] window_id={args.window_id} screencapture exited 0 but no "
              f"file at {out}. Window id may be stale (Arena restarted?), or Screen "
              f"Recording permission may not be granted to this process.", file=sys.stderr)
        sys.exit(5)
    nbytes = os.path.getsize(out)
    if nbytes < 1000:
        print(f"[CAPTURE_SUSPECT] window_id={args.window_id} out={os.path.basename(out)} "
              f"bytes={nbytes} — file is tiny, capture may have failed silently",
              file=sys.stderr)
        sys.exit(5)
    print(f"[CAPTURE_OK] window_id={args.window_id} out={os.path.basename(out)} "
          f"bytes={nbytes}")


def cmd_verify(args):
    h_cur, err = dhash(args.current)
    if err:
        print(f"[ERROR] {err}", file=sys.stderr)
        sys.exit(3)

    cur_size = os.path.getsize(args.current)
    short_cur = os.path.basename(args.current)

    if not args.previous:
        print(f"[VERIFIED] frame={short_cur} bytes={cur_size} "
              f"phash_delta_from_prev=N/A (first frame)")
        return

    h_prev, err = dhash(args.previous)
    if err:
        print(f"[ERROR] previous frame: {err}", file=sys.stderr)
        sys.exit(3)

    delta_bits = hamming(h_cur, h_prev)
    delta_norm = delta_bits / 64.0  # 64-bit dhash

    if delta_bits == 0:
        print(f"[NO_CHANGE] frame={short_cur} phash_delta_from_prev=0.000")
        print("WARNING: visual identical to previous frame. Either param has no "
              "effect, the screenshot was not refreshed, or the screenshot "
              "captured the wrong window. Note this in the report; do not "
              "silently continue.", file=sys.stderr)
    else:
        print(f"[VERIFIED] frame={short_cur} phash_delta_from_prev={delta_norm:.3f} "
              f"(hamming={delta_bits}/64)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Atomic PUT + GET + verify")
    w.add_argument("--table", required=True, help="path to ffgl_param_table.py output JSON")
    g = w.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", help="param name from table (e.g. 'FB Opacity')")
    g.add_argument("--id", type=int, help="numeric param id")
    w.add_argument("--value", required=True, type=str,
                   help="value to write. Numeric (0..1) for ParamRange; 'true'/'false' for ParamBoolean")
    w.set_defaults(func=cmd_write)

    r = sub.add_parser("read", help="Read current param value")
    r.add_argument("--table", required=True)
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--name")
    g.add_argument("--id", type=int)
    r.set_defaults(func=cmd_read)

    c = sub.add_parser("capture", help="Screenshot a specific Arena window by id (macOS)")
    c.add_argument("--window-id", required=True, type=int,
                   help="CGWindowID from list_resolume_windows MCP call")
    c.add_argument("--out", required=True, help="output png path")
    c.set_defaults(func=cmd_capture)

    v = sub.add_parser("verify", help="Confirm screenshot exists and compute dhash delta")
    v.add_argument("--current", required=True, help="path to current screenshot")
    v.add_argument("--previous", help="path to previous screenshot (optional, first frame only)")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
