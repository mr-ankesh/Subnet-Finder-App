"""
Reachability Tester — run ping / TCP-port (telnet-style) / curl checks from
the R&D or NMO ZPA connector VMs, over SSH.

Security: the destination is validated to be a bare IP or FQDN and the port to
be an integer, so the remote command is built ONLY from safe, validated tokens
(no shell metacharacters ever reach the VM). The SSH key is a secret setting
(encrypted at rest) and never leaves the server.
"""
import io
import ipaddress
import logging
import re
import shlex
from urllib.parse import urlparse

from config import cfg

log = logging.getLogger(__name__)

_FQDN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")

METHODS = ("ping", "telnet", "curl")
SOURCES = ("rnd", "nmo")
INSTANCES = ("primary", "secondary")

CONTACT = "Ankesh Singh"


def source_label(source: str, instance: str = "primary") -> str:
    base = {"rnd": "R&D ZPA connector", "nmo": "NMO ZPA connector"}.get(source, source)
    return base + (" (secondary)" if instance == "secondary" else "")


def _source_cfg(source: str, instance: str = "primary"):
    """Config for one connector VM instance. Primary and secondary share the
    same SSH user/port/key; only the host/IP differs (HA pair)."""
    if source == "rnd":
        host = cfg.ZPA_RND_VM_HOST_2 if instance == "secondary" else cfg.ZPA_RND_VM_HOST
        return {"label": source_label(source, instance), "source": source, "instance": instance,
                "host": host, "user": cfg.ZPA_RND_VM_USER, "port": cfg.ZPA_RND_VM_PORT,
                "key": cfg.ZPA_RND_VM_KEY}
    if source == "nmo":
        host = cfg.ZPA_NMO_VM_HOST_2 if instance == "secondary" else cfg.ZPA_NMO_VM_HOST
        return {"label": source_label(source, instance), "source": source, "instance": instance,
                "host": host, "user": cfg.ZPA_NMO_VM_USER, "port": cfg.ZPA_NMO_VM_PORT,
                "key": cfg.ZPA_NMO_VM_KEY}
    return None


def configured(source: str, instance: str = "primary") -> bool:
    c = _source_cfg(source, instance)
    return bool(c and c["host"] and c["user"] and c["key"])


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _valid_dest(dest: str):
    """Return the destination if it's a clean IP or FQDN, else None."""
    dest = (dest or "").strip()
    if not dest or len(dest) > 253:
        return None
    if _is_ip(dest):
        return dest
    return dest if _FQDN_RE.match(dest) else None


def _valid_url(url: str):
    """Return a normalised http(s) URL, else None. No whitespace/control chars."""
    url = (url or "").strip()
    if not url or len(url) > 2048 or re.search(r"\s", url):
        return None
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    return url


# ── Result interpretation ───────────────────────────────────────────────────
# Turn raw ping/telnet/curl output into a plain verdict + actionable guidance.

_DNS_MARKERS = ("could not resolve", "couldn't resolve", "name or service not known",
                "unknown host", "temporary failure in name resolution", "dns_fail",
                "name resolution", "could not resolve host")
# curl exit codes where the host WAS reached (TLS negotiated / cert issue) → reachable.
_CURL_TLS_OK = {35, 51, 53, 54, 58, 59, 60, 66, 77, 80, 82, 83, 90, 91}

_GUIDANCE_DNS = ("This looks like a DNS issue — the name is not resolving. Ask the requester "
                 "to raise a “DNS Integration with Hub” request so the Hub can resolve it.")
_GUIDANCE_UNREACHABLE = ("Is the spoke integrated with the Hub? If YES, contact " + CONTACT +
                         ". If NO, ask the requester to raise a “Hub Integration” request.")


def _classify(method: str, host: str, exit_code: int, stdout: str, stderr: str) -> dict:
    text = ((stdout or "") + " " + (stderr or "")).lower()
    is_dns = any(m in text for m in _DNS_MARKERS) or (method == "curl" and exit_code == 6) \
        or (method == "telnet" and exit_code == 6)

    if method == "curl":
        reachable = (exit_code == 0) or (exit_code in _CURL_TLS_OK)
    else:
        reachable = (exit_code == 0)

    if reachable:
        extra = ""
        if method == "curl" and exit_code in _CURL_TLS_OK:
            extra = " (TCP/TLS connected; a certificate/TLS note is shown in details)"
        return {"verdict": "reachable", "ok": True,
                "headline": f"{host} is reachable{extra}.",
                "guidance": ""}
    if is_dns:
        return {"verdict": "dns", "ok": False,
                "headline": f"Not reachable — {host} is not resolving (DNS).",
                "guidance": _GUIDANCE_DNS}
    return {"verdict": "unreachable", "ok": False,
            "headline": f"Not reachable — {host} did not respond.",
            "guidance": _GUIDANCE_UNREACHABLE}


