#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Flush DNS cache on Linux or macOS (document ITOps-FlushDns). Runs as root.
set -u

case "$(uname -s)" in
  Darwin)
    dscacheutil -flushcache
    killall -HUP mDNSResponder 2>/dev/null
    echo "macOS DNS cache flushed (dscacheutil + mDNSResponder)."
    scutil --dns | awk '/nameserver\[[0-9]+\]/ {print "  DNS server: " $3}' | sort -u
    ;;
  Linux)
    flushed=0
    if command -v resolvectl >/dev/null 2>&1 && systemctl is-active --quiet systemd-resolved; then
      resolvectl flush-caches && echo "systemd-resolved cache flushed." && flushed=1
    fi
    if command -v nscd >/dev/null 2>&1; then nscd -i hosts 2>/dev/null && echo "nscd hosts cache flushed." && flushed=1; fi
    if systemctl is-active --quiet dnsmasq 2>/dev/null; then systemctl restart dnsmasq && echo "dnsmasq restarted." && flushed=1; fi
    [ "$flushed" -eq 0 ] && echo "No local DNS cache service found (nothing to flush)."
    grep -E '^nameserver' /etc/resolv.conf | sed 's/^/  /'
    ;;
esac

failed=0
for name in www.microsoft.com login.microsoftonline.com; do
  if [ "$(uname -s)" = "Darwin" ]; then
    ip=$(dscacheutil -q host -a name "$name" | awk '/ip_address/ {print $2; exit}')
  else
    ip=$(getent ahostsv4 "$name" | awk '{print $1; exit}')
  fi
  if [ -n "$ip" ]; then echo "OK    $name -> $ip"; else echo "FAIL  $name"; failed=$((failed + 1)); fi
done
[ "$failed" -gt 0 ] && { echo "$failed lookup(s) still failing - network or DNS server problem."; exit 2; }
exit 0
