# resolume-utils

Resolume utility scripts: FFGL parameter sweep helpers, REST health monitor, and a LaunchAgent that runs the monitor in the background.

## Status
Active.

## Stack
- Python 3
- LaunchAgent (macOS)

## Layout
- `scripts/`
  - `ffgl_param_table.py`, `ffgl_sweep_helper.py` — FFGL param introspection
  - `rest_health_monitor.py`, `rest_health_monitor_ui.py` — Resolume REST monitor
  - `plugins.json`, `DEEP_ANALYSIS_PROTOCOL.md`
- `launch_agent/com.deptofboxes.cubeport-status.plist` — LaunchAgent
- `install.sh`, `uninstall.sh`
- `dist/` — packaged output

## How to run
```
./install.sh         # install LaunchAgent
./uninstall.sh
# manual:
python scripts/rest_health_monitor.py
```

## Related
Companion to `[[project_cubeport_modular_ecosystem]]`.
