#!/bin/bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LABEL="com.t3code.discord-presence"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON=""
for candidate in "$(type -P python3 || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
done
if [ -z "$PYTHON" ] || [ ! -f "$DIR/t3_presence.py" ]; then
  echo "Could not find python3 or $DIR/t3_presence.py" >&2
  exit 1
fi

ENV_XML=""
if [ -n "${T3CODE_HOME:-}" ]; then
  T3CODE_HOME="$("$PYTHON" -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$T3CODE_HOME")"
  ESCAPED="$(printf '%s' "$T3CODE_HOME" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')"
  ENV_XML="<key>EnvironmentVariables</key><dict><key>T3CODE_HOME</key><string>$ESCAPED</string></dict>"
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$DIR/t3_presence.py</string>
  </array>
  $ENV_XML
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/presence.log</string>
  <key>StandardErrorPath</key><string>$DIR/presence.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Logs: $DIR/presence.log"
