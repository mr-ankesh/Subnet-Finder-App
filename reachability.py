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

CONTACT = "Ankesh Singh"


def source_label(source: str) -> str:
    return {"rnd": "R&D ZPA connector", "nmo": "NMO ZPA connector"}.get(source, source)


def _source_cfg(source: str):
    if source == "rnd":
        return {"label": source_label(source), "host": cfg.ZPA_RND_VM_HOST,
                "user": cfg.ZPA_RND_VM_USER, "port": cfg.ZPA_RND_VM_PORT, "key": cfg.ZPA_RND_VM_KEY}
    if source == "nmo":
        return {"label": source_label(source), "host": cfg.ZPA_NMO_VM_HOST,
                "user": cfg.ZPA_NMO_VM_USER, "port": cfg.ZPA_NMO_VM_PORT, "key": cfg.ZPA_NMO_VM_KEY}
    return None


def configured(source: str) -> bool:
    c = _source_cfg(source)
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


def run_check(source: str, method: str, target: str, port=None) -> dict:
    """Execute one reachability check on a connector VM. Returns a result dict
    with a plain-language verdict + guidance. Never raises."""
    if source not in SOURCES:
        return {"success": False, "message": "Unknown source connector."}
    if method not in METHODS:
        return {"success": False, "message": "Unknown check type."}

    plan, err = _plan(method, target, port)
    if err:
        return {"success": False, "message": err}

    c = _source_cfg(source)
    if not c or not c["host"] or not c["user"] or not c["key"]:
        return {"success": False,
                "message": f"The {source_label(source)} VM is not configured "
                           f"(Settings → ZPA Connector VMs — host, user and key)."}

    try:
        import paramiko
        key, keyerr = _load_key(c["key"])
        if key is None:
            return {"success": False,
                    "message": f"Could not parse the SSH private key ({keyerr}). Paste the full "
                               f"key including the BEGIN/END lines in Settings → ZPA Connector VMs."}
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(c["host"], port=int(c["port"] or 22), username=c["user"], pkey=key,
                       timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=False,
                       allow_agent=False)
        try:
            _in, out, err_s = client.exec_command(plan["command"], timeout=30)
            stdout = out.read().decode(errors="replace")[:6000]
            stderr = err_s.read().decode(errors="replace")[:2000]
            rc = out.channel.recv_exit_status()
        finally:
            client.close()
        verdict = _classify(method, plan["host"], rc, stdout, stderr)
        return {"success": True, "source": c["label"], "from_host": c["host"],
                "target": plan["display"], "host": plan["host"], "method": method,
                "exit_code": rc,
                "details": (stdout.strip() + ("\n" + stderr.strip() if stderr.strip() else "")).strip(),
                **verdict}
    except Exception as exc:
        log.error("reachability run_check failed (%s %s→%s): %s", source, method, target, exc)
        return {"success": False, "source": c["label"],
                "message": f"SSH to the {source_label(source)} VM failed: {str(exc)[:200]}"}


def test_ssh(source: str) -> dict:
    """Settings-side connectivity check: can we SSH to the connector VM at all?"""
    if not configured(source):
        return {"success": False, "message": f"{source_label(source)} VM not fully configured."}
    res = run_check(source, "ping", "127.0.0.1")
    if res.get("success"):
        return {"success": True, "message": f"SSH to the {source_label(source)} VM works "
                                            f"({res['from_host']})."}
    return {"success": False, "message": res.get("message", "SSH failed.")}
