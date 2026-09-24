# Endpoint onboarding (SSM hybrid fleet)

## 1. Network requirements
Each device needs outbound HTTPS (TCP 443) to:
`ssm.<region>.amazonaws.com`, `ssmmessages.<region>.amazonaws.com`, `ec2messages.<region>.amazonaws.com`,
and (for installation and updates) `amazon-ssm-<region>.s3.<region>.amazonaws.com`.
No inbound ports and no VPN. If a proxy is in use, set `https_proxy` for the agent service.

## 2. Create a hybrid activation (IT admin workstation)
`cdk deploy` created the IAM service role `ITOpsHybridEndpointRole-<region>`. That role has
`AmazonSSMManagedInstanceCore` plus an explicit **deny on `/itops/*` parameters**. Without the deny, a
local admin on any laptop could use the agent's credentials to read your LLM key and Graph secret.

```powershell
# Laptops/desktops: 25 registrations, valid for 7 days
.\tools\new-activation.ps1 -Region ap-southeast-1 -Limit 25 -Days 7

# The AD management server gets its own activation so it is tagged ITOps:Role=ADAdmin
.\tools\new-activation.ps1 -Region ap-southeast-1 -Limit 1 -Days 1 -Role ADAdmin
```
Console alternative: *Systems Manager → Hybrid Activations → Create*. Select the role above and add tags
`ITOps:Managed=true` and `ITOps:Role=Endpoint`.

Treat the activation code like a password: anyone who has it can enrol a machine. Keep the limit low and
the expiry short, and delete the activation when you are done (`aws ssm delete-activation`).

## 3. Install the agent
Run as Administrator or root on each device:

| OS | Command |
|---|---|
| Windows 10/11, Server 2016+ | `.\scripts\onboarding\install-ssm-windows.ps1 -ActivationCode <code> -ActivationId <id> -Region <region>` |
| Ubuntu/Debian/RHEL/Amazon Linux | `sudo ./scripts/onboarding/install-ssm-linux.sh <code> <id> <region>` |
| macOS (Intel & Apple silicon) | `sudo ./scripts/onboarding/install-ssm-macos.sh <code> <id> <region>` |

Each installer downloads from AWS's regional bucket and **checks the Amazon code signature** before
installing. Linux uses AWS's `ssm-setup-cli`, which does the same check. Before you roll out on macOS,
confirm your macOS version is on the current list of supported hybrid operating systems in the SSM
documentation.

For mass deployment, push the same script through Intune, GPO startup scripts or Jamf. The activation
code is a secret, so use your MDM's secure script parameters rather than hard-coding it.

## 4. Assign the device to its user
In the console, go to *Fleet Manager*, confirm the node is **Online**, and note its `mi-…` ID. Then run:
```powershell
.\tools\set-device-owner.ps1 -Region ap-southeast-1 -InstanceId mi-0123456789abcdef0 -Owner jane.doe@contoso.com
```
This adds the tag `ITOps:Owner` and triggers an inventory sync. The next sync also runs automatically
within 6 hours. Until a device has an owner, only admins can target it. A rogue machine enrolled with a
leaked code therefore cannot be used through the chatbot.

## 5. AD management server (for `ad_*` actions)
- Windows Server with **RSAT AD PowerShell** (`Install-WindowsFeature RSAT-AD-PowerShell`), domain-joined,
  registered with the `ADAdmin` activation.
- Scripts run as SYSTEM, which authenticates to AD as the computer account `SERVER$`. Delegate only this:
  - *Unlock:* in ADUC, *Delegate Control* on the staff OU → custom task → **User objects** →
    property **Read/Write lockoutTime**.
  - *Group membership:* on **each approved group only** → Security → Advanced → add `SERVER$` →
    **Write Members**.
- Do **not** use a domain controller or grant Account Operators or Domain Admins. The scripts also refuse
  to act on `AdminCount=1` (protected) users and groups.

## 6. Verify
```powershell
aws ssm describe-instance-information --filters "Key=tag:ITOps:Managed,Values=true" `
  --query "InstanceInformationList[].[InstanceId,ComputerName,PlatformType,PingStatus]" --output table
python tools\itops_cli.py devices
python tools\itops_cli.py chat "Please run a health check on my laptop"
```

## 7. Troubleshooting
| Symptom | Check |
|---|---|
| Node never appears | Agent log: Windows `%PROGRAMDATA%\Amazon\SSM\Logs\amazon-ssm-agent.log`, Linux `/var/log/amazon/ssm/`, macOS `/var/log/amazon/ssm/` |
| `ConnectionLost` | Outbound 443 is blocked, a proxy is not configured, or the device is asleep |
| Command `Undeliverable` / `DeliveryTimedOut` | Device was offline for more than 1 h. Retry when it is online |
| `AccessDenied` sending command | Node is missing tag `ITOps:Managed=true` (it was registered with an untagged activation) |
| Ticket stuck in `DISPATCHED` | `GET /tickets/{id}` checks SSM directly; also look at the EventBridge rule and the Events Lambda logs |

## 8. Offboarding
```powershell
aws ssm deregister-managed-instance --instance-id mi-0123456789abcdef0
```
Then uninstall the agent from the device. The DynamoDB device record is overwritten at the next sync,
or you can delete it manually.
