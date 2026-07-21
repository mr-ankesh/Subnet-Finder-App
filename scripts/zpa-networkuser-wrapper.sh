#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# ZPA Analyzer — read-only forced-command wrapper for `networkuser`
#
# Purpose: confine the SSH key so it can run only READ-ONLY reachability and
# health commands — the ZPA Analyzer's own commands AND ad-hoc diagnostics you
# run by hand (curl/ping/telnet/dig/traceroute/…). It blocks shell chaining,
# redirection, command substitution and anything that could modify the VM.
# `networkuser` is already non-root, so it cannot change the system regardless;
# this stops the key from running arbitrary commands.
#
# Install (on each connector VM, as the account owner or root):
#   1. cp scripts/zpa-networkuser-wrapper.sh /usr/local/bin/zpa-networkuser-wrapper.sh
#   2. chmod 755 /usr/local/bin/zpa-networkuser-wrapper.sh
#   3. In ~networkuser/.ssh/authorized_keys, prefix the analyzer's key line with:
#        command="/usr/local/bin/zpa-networkuser-wrapper.sh",no-port-forwarding,\
#        no-x11-forwarding,no-agent-forwarding,no-pty <ssh-key...>
#   4. Test — see the ZPA_WRAP_DRYRUN examples at the bottom, then the app.
# ─────────────────────────────────────────────────────────────────────────────
set -o noglob
cmd="${SSH_ORIGINAL_COMMAND:-}"

_allow() { [[ "${ZPA_WRAP_DRYRUN:-}" == "1" ]] && { echo ALLOW; exit 0; }; exec /bin/bash -c "$cmd"; }
_deny()  {
  [[ "${ZPA_WRAP_DRYRUN:-}" == "1" ]] && { echo "DENY${1:+ ($1)}"; exit 100; }
  echo "zpa-analyzer: command not permitted for this read-only key" >&2
  command -v logger >/dev/null 2>&1 && logger -t zpa-networkuser "denied: ${cmd:0:200}"
  exit 100
}

# ── 1) The analyzer's compound commands (need shell operators) — exact shapes.
#      '.' stands in for a literal quote/backslash in the command string.
APP_EXACT=(
  "^(getent hosts [A-Za-z0-9._-]+ >/dev/null 2>&1 \\|\\| \\{ echo DNS_FAIL; exit 6; \\}; )?timeout 7 bash -c '</dev/tcp/[A-Za-z0-9._-]+/[0-9]+' 2>/dev/null && echo TCP_OPEN \\|\\| \\{ echo TCP_CLOSED; exit 7; \\}\$"
  '^head -1 /proc/stat; cat /proc/net/dev; sleep 1; printf .@@@SPLIT@@@.n.; head -1 /proc/stat; cat /proc/net/dev$'
  "^curl -sS -m 12 -o /dev/null -w '[^']*' '[^']*'\$"
)
for re in "${APP_EXACT[@]}"; do [[ "$cmd" =~ $re ]] && _allow; done

# ── 2) Everything else must be a SINGLE simple command: no shell chaining,
#      redirection, command substitution or newlines.
case "$cmd" in
  *';'*|*'|'*|*'&'*|*'<'*|*'>'*|*'`'*|*'$('*|*$'\n'*) _deny "shell operator" ;;
esac

read -r tool rest <<< "$cmd"
case "$tool" in
  # Read-only reachability / network diagnostics — any args.
  ping|ping6|curl|telnet|nc|ncat|traceroute|tracepath|tracepath6|mtr|dig|nslookup|host|getent|arping|fping)
    if [[ "$tool" == "curl" ]]; then
      [[ "$cmd" =~ [Ff][Ii][Ll][Ee]:// ]] && _deny "curl file://"
      [[ "$cmd" =~ (^|[[:space:]])(-O|--remote-name|-T|--upload-file|-d|--data|--data-[a-z-]+|-F|--form|-X)([[:space:]]|=|$) ]] && _deny "curl write/POST"
      if [[ "$cmd" =~ (^|[[:space:]])(-o|--output)[[:space:]]+([^[:space:]]+) ]]; then
        [[ "${BASH_REMATCH[3]}" != "/dev/null" ]] && _deny "curl -o file"
      fi
    fi
    _allow ;;
  # Read-only host/health facts — any args.
  uptime|nproc|free|df|timedatectl|hostname|date|whoami|id|uname|lscpu|ip|ss|w|who|vmstat|iostat)
    _allow ;;
  # /proc + /sys reads only.
  cat|head|tail)
    for a in $cmd; do
      [[ "$a" == "$tool" || "$a" == -* ]] && continue
      [[ "$a" == /proc/* || "$a" == /sys/* ]] || _deny "cat outside /proc,/sys"
    done
    _allow ;;
  # systemd read-only subcommands only.
  systemctl)
    read -r _ sub _ <<< "$cmd"
    case "$sub" in show|status|is-active|is-enabled|is-failed|list-units|list-unit-files|cat) _allow ;; esac
    _deny "systemctl non-read subcommand" ;;
esac
_deny "command not in allow-list"

# ── Self-test:  ZPA_WRAP_DRYRUN=1 SSH_ORIGINAL_COMMAND='<cmd>' ./zpa-networkuser-wrapper.sh
#   'curl -vk http://host:8000/path'  → ALLOW      'rm -rf /'          → DENY
#   'ping -c1 10.0.0.1'               → ALLOW      'curl x; rm -rf y'  → DENY
