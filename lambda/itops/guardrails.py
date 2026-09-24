"""Static checks for LLM-generated scripts.

This is DEFENCE IN DEPTH, not the security boundary. A denylist can always be
evaded by a determined author; the real controls are (1) catalog-only execution
for everything the chatbot does on its own, and (2) mandatory human review with
hash-pinned, two-person approval for generated scripts. These rules exist to
reject obviously dangerous output before a human ever has to look at it.
"""
import re

MAX_SCRIPT_CHARS = 8000
MAX_SCRIPT_LINES = 200

_COMMON = [
    (r"[^\x09\x0a\x0d\x20-\x7e]", "non-ASCII or control characters (possible homoglyph/hidden payload)"),
    (r"(?i)amazon-ssm-agent|amazonssmagent|\\amazon\\ssm", "tampering with the SSM agent"),
    (r"(?i)\b(mimikatz|sekurlsa|lsass|procdump|hashdump)\b", "credential access tooling"),
    (r"(?i)\b(169\.254\.169\.254|fd00:ec2::254)\b", "cloud metadata endpoint access"),
    (r"(?i)/itops/|ssm:GetParameter|get-ssmparameter|aws\s+ssm\s+get-parameter", "reading platform secrets"),
]

_POWERSHELL = [
    (r"(?i)\b(invoke-expression|iex)\b|\[scriptblock\]::create|add-type", "dynamic code execution"),
    (r"(?i)(^|\s)-(e|ec|en|enc|encodedcommand)\s+[A-Za-z0-9+/=]{16,}", "encoded command"),
    (r"(?i)frombase64string|convert\]::frombase64", "base64-decoded payload"),
    (r"(?i)\b(invoke-webrequest|iwr|invoke-restmethod|irm|start-bitstransfer|curl|wget|bitsadmin|certutil)\b"
     r"|net\.webclient|downloadstring|downloadfile|system\.net\.http|net\.sockets", "network download or egress"),
    (r"(?i)set-mppreference|add-mppreference|disablerealtimemonitoring|tamperprotection", "changing Defender configuration"),
    (r"(?i)\b(format-volume|clear-disk|initialize-disk|remove-partition|diskpart|bcdedit|vssadmin|wbadmin|cipher(\.exe)?\s+/w)\b",
     "destructive disk, boot or backup operation"),
    (r"(?i)\b(remove-item|rm|del|erase|rd|rmdir)\b[^\n]*(['\"\s]|^)([a-z]:\\?|\$env:(systemdrive|systemroot|windir|programfiles|programdata|userprofile)\\?)['\"*\s]*($|\s-)",
     "deleting a drive root or system directory"),
    (r"(?i)\b(new-localuser|set-localuser|add-localgroupmember|net(\.exe)?\s+(user|localgroup))\b", "local account or group changes"),
    (r"(?i)\b(register-scheduledtask|new-scheduledtask|schtasks|new-service|sc(\.exe)?\s+(create|config)|wmic)\b",
     "persistence via tasks, services or WMI"),
    (r"(?i)currentversion\\\\?run|\\\\?services\\\\|hklm:\\\\?system", "autorun or service registry keys"),
    (r"(?i)\b(reg(\.exe)?\s+(delete|add)|remove-itemproperty|set-itemproperty)\b[^\n]*hklm", "machine registry modification"),
    (r"(?i)\b(set-executionpolicy|stop-computer|restart-computer|shutdown(\.exe)?)\b", "policy change or reboot (use an approved action)"),
    (r"(?i)\b(disable-netfirewall|set-netfirewallprofile|netsh\s+advfirewall)\b", "firewall changes"),
    (r"(?i)\bstart-process\b[^\n]*-verb\s+runas", "elevation prompt"),
]

_SHELL = [
    (r"\brm\s+(-{1,2}[A-Za-z-]+\s+)*(/|/\*|~|\$HOME|/(etc|usr|var|bin|sbin|boot|lib|lib64|opt|System|Library|Users|home|Applications))/?(\s|;|$)",
     "deleting a root or system directory"),
    (r"\b(mkfs(\.\w+)?|fdisk|sfdisk|parted|wipefs|shred|diskutil\s+(erase\w*|partition\w*|zero\w*|secureErase))\b", "disk formatting or wiping"),
    (r"\bdd\b[^\n]*\bof=/dev/", "raw disk write"),
    (r"\b(curl|wget|fetch|nc|ncat|netcat|socat|scp|sftp|ftp|tftp|rsync|telnet)\b", "network download or egress"),
    (r"/dev/(tcp|udp)/", "raw network socket"),
    (r"\bbase64\s+(-d|--decode|-D)\b|\bxxd\s+-r\b|\bopenssl\s+enc\b", "encoded payload"),
    (r"(^|[;&|\s])(eval|exec|source)\s", "dynamic code execution"),
    (r"\b(python[0-9.]*|perl|ruby|php|node|osascript|awk)\s+-(c|e)\b|\bbash\s+-c\b|\bsh\s+-c\b", "inline interpreter"),
    (r"\b(useradd|adduser|usermod|userdel|deluser|passwd|chpasswd|dscl|sysadminctl|dseditgroup)\b", "account changes"),
    (r"/etc/(sudoers|shadow|passwd|pam\.d)|authorized_keys|\.ssh/", "credential or privilege files"),
    (r"\b(crontab|launchctl\s+(load|bootstrap|submit|enable)|systemctl\s+(enable|mask|link))\b|\bat\s+now\b|/Library/Launch(Daemons|Agents)",
     "persistence"),
    (r"\bchmod\s+(-R\s+)?(777|666|[0-7]?[4-7][0-7]{3}|[ugoa]*\+s)\b|\bchown\s+-R\s+\S+\s+/(\s|$)", "insecure permissions or setuid"),
    (r"\b(setenforce\s+0|csrutil|spctl\s+--master-disable|ufw\s+disable|iptables\s+-F|pfctl\s+-d)\b"
     r"|systemctl\s+(stop|disable)\s+(auditd|firewalld|ufw|apparmor|mdatp|clamav\w*)", "disabling security controls"),
    (r"\b(shutdown|reboot|halt|poweroff)\b|\binit\s+[06]\b", "reboot or shutdown (use an approved action)"),
    (r":\s*\(\s*\)\s*\{", "fork bomb"),
    (r"\bsudo\b", "sudo (scripts already run as root; sudo suggests privilege games)"),
]


def check_script(platform, script):
    """Return a list of human-readable violations. Empty list == passed static checks."""
    if not isinstance(script, str) or not script.strip():
        return ["script is empty"]
    violations = []
    if len(script) > MAX_SCRIPT_CHARS:
        violations.append(f"script longer than {MAX_SCRIPT_CHARS} characters")
    if script.count("\n") + 1 > MAX_SCRIPT_LINES:
        violations.append(f"script longer than {MAX_SCRIPT_LINES} lines")

    rules = _COMMON + (_POWERSHELL if platform == "windows" else _SHELL)
    for pattern, reason in rules:
        for line_no, line in enumerate(script.splitlines(), 1):
            stripped = line.strip()
            # Comments are still scanned: attackers hide payloads in "comments" that a
            # later edit uncomments, and reviewers skim them.
            if re.search(pattern, stripped):
                violations.append(f"line {line_no}: {reason}")
                break
    return violations
