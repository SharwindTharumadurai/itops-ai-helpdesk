#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Antivirus scan on Linux or macOS (document ITOps-AntivirusScan). Runs as root.
# Prefers Microsoft Defender for Endpoint (mdatp), falls back to ClamAV.
# Exit 0 = clean, 1 = threats found, 3 = no on-demand scanner installed.
SCAN_TYPE='{{ ScanType }}'
set -u

if command -v mdatp >/dev/null 2>&1; then
  mdatp definitions update >/dev/null 2>&1 && echo "Defender definitions updated."
  if [ "$SCAN_TYPE" = "FullScan" ]; then mdatp scan full; else mdatp scan quick; fi
  threats=$(mdatp threat list 2>/dev/null | grep -c 'Id:' || true)
  echo "Defender threat list entries: $threats"
  [ "$threats" -gt 0 ] && exit 1
  exit 0
fi

clam=""
for c in clamscan /opt/homebrew/bin/clamscan /usr/local/bin/clamscan; do command -v "$c" >/dev/null 2>&1 && clam=$(command -v "$c") && break; done
if [ -n "$clam" ]; then
  command -v freshclam >/dev/null 2>&1 && freshclam --quiet 2>/dev/null
  if [ "$SCAN_TYPE" = "FullScan" ]; then
    targets="/"
  elif [ "$(uname -s)" = "Darwin" ]; then
    targets="/Users /private/tmp /Applications"
  else
    targets="/home /tmp /var/tmp /root"
  fi
  # shellcheck disable=SC2086
  "$clam" -r -i --no-summary --exclude-dir='^/(proc|sys|dev|run|System/Volumes)' $targets
  rc=$?
  case $rc in 0) echo "ClamAV: clean."; exit 0 ;; 1) echo "ClamAV: INFECTED files found (listed above)."; exit 1 ;; *) echo "ClamAV error ($rc)"; exit 2 ;; esac
fi

if [ "$(uname -s)" = "Darwin" ]; then
  echo "No on-demand scanner installed. macOS XProtect runs automatically; version info:"
  system_profiler SPInstallHistoryDataType 2>/dev/null | grep -A3 'XProtect' | tail -4
fi
echo "Install Microsoft Defender for Endpoint or ClamAV to enable on-demand scans."
exit 3
