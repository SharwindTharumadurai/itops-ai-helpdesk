#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Reset network interfaces on Linux or macOS (document ITOps-ResetNetwork). Runs as root.
# Soft: bounce active interfaces. Full: also restart the network service / flush DNS and routes cache.
MODE='{{ Mode }}'
set -u

wait_for_network() {
  for _ in $(seq 1 12); do
    sleep 5
    if [ "$(uname -s)" = "Darwin" ]; then
      dscacheutil -q host -a name www.microsoft.com | grep -q ip_address && return 0
    else
      getent ahostsv4 www.microsoft.com >/dev/null && return 0
    fi
  done
  return 1
}

case "$(uname -s)" in
  Darwin)
    networksetup -listallhardwareports | awk '/^Hardware Port:/ {sub(/^Hardware Port: /,""); port=$0} /^Device:/ {print port "|" $2}' |
    while IFS='|' read -r port dev; do
      case "$port" in
        Wi-Fi|AirPort)
          echo "Cycling Wi-Fi ($dev)"; networksetup -setairportpower "$dev" off; sleep 3; networksetup -setairportpower "$dev" on ;;
        *Ethernet*|*LAN*|*Thunderbolt\ Ethernet*)
          if ifconfig "$dev" 2>/dev/null | grep -q 'status: active'; then
            echo "Cycling $port ($dev)"; ifconfig "$dev" down; sleep 3; ifconfig "$dev" up
          fi ;;
      esac
    done
    if [ "$MODE" = "Full" ]; then
      dscacheutil -flushcache; killall -HUP mDNSResponder 2>/dev/null; echo "DNS cache flushed."
    fi
    ;;
  Linux)
    if command -v nmcli >/dev/null 2>&1; then
      echo "Cycling NetworkManager networking"
      nmcli networking off; sleep 3; nmcli networking on
      [ "$MODE" = "Full" ] && { systemctl restart NetworkManager; echo "NetworkManager restarted."; }
    elif systemctl is-active --quiet systemd-networkd; then
      echo "Restarting systemd-networkd"; systemctl restart systemd-networkd
    else
      for dev in $(ip -o link show up | awk -F': ' '$2 != "lo" {print $2}' | cut -d@ -f1); do
        echo "Cycling $dev"; ip link set "$dev" down; sleep 2; ip link set "$dev" up
      done
    fi
    if [ "$MODE" = "Full" ] && command -v resolvectl >/dev/null 2>&1; then resolvectl flush-caches; echo "DNS cache flushed."; fi
    ;;
esac

if wait_for_network; then echo "Connectivity restored."; exit 0; fi
echo "Connectivity NOT restored within 60s."
exit 2
