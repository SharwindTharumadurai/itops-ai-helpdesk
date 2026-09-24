#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Read-only health snapshot for Linux or macOS (document ITOps-CollectDiagnostics). Changes nothing.
set -u
section() { echo; echo "== $1 =="; }

section "System"
echo "$(hostname) | $(uname -srm)"
if [ "$(uname -s)" = "Darwin" ]; then
  sw_vers | tr '\n' ' '; echo
  echo "Model: $(sysctl -n hw.model) | RAM: $(( $(sysctl -n hw.memsize) / 1073741824 )) GB"
else
  [ -r /etc/os-release ] && . /etc/os-release && echo "${PRETTY_NAME:-unknown}"
  free -h | awk '/^Mem:/ {print "RAM: " $2 " total, " $3 " used, " $7 " available"}'
fi
echo "Uptime/load: $(uptime)"

section "Disks"
df -hP | awk 'NR==1 || $1 ~ /^\/dev\// {print}'

section "Top processes by CPU"
ps -Ao pid,pcpu,pmem,comm | sort -k2 -nr | head -6

section "Network"
if [ "$(uname -s)" = "Darwin" ]; then
  ifconfig | awk '/^[a-z]/ {i=$1} /inet / && $2 != "127.0.0.1" {print i " " $2}'
  route -n get default 2>/dev/null | awk '/gateway/ {print "gateway " $2}'
else
  ip -4 -o addr show scope global | awk '{print $2 " " $4}'
  ip route show default
fi
for h in login.microsoftonline.com outlook.office365.com; do
  if (exec 3<>"/dev/tcp/$h/443") 2>/dev/null; then echo "HTTPS $h: reachable"; else echo "HTTPS $h: UNREACHABLE"; fi
done

section "Security & updates"
if command -v mdatp >/dev/null 2>&1; then mdatp health --field real_time_protection_enabled 2>/dev/null | sed 's/^/Defender realtime: /'; fi
if [ "$(uname -s)" = "Darwin" ]; then
  softwareupdate --list 2>&1 | grep -E '^\s*\*' | head -10 || echo "No updates listed."
elif command -v apt-get >/dev/null 2>&1; then
  echo "Upgradable packages: $(apt list --upgradable 2>/dev/null | grep -vc '^Listing')"
  [ -f /var/run/reboot-required ] && echo "Reboot required: yes"
elif command -v dnf >/dev/null 2>&1; then
  echo "Pending updates: $(dnf -q check-update 2>/dev/null | grep -c '^[a-zA-Z0-9]')"
fi

section "Recent errors (last 24h, top 10)"
if [ "$(uname -s)" = "Darwin" ]; then
  log show --last 24h --predicate 'messageType == fault' --style compact 2>/dev/null | tail -10
else
  journalctl -p err --since "24 hours ago" --no-pager -q 2>/dev/null | tail -10
fi
exit 0