def _rewrap_pem(text: str) -> str:
    """Rebuild a PEM whose newlines were flattened (e.g. pasted into a
    single-line input): keep the BEGIN/END markers, strip whitespace from the
    base64 body, and re-wrap at 64 chars so it parses again."""
    import re
    m = re.search(r"(-----BEGIN [A-Z0-9 ]+?-----)(.*?)(-----END [A-Z0-9 ]+?-----)",
                  text, re.S)
    if not m:
        return text
    header, body, footer = m.group(1), m.group(2), m.group(3)
    b64 = re.sub(r"\s+", "", body)
    wrapped = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
    return f"{header}\n{wrapped}\n{footer}\n"


def _load_key(pem: str, passphrase: str = None):
    """Parse a private key (RSA / Ed25519 / ECDSA), tolerating a flattened
    PEM. Returns (key, None) or (None, reason)."""
    import paramiko
    raw = (pem or "").strip()
    candidates = [raw]
    if "\\n" in raw and "\n" not in raw:            # literal backslash-n
        candidates.append(raw.replace("\\n", "\n"))
    rewrapped = _rewrap_pem(candidates[-1])
    if rewrapped not in candidates:
        candidates.append(rewrapped)
    last_err = None
    for text in candidates:
        for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return cls.from_private_key(io.StringIO(text), password=passphrase), None
            except paramiko.PasswordRequiredException:
                last_err = "the key is passphrase-protected — use a key without a passphrase"
            except Exception as exc:
                last_err = str(exc)
    return None, (last_err or "unrecognised key format")


def _plan(method: str, target: str, port):
    """Validate inputs and build (command, host, display_target) or (None, error)."""
    if method == "curl":
        url = _valid_url(target)
        if not url:
            return None, "Enter a full URL to curl (e.g. https://api.example.com or " \
                         "https://host:8443/health)."
        host = urlparse(url).hostname
        # URL is shell-quoted → safe to embed even with query params.
        cmd = ("curl -sS -m 12 -o /dev/null "
               "-w 'HTTP %{http_code} in %{time_total}s (resolved %{remote_ip})' " + shlex.quote(url))
        return {"command": cmd, "host": host, "display": url}, None

    dest = _valid_dest(target)
    if not dest:
        return None, "Enter a valid destination — an IP address or FQDN " \
                     "(e.g. 10.20.30.40 or api.example.com)."
    if method == "ping":
        return {"command": f"ping -c 4 -W 3 {dest}", "host": dest, "display": dest}, None
    # telnet — TCP port reachability, with DNS pre-check for FQDNs so we can
    # tell a resolution failure apart from a connection failure.
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 0
    if not (1 <= port <= 65535):
        return None, "Enter a valid port (1–65535) for the telnet check."
    check = (f"timeout 7 bash -c '</dev/tcp/{dest}/{port}' 2>/dev/null "
             f"&& echo TCP_OPEN || {{ echo TCP_CLOSED; exit 7; }}")
    if not _is_ip(dest):
        check = f"getent hosts {dest} >/dev/null 2>&1 || {{ echo DNS_FAIL; exit 6; }}; " + check
    return {"command": check, "host": dest, "display": f"{dest}:{port}"}, None


def _ssh_run(c: dict, command: str, timeout: int = 30) -> dict:
    """Connect to a connector VM and run one command. Returns
    {success, exit_code, stdout, stderr} or {success:False, message}. Never raises."""
    try:
        import paramiko
        key, keyerr = _load_key(c["key"])
        if key is None:
            return {"success": False, "message": f"key parse error: {keyerr}"}
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(c["host"], port=int(c["port"] or 22), username=c["user"], pkey=key,
                       timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=False,
                       allow_agent=False)
        try:
            _in, out, err_s = client.exec_command(command, timeout=timeout)
            stdout = out.read().decode(errors="replace")[:8000]
            stderr = err_s.read().decode(errors="replace")[:3000]
            rc = out.channel.recv_exit_status()
        finally:
            client.close()
        return {"success": True, "exit_code": rc, "stdout": stdout, "stderr": stderr}
    except Exception as exc:
        log.error("ssh run failed (%s): %s", c.get("host"), exc)
        return {"success": False, "message": str(exc)[:200]}


