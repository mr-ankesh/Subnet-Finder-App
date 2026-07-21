#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# ZPA Analyzer — read-only forced-command wrapper for `networkuser`
#
# Purpose: confine the SSH key the Network Copilot ZPA Analyzer uses so it can
# ONLY run the read-only reachability + health commands below — nothing else.
# `networkuser` is already non-root (it cannot modify the VM); this additionally
# stops the key from running arbitrary commands non-interactively.
#
# Install (on each connector VM, as the account owner or root):
#   1. Copy this file to /usr/local/bin/zpa-networkuser-wrapper.sh
#   2. chmod 755 /usr/local/bin/zpa-networkuser-wrapper.sh
#   3. In ~networkuser/.ssh/authorized_keys, prefix the analyzer's key line with:
#        command="/usr/local/bin/zpa-networkuser-wrapper.sh",no-port-forwarding,\
#        no-x11-forwarding,no-agent-forwarding,no-pty <ssh-key...>
#   4. Test from the app (Settings → ZPA Connector VMs → Test) and the ZPA
#      Analyzer health dashboard before relying on it.
#
# NOTE: the allow-list mirrors the commands built in reachability.py. If you
# change ZPA_CONNECTOR_SERVICE or the analyzer commands, update the patterns.
# Every allowed command only READS state (ping/curl probes, /proc, free, df,
# `systemctl show/status`, timedatectl) — none modify the system.
# ─────────────────────────────────────────────────────────────────────────────
set -o noglob
cmd="${SSH_ORIGINAL_COMMAND:-}"

# Extended-regex allow-list (anchored). '.' is used where a literal quote or
# backslash appears in the analyzer's command string.
ALLOW=(
  # ── Reachability probes ──
  '^ping -c [0-9]+ -W [0-9]+ [A-Za-z0-9._:-]+$'
  "^curl -sS -m 12 -o /dev/null -w '[^']*' 'https?://[^']*'\$"
  "^timeout 7 bash -c '</dev/tcp/[A-Za-z0-9._-]+/[0-9]+' 2>/dev/null && echo TCP_OPEN \\|\\| \\{ echo TCP_CLOSED; exit 7; \\}\$"
  "^getent hosts [A-Za-z0-9._-]+ >/dev/null 2>&1 \\|\\| \\{ echo DNS_FAIL; exit 6; \\}; timeout 7 bash -c '</dev/tcp/[A-Za-z0-9._-]+/[0-9]+' 2>/dev/null && echo TCP_OPEN \\|\\| \\{ echo TCP_CLOSED; exit 7; \\}\$"
  # ── Health dashboard (read-only) ──
  '^uptime( -p)?$'
  '^cat /proc/loadavg$'
  '^nproc$'
  '^free -[bm]$'
  '^df -Pk? /$'
  '^df -h /$'
  '^systemctl show [A-Za-z0-9@._-]+ --property=[A-Za-z0-9,]+$'
  '^systemctl (is-active|is-enabled|status) [A-Za-z0-9@._ -]+$'
  '^timedatectl$'
  # ── CPU + bandwidth sample pipeline ──
  '^head -1 /proc/stat; cat /proc/net/dev; sleep 1; printf .@@@SPLIT@@@.n.; head -1 /proc/stat; cat /proc/net/dev$'
)

# Set ZPA_WRAP_DRYRUN=1 to test the allow-list without running anything:
#   ZPA_WRAP_DRYRUN=1 SSH_ORIGINAL_COMMAND='uptime -p' ./zpa-networkuser-wrapper.sh
for re in "${ALLOW[@]}"; do
  if [[ "$cmd" =~ $re ]]; then
    if [[ "${ZPA_WRAP_DRYRUN:-}" == "1" ]]; then echo "ALLOW"; exit 0; fi
    exec /bin/bash -c "$cmd"
  fi
done

if [[ "${ZPA_WRAP_DRYRUN:-}" == "1" ]]; then echo "DENY"; exit 100; fi

echo "zpa-analyzer: command not permitted for this read-only key" >&2
command -v logger >/dev/null 2>&1 && logger -t zpa-networkuser "denied: ${cmd:0:200}"
exit 100
