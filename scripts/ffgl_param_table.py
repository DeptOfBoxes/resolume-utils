#!/usr/bin/env python3
"""
FFGL Param Table — discover all params for a named FFGL plugin on a Resolume clip.

Outputs a structured JSON table the deep-analysis sub-agents must use as ground
truth. The agent does not discover anything itself — it reads this table and
addresses params by id.

Usage:
  python3 ffgl_param_table.py --plugin "FeedBox" --layer 2 --clip 13
  python3 ffgl_param_table.py --plugin "FeedBox" --auto    # use selected clip
  python3 ffgl_param_table.py --plugin "FeedBox" --auto --out /tmp/feedbox.json

Output schema:
  {
    "plugin": "FeedBox",
    "layer": 2,
    "clip": 13,
    "clip_name": "CubePortModel",
    "effect_index": 5,
    "param_count": 81,
    "captured_at": "2026-05-10T03:42:00",
    "params": [
      {
        "key": "FB Opacity",
        "id": 1778406152796,
        "valuetype": "ParamRange",
        "min": 0.0,
        "max": 1.0,
        "value": 1.0,
        "options": null,
        "group": "FB"        # heuristic: leading prefix before first space
      },
      ...
    ]
  }
"""

import argparse, json, sys, urllib.request, urllib.error
from datetime import datetime

API = "http://127.0.0.1:8080/api/v1"


def fetch_json(path):
    try:
        with urllib.request.urlopen(API + path, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.URLError as e:
        sys.exit(f"REST unreachable at {API}{path}: {e}")


def find_selected_clip(comp):
    for li, layer in enumerate(comp.get("layers", []), start=1):
        for ci, clip in enumerate(layer.get("clips", []), start=1):
            sel = clip.get("selected")
            if isinstance(sel, dict) and sel.get("value"):
                return li, ci
    sys.exit("no selected clip found")


def extract_param(key, p):
    if not isinstance(p, dict):
        return None
    return {
        "key": key,
        "id": p.get("id"),
        "valuetype": p.get("valuetype"),
        "min": p.get("min"),
        "max": p.get("max"),
        "value": p.get("value"),
        "options": p.get("options"),
        "group": key.split(" ", 1)[0] if " " in key else key,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plugin", required=True, help="Effect display name (e.g. 'FeedBox')")
    ap.add_argument("--layer", type=int, help="Layer number (1-indexed)")
    ap.add_argument("--clip", type=int, help="Clip number (1-indexed)")
    ap.add_argument("--auto", action="store_true", help="Auto-detect selected clip")
    ap.add_argument("--out", help="Write JSON to this path instead of stdout")
    args = ap.parse_args()

    if not args.auto and (args.layer is None or args.clip is None):
        sys.exit("must pass --auto OR both --layer and --clip")

    comp = fetch_json("/composition")
    if args.auto:
        li, ci = find_selected_clip(comp)
    else:
        li, ci = args.layer, args.clip

    try:
        clip = comp["layers"][li - 1]["clips"][ci - 1]
    except (IndexError, KeyError):
        sys.exit(f"clip layer={li} clip={ci} not found")

    clip_name = clip.get("name", {}).get("value", "?") if isinstance(clip.get("name"), dict) else "?"
    fx_list = clip.get("video", {}).get("effects", [])
    if not isinstance(fx_list, list):
        sys.exit(f"unexpected effects shape on clip layer={li} clip={ci}")

    matched_index = None
    matched_fx = None
    for i, fx in enumerate(fx_list):
        if not isinstance(fx, dict):
            continue
        nm = fx.get("name", "?")
        if isinstance(nm, dict):
            nm = nm.get("value", "?")
        if str(nm).strip().lower() == args.plugin.strip().lower():
            matched_index = i
            matched_fx = fx
            break
    if matched_fx is None:
        names = []
        for fx in fx_list:
            nm = fx.get("name", "?") if isinstance(fx, dict) else "?"
            if isinstance(nm, dict):
                nm = nm.get("value", "?")
            names.append(str(nm))
        sys.exit(f"plugin {args.plugin!r} not found on clip layer={li} clip={ci}. Effects present: {names}")

    params_blob = matched_fx.get("params", {})
    params_out = []
    if isinstance(params_blob, dict):
        for k, p in params_blob.items():
            row = extract_param(k, p)
            if row and row["id"] is not None:
                params_out.append(row)
    elif isinstance(params_blob, list):
        for i, p in enumerate(params_blob):
            row = extract_param(p.get("name", f"#{i}") if isinstance(p, dict) else f"#{i}", p)
            if row and row["id"] is not None:
                params_out.append(row)

    out = {
        "plugin": args.plugin,
        "layer": li,
        "clip": ci,
        "clip_name": clip_name,
        "effect_index": matched_index,
        "param_count": len(params_out),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "api": API,
        "params": params_out,
    }

    text = json.dumps(out, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {len(params_out)} params to {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