def run_check(source: str, method: str, target: str, port=None, instance: str = "primary") -> dict:
    """Execute one reachability check on a connector VM. Returns a result dict
    with a plain-language verdict + guidance. Never raises."""
    if source not in SOURCES:
        return {"success": False, "message": "Unknown source connector."}
    if instance not in INSTANCES:
        instance = "primary"
    if method not in METHODS:
        return {"success": False, "message": "Unknown check type."}

    plan, err = _plan(method, target, port)
    if err:
        return {"success": False, "message": err}

    c = _source_cfg(source, instance)
    if not c or not c["host"] or not c["user"] or not c["key"]:
        return {"success": False,
                "message": f"The {source_label(source, instance)} VM is not configured "
                           f"(Settings → ZPA Connector VMs — host, user and key)."}

    run = _ssh_run(c, plan["command"], timeout=30)
    if not run.get("success"):
        return {"success": False, "source": c["label"],
                "message": f"SSH to the {source_label(source, instance)} VM failed: {run.get('message')}"}
    stdout, stderr, rc = run["stdout"], run["stderr"], run["exit_code"]
    verdict = _classify(method, plan["host"], rc, stdout, stderr)
    return {"success": True, "source": c["label"], "instance": instance, "from_host": c["host"],
            "target": plan["display"], "host": plan["host"], "method": method, "exit_code": rc,
            "details": (stdout.strip() + ("\n" + stderr.strip() if stderr.strip() else "")).strip(),
            **verdict}


def test_ssh(source: str, instance: str = "primary") -> dict:
    """Settings-side connectivity check: can we SSH to the connector VM at all?"""
    if not configured(source, instance):
        return {"success": False, "message": f"{source_label(source, instance)} VM not fully configured."}
    res = run_check(source, "ping", "127.0.0.1", instance=instance)
    if res.get("success"):
        return {"success": True, "message": f"SSH to the {source_label(source, instance)} VM works "
                                            f"({res['from_host']})."}
    return {"success": False, "message": res.get("message", "SSH failed.")}


# ── Health dashboard: are the connector VMs up? + richer per-VM status ───────

def health_all() -> list:
    """Health of every configured connector VM instance, via SSH + a loopback
    ping test. Secondary instances are only included when a host is set."""
    out = []
    for source in SOURCES:
        for instance in INSTANCES:
            c = _source_cfg(source, instance)
            if not c:
                continue
            if not c["host"]:
                if instance == "secondary":
                    continue                       # optional — skip when unset
                out.append({"source": source, "instance": instance, "label": c["label"],
                            "host": "", "configured": False, "up": None,
                            "message": "Not configured — set the host, user and key in Settings."})
                continue
            if not (c["user"] and c["key"]):
                out.append({"source": source, "instance": instance, "label": c["label"],
                            "host": c["host"], "configured": False, "up": None,
                            "message": "Missing SSH user or key in Settings."})
                continue
            # SSH reachability is the definitive "VM is up" signal; the plain
            # ping (an allowed command) is the extra check the dashboard shows.
            run = _ssh_run(c, "ping -c 2 -W 3 127.0.0.1", timeout=20)
            if not run.get("success"):
                out.append({"source": source, "instance": instance, "label": c["label"],
                            "host": c["host"], "configured": True, "up": False, "ping_ok": False,
                            "message": f"Down — cannot reach the VM: {run.get('message')}"})
                continue
            ping_ok = run.get("exit_code") == 0
            out.append({"source": source, "instance": instance, "label": c["label"],
                        "host": c["host"], "configured": True, "up": True, "ping_ok": ping_ok,
                        "message": "Up — SSH reachable" + (", ping OK." if ping_ok
                                   else ", but the ping test did not pass (restricted?).")})
    return out


