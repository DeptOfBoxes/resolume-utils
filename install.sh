#!/bin/bash
# Install CubePort Status as a login item via LaunchAgent.
# Fills __PYTHON__ and __SCRIPT__ in the plist template, copies it to
# ~/Library/LaunchAgents/, and loads it immediately.
set -e

REPO="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$REPO/scripts/cubeport_status_ui.py"
PLIST_SRC="$REPO/launch_agent/com.deptofboxes.cubeport-status.plist"
PLIST_LABEL="com.deptofboxes.cubeport-status"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# ── Preflight ──────────────────────────────────────────────────────────────────
if [ ! -f "$SCRIPT" ]; then
  echo "ERROR: script not found at $SCRIPT"
  exit 1
fi

PYTHON=$(which python3 2>/dev/null || true)
if [ -z "$PYTHON" ]; then
  echo "ERROR: python3 not found in PATH"
  exit 1
fi

if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
  echo "ERROR: python3 at $PYTHON does not have tkinter"
  echo "  Install via Homebrew: brew install python-tk"
  exit 1
fi

# ── Stop existing agent if running ────────────────────────────────────────────
if launchctl list "$PLIST_LABEL" &>/dev/null; then
  echo "Stopping existing agent…"
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

# Kill any stray UI process left over
pkill -f "cubeport_status_ui" 2>/dev/null || true

# ── Write filled-in plist ─────────────────────────────────────────────────────
mkdir -p "$HOME/Library/LaunchAgents"
sed \
  -e "s|__PYTHON__|$PYTHON|g" \
  -e "s|__SCRIPT__|$SCRIPT|g" \
  "$PLIST_SRC" > "$PLIST_DEST"

echo "Wrote $PLIST_DEST"

# ── Load ──────────────────────────────────────────────────────────────────────
launchctl load "$PLIST_DEST"
echo "Loaded $PLIST_LABEL"

# Brief pause then confirm
sleep 1
if launchctl list "$PLIST_LABEL" &>/dev/null; then
  echo ""
  echo "✓ CubePort Status will now auto-launch with Resolume Arena."
  echo "  Log: /tmp/cubeport-status.log"
  echo "  To uninstall: ./uninstall.sh"
else
  echo "WARN: agent loaded but not appearing in launchctl list — check /tmp/cubeport-status.log"
fi
