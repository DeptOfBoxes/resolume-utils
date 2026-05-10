# FFGL Deep Analysis Protocol

Reusable scaffolding for sub-agent passes that systematically exercise every
parameter of an FFGL plugin in Resolume and report what happens. Encodes the
lessons from the 2026-05-10 FeedBox Pass 1 hallucination incident.

## The hallucination failure mode (what this protocol prevents)

A sub-agent given a sweep task without scaffolding will:
1. Issue param writes via the MCP `set_param` tool (which routes through OSC)
2. Watch them snap back instantly because **OSC writes to FFGL params do not
   stick in Resolume Arena 7.x** (see `feedback_ffgl_osc_params_snapback.md`)
3. Not verify the write landed
4. Not notice the visual is unchanged
5. Confabulate a fluent-sounding report by paraphrasing the plugin's own
   handoff/spec docs and inventing per-position observations
6. Fabricate technical-sounding identifiers (`addr: fbopacity` etc.) that don't
   exist in the source

The countermeasures below make each step mechanically impossible.

## Mandatory tools

All sub-agents doing FFGL deep analysis MUST use these and ONLY these for
param interaction:

- `ffgl_param_table.py` — pre-flight, fetches the canonical param table
- `ffgl_sweep_helper.py write`   — every param change goes through this
  - Supports ParamRange (`--value 0.5`), ParamBoolean (`--value true|false`),
    ParamChoice (`--value "Option String"` — must match an `options[]` entry).
  - Validates type from the param table and rejects mismatches with exit 2.
- `ffgl_sweep_helper.py read`    — explicit reads
- `ffgl_sweep_helper.py capture` — every screenshot goes through this
- `ffgl_sweep_helper.py verify`  — every screenshot gets dhash-diffed

The MCP `set_param` tool is **BANNED** for FFGL plugins. It uses OSC and the
write will silently snap back. The agent must not call it.

The MCP `get_resolume_screenshot` tool is **BANNED** for sweep captures. It
picks the largest Arena window when multiple share a title, which is usually
the main UI (showing only a tiny preview thumbnail of the actual output).
Using it caused a halt+restart on the 2026-05-10 v2 run. Use
`ffgl_sweep_helper.py capture --window-id N` instead, with the id of the
pop-out Output / Composition Monitor window — discovered ONCE at pre-flight
via the MCP `list_resolume_windows` tool.

## Pre-flight (do once, before sweeping)

```bash
# 1. Confirm REST is alive
curl -s --max-time 2 http://127.0.0.1:8080/api/v1/composition | head -c 50

# 2. Confirm the REST health monitor is running (catches drops mid-sweep)
pgrep -fl rest_health_monitor

# 3. Fetch the param table for the target plugin
python3 ~/PycharmProjects/resolume-utils/scripts/ffgl_param_table.py \
    --plugin "PluginName" --auto \
    --out /tmp/PluginName_params.json

# 4. Create a captures directory (deterministic location, plugin-namespaced)
mkdir -p /tmp/sweep_captures/PluginName

# 5. Discover the capture window id. Call MCP list_resolume_windows. You will
#    typically see TWO Arena windows with identical titles:
#      id=A  ~1728×1084  ← main UI (shows only a tiny output thumbnail)
#      id=B  ~1565×1084  ← pop-out Output / Composition Monitor (~95% FFGL pixels)
#    Pick the smaller window (the pop-out). Save its id:
echo <window_id_of_popout> > /tmp/PluginName_capture_window.txt

#    Verify the right window was picked by capturing once and inspecting:
python3 ~/PycharmProjects/resolume-utils/scripts/ffgl_sweep_helper.py capture \
    --window-id $(cat /tmp/PluginName_capture_window.txt) \
    --out /tmp/sweep_captures/PluginName/PREFLIGHT_TEST.png
#    Then read that file and confirm it shows the FFGL output, NOT the Arena UI.
#    If it shows the Arena UI grid/parameter panels, you picked the wrong window.

# 6. Snapshot the starting state of every numeric param so it can be restored
#    (read each, save the value list to /tmp/PluginName_starting_state.json)
```

## Sweep loop (per param, per knob position)

The agent MUST follow this exact sequence for every observation:

```bash
# 1. Write — halts the run if the value doesn't stick
python3 ffgl_sweep_helper.py write \
    --table /tmp/PluginName_params.json \
    --name "FB Opacity" \
    --value 0.50

# Expected: [WRITE_OK] name='FB Opacity' id=... wrote=0.500 read=0.500 delta=0.000
# Failure:  [WRITE_FAILED] ... → exit code 2 → STOP, do not proceed

# 2. Wait for the visual to settle (feedback engines need time)
sleep 0.4

# 3. Capture screenshot via helper (NOT MCP — see "Mandatory tools")
python3 ffgl_sweep_helper.py capture \
    --window-id $(cat /tmp/PluginName_capture_window.txt) \
    --out /tmp/sweep_captures/PluginName/feedback_fb_opacity_050.png
# Path convention: {group}_{paramname_snake}_{position3digit}.png

# 4. Verify the screenshot exists AND differs from the previous one in this sweep
python3 ffgl_sweep_helper.py verify \
    --current  /tmp/sweep_captures/PluginName/feedback_fb_opacity_050.png \
    --previous /tmp/sweep_captures/PluginName/feedback_fb_opacity_025.png

# Expected: [VERIFIED] frame=... phash_delta_from_prev=0.187
# Suspect:  [NO_CHANGE] phash_delta_from_prev=0.000 → log this, do not silently continue
# Error:    file missing → exit 3 → STOP
```