def _ssh_run_many(c: dict, commands: list, timeout: int = 15) -> list:
    """Run several commands over ONE SSH connection (robust against restricted
    shells — no shell operators required). One result dict per command; a single
    'connect_failed' element if the connection itself fails. Never raises."""
    try:
        import paramiko
        key, keyerr = _load_key(c["key"])
        if key is None:
            return [{"success": False, "connect_failed": True, "message": f"key parse error: {keyerr}"}]
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(c["host"], port=int(c["port"] or 22), username=c["user"], pkey=key,
                       timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=False,
                       allow_agent=False)
        results = []
        try:
            for cmd in commands:
                try:
                    _in, out, err_s = client.exec_command(cmd, timeout=timeout)
                    results.append({"success": True, "exit_code": out.channel.recv_exit_status(),
                                    "stdout": out.read().decode(errors="replace")[:4000],
                                    "stderr": err_s.read().decode(errors="replace")[:2000]})
                except Exception as exc:
                    results.append({"success": False, "message": str(exc)[:200]})
        finally:
            client.close()
        return results
    except Exception as exc:
        log.error("ssh multi run failed (%s): %s", c.get("host"), exc)
        return [{"success": False, "connect_failed": True, "message": str(exc)[:200]}]


# ── Metric parsing helpers (all read-only: /proc, free, df, systemctl show) ──

def _num(s, cast=float, default=None):
    try:
        return cast(str(s).strip())
    except (TypeError, ValueError):
        return default


def _parse_free_b(text: str):
    for line in (text or "").splitlines():
        if line.lower().startswith("mem:"):
            p = line.split()
            total = _num(p[1], int) if len(p) > 1 else None
            avail = _num(p[6], int) if len(p) > 6 else None
            if total:
                avail = avail if avail is not None else _num(p[3], int, 0)
                used = total - (avail or 0)
                mb = 1048576
                return {"total_mb": round(total / mb), "used_mb": round(used / mb),
                        "available_mb": round((avail or 0) / mb),
                        "used_pct": round(used / total * 100, 1)}
    return None


