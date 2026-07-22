#!/usr/bin/env bash
# Thin wrapper — cockpit is the real tool (start/stop/restart/update/status/logs).
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cockpit" uninstall "$@"
