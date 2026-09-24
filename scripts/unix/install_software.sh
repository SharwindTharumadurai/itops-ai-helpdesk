#!/bin/bash
[ -z "${BASH_VERSION:-}" ] && exec bash "$0" "$@"  # SSM runs scripts with sh; re-exec under bash
# ITOps: Install an APPROVED package on Linux or macOS (document ITOps-InstallSoftware). Runs as root.
# SOFTWARE is restricted by SSM allowedValues.
SOFTWARE='{{ Software }}'
set -u

if [ "$(uname -s)" = "Darwin" ]; then
  case "$SOFTWARE" in
    chrome) pkg=google-chrome ;; firefox) pkg=firefox ;; 7zip) pkg=sevenzip ;; vlc) pkg=vlc ;;
    vscode) pkg=visual-studio-code ;; zoom) pkg=zoom ;; adobereader) pkg=adobe-acrobat-reader ;;
    *) echo "$SOFTWARE is not packaged for macOS in this catalog."; exit 3 ;;
  esac
  brew=""
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && brew="$b" && break; done
  [ -z "$brew" ] && { echo "Homebrew is not installed on this Mac."; exit 3; }
  # Homebrew refuses to run as root; run as the owner of the brew prefix.
  brew_user=$(stat -f %Su "$(dirname "$(dirname "$brew")")")
  kind="--cask"; [ "$pkg" = "sevenzip" ] && kind="--formula"
  if sudo -H -u "$brew_user" "$brew" list $kind "$pkg" >/dev/null 2>&1; then echo "$pkg already installed."; exit 0; fi
  sudo -H -u "$brew_user" env HOMEBREW_NO_AUTO_UPDATE=1 "$brew" install $kind "$pkg" && { echo "Installed $pkg."; exit 0; }
  echo "brew install $pkg failed (some casks need an interactive admin password)."; exit 1
fi

if command -v apt-get >/dev/null 2>&1; then
  case "$SOFTWARE" in
    firefox) pkg=firefox ;; 7zip) pkg=p7zip-full ;; vlc) pkg=vlc ;;
    *) pkg="" ;;
  esac
  [ -z "$pkg" ] && { echo "$SOFTWARE is not in the default apt repositories; needs a human."; exit 3; }
  dpkg -s "$pkg" >/dev/null 2>&1 && { echo "$pkg already installed."; exit 0; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q && apt-get install -y -q "$pkg" && { echo "Installed $pkg."; exit 0; }
  exit 1
elif command -v dnf >/dev/null 2>&1; then
  case "$SOFTWARE" in
    firefox) pkg=firefox ;; 7zip) pkg=p7zip ;; vlc) pkg=vlc ;;
    *) pkg="" ;;
  esac
  [ -z "$pkg" ] && { echo "$SOFTWARE is not in the default dnf repositories; needs a human."; exit 3; }
  rpm -q "$pkg" >/dev/null 2>&1 && { echo "$pkg already installed."; exit 0; }
  dnf install -y -q "$pkg" && { echo "Installed $pkg."; exit 0; }
  exit 1
fi
echo "No supported package manager (apt/dnf) found."
exit 3
