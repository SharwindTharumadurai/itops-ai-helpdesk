#!/bin/bash
# Install the AWS SSM Agent on macOS and register it with a hybrid activation.
# Usage: sudo ./install-ssm-macos.sh <activation-code> <activation-id> <region>
# Check the current list of macOS versions supported for hybrid nodes in the SSM docs first.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ $# -eq 3 ] || { echo "Usage: $0 <activation-code> <activation-id> <region>"; exit 1; }
CODE="$1"; ID="$2"; REGION="$3"
[[ "$REGION" =~ ^[a-z]{2}(-gov)?-[a-z]+-[0-9]$ ]] || { echo "Bad region"; exit 1; }

case "$(uname -m)" in
  x86_64) ARCH=darwin_amd64 ;;
  arm64) ARCH=darwin_arm64 ;;
  *) echo "Unsupported architecture"; exit 1 ;;
esac

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
curl -fsSL "https://amazon-ssm-${REGION}.s3.${REGION}.amazonaws.com/latest/${ARCH}/amazon-ssm-agent.pkg" -o "$WORK/amazon-ssm-agent.pkg"

# Only install a package signed by Amazon.
pkgutil --check-signature "$WORK/amazon-ssm-agent.pkg" | grep -q "Amazon" || { echo "Signature check failed"; exit 1; }
installer -pkg "$WORK/amazon-ssm-agent.pkg" -target /

launchctl unload -w /Library/LaunchDaemons/com.amazon.aws.ssm.plist 2>/dev/null || true
/opt/aws/ssm/bin/amazon-ssm-agent -register -code "$CODE" -id "$ID" -region "$REGION" -y
launchctl load -w /Library/LaunchDaemons/com.amazon.aws.ssm.plist
launchctl start com.amazon.aws.ssm

cat /opt/aws/ssm/data/registration 2>/dev/null || cat /var/lib/amazon/ssm/registration 2>/dev/null || true
echo
echo "Done. Ask IT to tag this device with its owner (tools/set-device-owner.ps1)."
