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

from config import cfg

log = logging.getLogger(__name__)

_FQDN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$")

METHODS = ("ping", "tcp", "curl")
SOURCES = ("rnd", "nmo")


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


def _valid_dest(dest: str):
    """Return the destination if it's a clean IP or FQDN, else None."""
    dest = (dest or "").strip()
    if not dest or len(dest) > 253:
        return None
    try:
        ipaddress.ip_address(dest)
        return dest
    except ValueError:
        pass
    return dest if _FQDN_RE.match(dest) else None


def _build_command(method: str, dest: str, port: int) -> str:
    """dest is pre-validated (no metacharacters); port is an int."""
    if method == "ping":
        return f"ping -c 4 -W 3 {dest}"
    if method == "tcp":
        return (f"timeout 7 bash -c '</dev/tcp/{dest}/{port}' 2>/dev/null "
                f"&& echo 'REACHABLE — TCP {port} open on {dest}' "
                f"|| echo 'UNREACHABLE — TCP {port} closed or filtered on {dest}'")
    if method == "curl":
        scheme = "https" if port == 443 else "http"
        url = f"{scheme}://{dest}:{port}"
        return (f"curl -sS -m 12 -o /dev/null "
                f"-w 'HTTP %{{http_code}} · %{{time_total}}s · resolved %{{remote_ip}}' {url} "
                f"|| echo ' — curl could not connect'")
    return ""


def _load_key(pem: str):
    import paramiko
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem))
        except Exception:
            continue
    return None


def run_check(source: str, dest: str, port, method: str) -> dict:
    """Execute one reachability check on a connector VM. Returns a result dict
    (never raises)."""
    if source not in SOURCES:
        return {"success": False, "message": "Unknown source connector."}
    if method not in METHODS:
        return {"success": False, "message": "Unknown check type."}
    d = _valid_dest(dest)
    if not d:
        return {"success": False, "message": "Enter a valid destination — an IP address "
                                             "or FQDN (e.g. 10.20.30.40 or api.example.com)."}
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 0
    if method in ("tcp", "curl") and not (1 <= port <= 65535):
        return {"success": False, "message": "Enter a valid port (1–65535) for this check."}

    c = _source_cfg(source)
    if not c or not c["host"] or not c["user"] or not c["key"]:
        return {"success": False,
                "message": f"The {source_label(source)} VM is not configured "
                           f"(Settings → ZPA Connector VMs — host, user and key)."}

    cmd = _build_command(method, d, port)
    try:
        import paramiko
        key = _load_key(c["key"])
        if key is None:
            return {"success": False, "message": "Could not parse the SSH private key for this "
                                                 "connector (Settings → ZPA Connector VMs)."}
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(c["host"], port=int(c["port"] or 22), username=c["user"], pkey=key,
                       timeout=10, banner_timeout=15, auth_timeout=15, look_for_keys=False,
                       allow_agent=False)
        try:
            _in, out, err = client.exec_command(cmd, timeout=30)
            stdout = out.read().decode(errors="replace")[:6000]
            stderr = err.read().decode(errors="replace")[:2000]
            rc = out.channel.recv_exit_status()
        finally:
            client.close()
        return {"success": True, "source": c["label"], "from_host": c["host"],
                "dest": d, "port": port if method in ("tcp", "curl") else None,
                "method": method, "command": cmd,
                "exit_code": rc, "stdout": stdout.strip(), "stderr": stderr.strip()}
    except Exception as exc:
        log.error("reachability run_check failed (%s→%s): %s", source, d, exc)
        return {"success": False, "source": c["label"],
                "message": f"SSH to the {source_label(source)} VM failed: {str(exc)[:200]}"}


def test_ssh(source: str) -> dict:
    """Settings-side connectivity check: can we SSH to the connector VM at all?"""
    if not configured(source):
        return {"success": False, "message": f"{source_label(source)} VM not fully configured."}
    res = run_check(source, "127.0.0.1", 22, "ping")
    if res.get("success"):
        return {"success": True, "message": f"SSH to the {source_label(source)} VM works "
                                            f"({res['from_host']})."}
    return {"success": False, "message": res.get("message", "SSH failed.")}