## Halt conditions — no silent workarounds

The agent MUST stop and report to the parent session if any of these occur:

- `[WRITE_FAILED]` from the helper (param did not accept the write)
- Three consecutive `[NO_CHANGE]` results across different params (means the
  screenshot tool is capturing a stale or wrong window)
- REST health monitor reports a drop
- Any unexpected helper exit code

**Do not "find a workaround" silently.** The previous agent's fatal failure
was attempting to compensate for a broken write path by inventing observations.

## Anti-hallucination rules for the report

### Banned vocabulary

The agent must NOT use any of these terms — they are markers of confabulation
because they appeared in the source spec docs the agent had access to:

`sweet zone`, `dead zone`, `breakage`, `dreamy`, `watercolor`, `cinematic`,
`sigmoid`, `log curve`, `bipolar`, `identity`, `wash`, `chaos`, `psychedelic`,
`mandala`, `trash`, `amazing`, `nuance lives here`, `breathes`, `chunky`

These are subjective/spec-paraphrased terms. Replace with concrete visual
observations: pixel-level color, geometry, motion direction, density,
edge sharpness, frame coverage, contrast.

### Required structured-line evidence

Every per-position observation in the report MUST be preceded by the literal
helper output for that position, pasted verbatim:

```
[WRITE_OK] name='FB Opacity' id=1778406152796 wrote=0.500 read=0.500 delta=0.000
[VERIFIED] frame=feedback_fb_opacity_050.png phash_delta_from_prev=0.187 (hamming=12/64)
Observation: at FB Opacity 0.50, the orange-tinted ghost rings from the
prior CubePort outline now persist for ~5 frames before fading. Compared to
0.25, the ring count visible at any moment increased from ~2 to ~5.
```

Observations without preceding `[WRITE_OK]` and `[VERIFIED]` lines are
inadmissible.

### Required uncertainty quotas

Each parameter group MUST contain at least ONE of:
- An `[NO_CHANGE]` event (visual didn't differ between sweep positions)
- An "I cannot tell what this does" entry
- An "interaction with another param obscures clean reading" entry

A group report with zero uncertainty across all params is a hallucination
flag. Real sweeps always surface something the agent can't read cleanly.

## Scope discipline

Start with ONE group (4–8 params) as proof-of-method. Bring the report back
to the parent session before expanding. This catches scaffolding failures
early — if helper or screenshots break on group 1, you find out after 8
sweeps, not 80.

Suggested first group for any plugin: the lowest-prefix or "core" group
(usually feedback / opacity / mix / amount-style master controls) since
these have the most legible visual effect.

## Restoring state

At the end of every sweep session, restore the starting state:

```bash
# Replay every write from /tmp/PluginName_starting_state.json through
# ffgl_sweep_helper.py write, in original order.
```

Do not leave the live clip in arbitrary mid-sweep state — Tim may be
performing or testing other things on the same comp.

## Output location convention

```
{plugin_dir}/DEEP_ANALYSIS_PASS{N}_DATA_{date}.md
{plugin_dir}/DEEP_ANALYSIS_PASS{N}_CAPTURES_{date}/   # symlink or copy
```

The data file references screenshot paths relative to the captures dir so
the parent session can audit any frame.

## Sub-agent prompt boilerplate

Every FFGL deep-analysis sub-agent prompt MUST include this paragraph
verbatim:

> You are operating under the FFGL Deep Analysis Protocol at
> `~/PycharmProjects/resolume-utils/scripts/DEEP_ANALYSIS_PROTOCOL.md`. Read
> it in full BEFORE doing anything. The MCP `set_param` tool is BANNED for
> this work — it uses OSC, and OSC writes to FFGL params snap back instantly,
> which has caused fabricated reports in the past. Every param change must
> go through `ffgl_sweep_helper.py write` and you must paste the helper's
> verbatim output into your report. If the helper exits non-zero, you STOP
> and report — you do not invent observations.

## Lessons encoded (provenance)

- 2026-05-10 — FeedBox Pass 1 hallucination → this entire protocol
- `feedback_ffgl_osc_params_snapback.md` — REST-only rule for FFGL writes
- `feedback_no_destructive_defaults.md` — restore starting state at end
- `feedback_check_arena_log_and_size_asserts.md` — REST health monitor in pre-flight
