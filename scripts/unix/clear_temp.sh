#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Delete temp/cache files older than N days on Linux or macOS (document ITOps-ClearTemp). Runs as root.
# find -P (default) never follows symlinks; -xdev keeps it on one filesystem.
AGE_DAYS='{{ AgeDays }}'
set -u

root_fs_free() { df -Pk / | awk 'NR==2 {printf "%.1f GB", $4/1048576}'; }
before=$(root_fs_free)
deleted=0

purge() {
  [ -d "$1" ] || return 0
  n=$(find "$1" -xdev -type f -mtime +"$AGE_DAYS" -print 2>/dev/null | wc -l | tr -d ' ')
  find "$1" -xdev -type f -mtime +"$AGE_DAYS" -delete 2>/dev/null
  deleted=$((deleted + n))
  echo "  $1: $n file(s)"
}

echo "Deleting files older than $AGE_DAYS days:"
if [ "$(uname -s)" = "Darwin" ]; then
  purge /private/tmp
  purge /private/var/tmp
  for home in /Users/*; do
    [ -d "$home/Library/Caches" ] && [ ! -L "$home" ] && purge "$home/Library/Caches"
  done
else
  purge /tmp
  purge /var/tmp
  for home in /home/*; do
    [ -d "$home/.cache" ] && [ ! -L "$home" ] && purge "$home/.cache"
  done
  if command -v apt-get >/dev/null 2>&1; then apt-get clean && echo "  apt package cache cleaned"; fi
  if command -v dnf >/dev/null 2>&1; then dnf clean packages -q && echo "  dnf package cache cleaned"; fi
  if command -v journalctl >/dev/null 2>&1; then journalctl --vacuum-time=30d -q && echo "  journal trimmed to 30 days"; fi
fi

echo "Deleted $deleted file(s). Free space on /: $before -> $(root_fs_free)"
exit 0
