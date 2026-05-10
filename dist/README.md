# REST Health Monitor

A real-time health monitor for the Resolume Arena / Avenue / Wire REST API.

Surfaces the root causes behind "nothing works" symptoms in under 5 seconds —
before you spend an hour debugging the wrong thing.

---

## What it checks

| Check | Why it matters |
|---|---|
| **Arena process** | Confirms Resolume is actually running |
| **Port owner** | Detects when a foreign process has grabbed :8080 and blocked Arena's webserver |
| **Listen address** | Shows whether the webserver is localhost-only or network-accessible — remote tools (Companion, TouchDesigner, QLab) silently fail on localhost-only |
| **REST HTTP probe** | `GET /api/v1/composition` — confirms the API is live and returns composition structure |
| **WebSocket probe** | Attempts a `101 Switching Protocols` upgrade — most real-time integrations depend on WS |
| **Response latency** | Flags slow `/composition` responses (use `/parameter/by-id/` for real-time reads) |
| **Arena version** | Surfaces known version-gated issues: webserver crash fix (7.24+), animation params + monitors endpoint (7.26+) |
| **`/api/docs/rest/`** | Confirms the Swagger UI is reachable |
| **`/monitors/` endpoint** | Checks for 7.26+ output snapshot capability |
| **"API" folder collision** | A folder literally named `API` in `~/Documents/Resolume Arena/` silently breaks the REST API — known forum issue |
| **Arena log panics** | Scans for recent FFGL panics and size-assert failures |
| **Plugin bundles** | Optional: checks that your FFGL plugin bundles are installed (configure via `plugins.json`) |

---

## Requirements

- macOS or Windows
- Python 3 with tkinter
  - macOS: `brew install python-tk` if tkinter is missing
  - Windows: install from [python.org](https://python.org) — **not** the Microsoft Store version, which omits tkinter
- Resolume Arena 7.8 or later

---

## Usage

**Floating panel (auto-launches with Arena):**
```bash
python3 rest_health_monitor_ui.py
```
The window hides when Arena is not running and appears automatically when Arena starts.

**CLI — live watch:**
```bash
python3 rest_health_monitor.py
```

**CLI — single snapshot:**
```bash
python3 rest_health_monitor.py --once
```

**Wire mode (port 8081):**
```bash
python3 rest_health_monitor.py --port 8081
python3 rest_health_monitor_ui.py --port 8081
```

---

## Auto-launch with login

**macOS:**
```bash
bash install.sh    # installs LaunchAgent, starts immediately
bash uninstall.sh  # removes LaunchAgent and stops the process
```
Log: `/tmp/cubeport-status.log`

**Windows:**
```
install_windows.bat    # creates a Task Scheduler task, runs at each login
uninstall_windows.bat  # removes the task and kills any running instance
```
Run as Administrator if the installer reports a permission error.

---

## Plugin bundle checking

Create a `plugins.json` file in the same directory as the scripts:

```json
[
  {"bundle": "MyPlugin", "dylib": "libmyplugin.dylib"},
  {"bundle": "AnotherPlugin", "dylib": "libanotherplugin.dylib"}
]
```

`bundle` is the `.bundle` folder name in `Extra Effects`.
`dylib` is optional — only needed with `--dev` for sha-drift checking.

See `plugins.json.example` for the format.

---

## Common issues the monitor detects

**`:8080` held by a foreign process**
A long-running child process (often an FFGL plugin's companion app) can inherit
Arena's REST socket and hold it after Arena restarts. The monitor names the PID
so you can kill it immediately: `kill <PID>`, then restart Arena.

**Webserver preference off**
Arena's REST API is disabled by default. Enable it:
`Arena → Preferences → Webserver → enable, port 8080`

**`127.0.0.1` vs `0.0.0.0`**
If the listen address shows `127.0.0.1`, remote tools on other machines or VMs
cannot reach the API. Change the listen address in Arena Preferences to `0.0.0.0`
or your machine's local IP.

**"API" folder collision**
If you have a folder named `API` anywhere inside `~/Documents/Resolume Arena/`,
Resolume silently fails to serve the REST API. Rename or remove it.

**Slow `/composition` responses**
The full composition dump is 10–100× slower than individual parameter reads.
For real-time integrations, use `GET /parameter/by-id/{id}` instead of polling
`/composition`.

**Read-only fields on PUT**
`GET /composition` includes a `selected` field. Sending it back in a PUT payload
fails with `"error reading field 'selected': This field is read-only"`.
Strip read-only fields before PUT.

**Clip positions go stale after drag-swap**
If you reorder clips in the Resolume UI, any code addressing clips by grid position
gets out of sync. Address clips by their unique ID instead.

**Wire uses port 8081**
Resolume Wire listens on 8081, not 8080. Use `--port 8081` for Wire.

---

## License

MIT
