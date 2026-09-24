#!/bin/bash
# Install the AWS SSM Agent on Linux and register it with a hybrid activation.
# Usage: sudo ./install-ssm-linux.sh <activation-code> <activation-id> <region>
# Uses AWS's ssm-setup-cli, which verifies the agent package signature before installing.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ $# -eq 3 ] || { echo "Usage: $0 <activation-code> <activation-id> <region>"; exit 1; }
CODE="$1"; ID="$2"; REGION="$3"
[[ "$REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]$ ]] || { echo "Bad region"; exit 1; }
[[ "$ID" =~ ^[0-9a-f-]{36}$ ]] || { echo "Bad activation id"; exit 1; }

case "$(uname -m)" in
  x86_64) ARCH=linux_amd64 ;;
  aarch64|arm64) ARCH=linux_arm64 ;;
  *) echo "Unsupported architecture $(uname -m)"; exit 1 ;;
esac

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
curl -fsSL "https://amazon-ssm-${REGION}.s3.${REGION}.amazonaws.com/latest/${ARCH}/ssm-setup-cli" -o "$WORK/ssm-setup-cli"
chmod +x "$WORK/ssm-setup-cli"
"$WORK/ssm-setup-cli" -register -activation-code "$CODE" -activation-id "$ID" -region "$REGION"

systemctl enable --now amazon-ssm-agent 2>/dev/null || snap start amazon-ssm-agent 2>/dev/null || true
cat /var/lib/amazon/ssm/registration 2>/dev/null || true
echo
echo "Done. Ask IT to tag this device with its owner (tools/set-device-owner.ps1)."