def _parse_df(text: str):
    lines = [l for l in (text or "").splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    p = lines[1].split()
    if len(p) < 6:
        return None
    gb = 1048576  # KB → GB
    total_k, used_k, avail_k = _num(p[1], int), _num(p[2], int), _num(p[3], int)
    pct = _num((p[4] or "").rstrip("%"), int)
    if not total_k:
        return None
    return {"total_gb": round(total_k / gb, 1), "used_gb": round((used_k or 0) / gb, 1),
            "avail_gb": round((avail_k or 0) / gb, 1),
            "used_pct": pct if pct is not None else round((used_k or 0) / total_k * 100),
            "mount": p[5]}


def _parse_kv(text: str):
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _parse_timedatectl(text: str):
    info = {}
    for line in (text or "").splitlines():
        low = line.lower()
        if "local time:" in low:
            info["local"] = line.split(":", 1)[1].strip()
        elif "time zone:" in low:
            info["timezone"] = line.split(":", 1)[1].strip()
        elif "synchronized:" in low:
            info["synced"] = "yes" in low
        elif "ntp service:" in low and "synced" not in info:
            info["ntp"] = line.split(":", 1)[1].strip()
    return info or None


def _cpu_line(text: str):
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            return [n for n in (_num(x, int) for x in line.split()[1:]) if n is not None]
    return None


def _net_ifaces(text: str):
    out = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        nums = [_num(x, int, 0) for x in rest.split()]
        if name and len(nums) >= 9:
            out[name] = (nums[0], nums[8])       # rx_bytes, tx_bytes
    return out


def _parse_sample(text: str, interval: float = 1.0):
    """Two /proc/stat + /proc/net/dev snapshots → (cpu_pct, bandwidth)."""
    if "@@@SPLIT@@@" not in (text or ""):
        return None, None
    a, b = text.split("@@@SPLIT@@@", 1)
    cpu_pct = None
    c1, c2 = _cpu_line(a), _cpu_line(b)
    if c1 and c2 and len(c1) >= 5 and len(c2) >= 5:
        idle1, idle2 = c1[3] + c1[4], c2[3] + c2[4]
        dt, di = sum(c2) - sum(c1), idle2 - idle1
        if dt > 0:
            cpu_pct = round(max(0.0, min(100.0, 100.0 * (1 - di / dt))), 1)
    bw = None
    n1, n2 = _net_ifaces(a), _net_ifaces(b)
    best = None
    for ifc, (rx2, tx2) in n2.items():
        if ifc == "lo" or ifc not in n1:
            continue
        rx, tx = rx2 - n1[ifc][0], tx2 - n1[ifc][1]
        if best is None or (rx + tx) > best[0]:
            best = (rx + tx, ifc, rx, tx)
    if best:
        _, ifc, rx, tx = best
        bw = {"iface": ifc, "rx_kbps": round(max(rx, 0) / interval / 1024, 1),
              "tx_kbps": round(max(tx, 0) / interval / 1024, 1)}
    return cpu_pct, bw


def vm_status(source: str, instance: str = "primary") -> dict:
    """Structured per-VM health for the dashboard's 'More status': CPU, RAM,
    bandwidth, disk, the ZPA connector service and clock/time — all read-only
    (reads /proc, free, df, systemctl show, timedatectl). Gathered over one SSH
    connection; anything unreadable is listed in 'unavailable'."""
    if source not in SOURCES or instance not in INSTANCES:
        return {"success": False, "message": "Unknown connector."}
    if not configured(source, instance):
        return {"success": False, "message": f"{source_label(source, instance)} VM not configured."}
    c = _source_cfg(source, instance)
    svc = cfg.ZPA_CONNECTOR_SERVICE or "zpa-connector"
    svcq = shlex.quote(svc)
    cmds = [
        "uptime -p",
        "cat /proc/loadavg",
        "nproc",
        "free -b",
        "df -Pk /",
        f"systemctl show {svcq} --property=ActiveState,SubState,UnitFileState,MainPID,MemoryCurrent,ActiveEnterTimestamp",
        "timedatectl",
        "head -1 /proc/stat; cat /proc/net/dev; sleep 1; printf '@@@SPLIT@@@\\n'; head -1 /proc/stat; cat /proc/net/dev",
    ]
    res = _ssh_run_many(c, cmds, timeout=20)
    if res and res[0].get("connect_failed"):
        return {"success": False, "label": c["label"], "host": c["host"],
                "message": f"SSH to the VM failed: {res[0].get('message')}"}

    def out(i):
        r = res[i] if i < len(res) else {}
        return (r.get("stdout") or "").strip() if r.get("success") else ""

    metrics, unavailable = {}, []

    uptime = out(0)
    if uptime:
        metrics["uptime"] = uptime
    else:
        unavailable.append("uptime")

    la = out(1).split()
    cores = _num(out(2), int)
    cpu = {}
    if len(la) >= 3:
        cpu.update({"load1": _num(la[0]), "load5": _num(la[1]), "load15": _num(la[2])})
    if cores:
        cpu["cores"] = cores

    mem = _parse_free_b(out(3))
    (metrics.__setitem__("memory", mem) if mem else unavailable.append("memory"))

    disk = _parse_df(out(4))
    (metrics.__setitem__("disk", disk) if disk else unavailable.append("disk"))

    kv = _parse_kv(out(5))
    if kv:
        mem_b = _num(kv.get("MemoryCurrent"), int)
        pid = kv.get("MainPID")
        metrics["service"] = {
            "name": svc, "active": kv.get("ActiveState"), "sub": kv.get("SubState"),
            "enabled": kv.get("UnitFileState"),
            "pid": pid if pid and pid != "0" else None,
            "memory_mb": round(mem_b / 1048576) if mem_b and mem_b < (1 << 62) else None,
            "since": kv.get("ActiveEnterTimestamp") or None}
    else:
        unavailable.append("service")

    tinfo = _parse_timedatectl(out(6))
    (metrics.__setitem__("time", tinfo) if tinfo else unavailable.append("time"))

    cpu_pct, bw = _parse_sample(out(7))
    if cpu_pct is not None:
        cpu["usage_pct"] = cpu_pct
    (metrics.__setitem__("cpu", cpu) if cpu else unavailable.append("cpu"))
    (metrics.__setitem__("bandwidth", bw) if bw else unavailable.append("bandwidth"))

    return {"success": True, "label": c["label"], "host": c["host"], "instance": instance,
            "service_name": svc, "metrics": metrics, "unavailable": unavailable}
