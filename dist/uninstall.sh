#!/bin/bash
# Remove CubePort Status LaunchAgent and stop the running process.
set -e

PLIST_LABEL="com.deptofboxes.cubeport-status"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

if launchctl list "$PLIST_LABEL" &>/dev/null; then
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
  echo "Unloaded $PLIST_LABEL"
else
  echo "Agent not loaded (nothing to unload)"
fi

if [ -f "$PLIST_DEST" ]; then
  rm "$PLIST_DEST"
  echo "Removed $PLIST_DEST"
fi

pkill -f "rest_health_monitor_ui" 2>/dev/null && echo "Stopped running process" || true

echo ""
echo "✓ CubePort Status uninstalled."
