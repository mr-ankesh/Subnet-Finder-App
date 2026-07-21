# ZPA connector VM — read-only `networkuser` for the ZPA Analyzer

The ZPA Analyzer Portal connects to each connector VM over SSH as the user set
in **Settings → ZPA Connector VMs** (`networkuser`) and runs a small set of
**read-only** commands: reachability probes (ping / curl / TCP) and health reads
(`/proc`, `free`, `df`, `systemctl show`, `timedatectl`).

## Is `networkuser` already read-only?

**Yes.** `networkuser` is a non-root account with no `sudo`, so it cannot modify
the VM, stop the connector, or change configuration. Everything the analyzer
runs only *reads* state. The health dashboard works today because these reads
succeed for an unprivileged user.

One caveat worth knowing: `networkuser`'s interactive shell is `rbash` with a
restricted `PATH` (only ping/curl/telnet), but that restriction applies **only
to interactive logins**. A non-interactive `ssh networkuser@host "<cmd>"` runs
with the default system `PATH`, so any command is reachable. That's why the
analyzer can read health — and why, if you want the key itself confined to just
these commands, you should add the forced-command wrapper below.

## Exact commands the analyzer runs (all read-only)

| Purpose | Command |
|---|---|
| Reachability — ping | `ping -c 4 -W 3 <dest>` |
| Reachability — TCP port | `timeout 7 bash -c '</dev/tcp/<dest>/<port>' …` (with a `getent hosts` DNS pre-check for FQDNs) |
| Reachability — curl | `curl -sS -m 12 -o /dev/null -w '…' '<https-url>'` |
| Health — up check | `ping -c 2 -W 3 127.0.0.1` |
| Health — uptime | `uptime -p` |
| Health — CPU load / count | `cat /proc/loadavg` · `nproc` |
| Health — CPU% + bandwidth | one `/proc/stat` + `/proc/net/dev` sample, `sleep 1`, sample again |
| Health — memory | `free -b` |
| Health — disk | `df -Pk /` |
| Health — connector service | `systemctl show <service> --property=ActiveState,SubState,UnitFileState,MainPID,MemoryCurrent,ActiveEnterTimestamp` |
| Health — clock/time | `timedatectl` |

`<service>` is the **ZPA connector service name** setting (default
`zpa-connector`). None of these modify anything.

## Optional hardening — confine the key to read-only commands

If you want `networkuser`'s SSH **key** to be unable to run mutating/arbitrary
commands (defence-in-depth against key misuse), install the forced-command
wrapper. It allows the analyzer's own commands **and** the read-only network
diagnostics you run by hand — `curl`, `ping`, `telnet`, `nc`, `dig`,
`traceroute`, `tracepath`, `nslookup`, `getent`, plus host/health reads
(`uptime`, `free`, `df`, `cat /proc/*`, `systemctl show/status`, `timedatectl`,
`ip`, `ss`, …). It blocks shell chaining/redirection/substitution (`;` `|` `&`
`` ` `` `$(` `<` `>` newline) and anything that writes or mutates — so
`curl -vk http://host:8000/path` works, but `curl … ; rm -rf …`,
`curl -o /etc/…`, `curl -d …`/uploads, `curl file://…`, `systemctl stop`, and
`cat` outside `/proc`,`/sys` are denied.

To install:

1. Copy [`scripts/zpa-networkuser-wrapper.sh`](../scripts/zpa-networkuser-wrapper.sh)
   to `/usr/local/bin/zpa-networkuser-wrapper.sh` on each connector VM and
   `chmod 755` it.
2. In `~networkuser/.ssh/authorized_keys`, prefix the analyzer's key line with:
   ```
   command="/usr/local/bin/zpa-networkuser-wrapper.sh",no-port-forwarding,no-x11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA… analyzer-key
   ```
3. **Test before relying on it** — from the app: Settings → ZPA Connector VMs →
   *Test*, and open the ZPA Analyzer health dashboard + *More status*. You can
   also dry-run the allow-list locally:
   ```bash
   ZPA_WRAP_DRYRUN=1 SSH_ORIGINAL_COMMAND='uptime -p' ./zpa-networkuser-wrapper.sh   # → ALLOW
   ZPA_WRAP_DRYRUN=1 SSH_ORIGINAL_COMMAND='rm -rf /'   ./zpa-networkuser-wrapper.sh   # → DENY
   ```

The wrapper allows the read-only tool families with any arguments; the
analyzer's two compound commands (the `</dev/tcp>` TCP probe and the CPU/network
sample pipeline) are permitted as **exact shapes** because they need shell
operators. If you change the analyzer's compound commands, update those two
patterns.

### Trade-off
The TCP-port (telnet-style) check needs `bash -c '</dev/tcp/…'`. The wrapper
permits **only** that exact probe shape, not arbitrary `bash`. If your policy
forbids `bash -c` outright, drop that pattern from the allow-list — the
analyzer's **ping** and **curl** checks and the whole health dashboard still
work; only the TCP-port check is lost.

## Going deeper (needs extra access)
These aren't enabled by default because they may require more than the current
read access:
- **Connector logs** — `journalctl -u <service>` (needs the `systemd-journal`/`adm`
  group or sudo).
- **Upstream broker sessions** — `ss -tnp` (process names need the owner or sudo).
- **Enrollment cert expiry** — files under `/opt/zscaler/` (usually root-only).

Ask if you want any of these surfaced in *More status*; I'll wire them with the
same graceful-degradation behaviour (anything unreadable is simply reported as
unavailable).
