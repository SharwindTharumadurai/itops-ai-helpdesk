#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Cancel stuck jobs and restart CUPS on Linux or macOS (document ITOps-RestartPrinting). Runs as root.
set -u
echo "Jobs before reset:"; lpstat -o 2>/dev/null || echo "  (none)"
cancel -a 2>/dev/null && echo "Cancelled all queued jobs."

if [ "$(uname -s)" = "Darwin" ]; then
  launchctl kickstart -k system/org.cups.cupsd && echo "cupsd restarted."
else
  systemctl restart cups && echo "cups restarted."
fi
sleep 2
lpstat -r || exit 1
lpstat -p 2>/dev/null
# Re-enable any printers CUPS disabled after errors.
for p in $(lpstat -p 2>/dev/null | awk '/disabled/ {print $2}'); do cupsenable "$p" && echo "Re-enabled $p"; done
exit 0
