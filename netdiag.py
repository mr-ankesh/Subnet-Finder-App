"""
Network connectivity diagnosis for "Report Network Issue" requests.

Given a source (IP or resource) and destination (IP or FQDN), traces the path
using the Azure control plane: locates the source VNet/subnet + its UDR, checks
DNS resolvability for FQDNs, follows the route toward the destination, checks
whether the hub firewall allows the traffic, and (for internal destinations)
checks the return route. Optionally runs a live ping/TCP/curl test from a ZPA
connector VM.

Everything here is READ-ONLY. Important limits are noted in each step:
  * Routing uses the subnet's associated UDR only — Azure system/BGP/peering
    routes are not visible without an "Effective routes" call on a real NIC.
  * DNS/firewall/reachability from the *exact* source can't be run remotely; we
    check config + (optionally) test from a connector VM as a proxy.
"""
import ipaddress
import logging
import re

import azure_tools
from config import cfg

log = logging.getLogger(__name__)


def _is_ip(s) -> bool:
    try:
        ipaddress.ip_address(str(s).strip())
        return True
    except ValueError:
        return False


def _is_private(ip) -> bool:
    try:
        return ipaddress.ip_address(str(ip).strip()).is_private
    except ValueError:
        return False


def _live(source, method, target, port=None):
    """Run a reachability check from a connector VM (best configured one)."""
    try:
        import reachability
        for inst_src in ("rnd", "nmo"):
            if reachability.configured(inst_src):
                return reachability.run_check(inst_src, method, target, port)
    except Exception as exc:
        log.error("netdiag live check failed: %s", exc)
    return {"success": False, "message": "No connector VM configured for a live test."}


def diagnose(details: dict, run_live: bool = False) -> dict:
    d = details or {}
    source = (d.get("source") or "").strip()
    source_kind = (d.get("source_kind") or "ip").strip()
    dest = (d.get("destination") or "").strip()
    port = (d.get("dest_port") or "").strip()

    steps = []

    def add(key, title, status, detail, data=None):
        steps.append({"key": key, "title": title, "status": status,
                      "detail": detail, "data": data or {}})

    # ── 1. Source location ──────────────────────────────────────────────────
    src_ip = source if _is_ip(source) else None
    src = {"found": False, "message": "No source IP provided."}
    if src_ip:
        src = azure_tools.locate_ip(src_ip, d.get("source_subscription"))
    elif source_kind == "aks" and d.get("source_resource"):
        r = azure_tools.aks_source_subnet(
            d.get("source_subscription") or cfg.SPOKE_SUBSCRIPTION_ID or cfg.HUB_SUBSCRIPTION_ID,
            d.get("source_rg", ""), d.get("source_resource"))
        src = azure_tools.subnet_details(r["subnet_id"]) if r.get("found") \
            else {"found": False, "message": r.get("message")}

    if src.get("found"):
        dns_note = (" Custom DNS: " + ", ".join(src["dns_servers"])) if src.get("dns_servers") \
            else " Uses Azure-provided (default) DNS."
        add("source", "Source location", "ok",
            f"{source or src.get('subnet')} sits in VNet '{src['vnet']}' / subnet '{src['subnet']}' "
            f"({src.get('address_prefix')}) in subscription {src['subscription']}."
            + (f" Route table: {src['route_table_id'].split('/')[-1]}." if src.get("route_table_id")
               else " No route table (UDR) attached.")
            + dns_note, src)
    else:
        add("source", "Source location", "warn",
            (src.get("message") or "Could not locate the source.")
            + " For a full trace, give a source IP that sits inside a hub/spoke VNet.")

    # ── 2/3. Destination + DNS ─────────────────────────────────────────────
    dest_ip = dest if _is_ip(dest) else None
    is_fqdn = bool(dest) and not dest_ip

    if is_fqdn:
        dns = azure_tools.private_dns_for_fqdn(dest, src.get("vnet_id") if src.get("found") else None)
        if not dns.get("checked"):
            add("dns", "DNS resolution", "info", f"FQDN '{dest}'. {dns.get('message', '')}")
        elif dns.get("zone"):
            if dns.get("source_linked"):
                add("dns", "DNS resolution", "ok",
                    f"Private DNS zone '{dns['zone']}' exists and IS linked to the source VNet — "
                    f"the source should resolve '{dest}'.", dns)
            elif dns.get("hub_linked"):
                add("dns", "DNS resolution", "warn",
                    f"Private DNS zone '{dns['zone']}' exists and is linked to the hub, but not directly "
                    f"to the source VNet. Resolution works only if the source's DNS is routed via the hub.", dns)
            else:
                add("dns", "DNS resolution", "fail",
                    f"Private DNS zone '{dns['zone']}' exists but is NOT linked to the source VNet or the hub "
                    f"— the source likely cannot resolve '{dest}'. A 'DNS: link zone to VNet' request is needed.", dns)
        else:
            add("dns", "DNS resolution", "warn",
                f"No private DNS zone in the hub covers '{dest}'. If it's an internal name, a DNS zone/link "
                f"may be missing; if public, resolution depends on the source's configured DNS.", dns)

        if run_live:
            res = _live("rnd", "ping", dest)          # ping resolves the name first
            if res.get("success"):
                v = res.get("verdict")
                if v == "dns":
                    add("dns_live", "Live DNS test (from connector VM)", "fail",
                        f"A connector VM could NOT resolve '{dest}' — {res.get('headline', '')}", res)
                else:
                    add("dns_live", "Live DNS test (from connector VM)", "ok",
                        f"A connector VM resolved '{dest}'. Note: the connector's resolver, not the "
                        f"source VNet's — use as a hint.", res)
            elif res.get("message"):
                add("dns_live", "Live DNS test (from connector VM)", "info", res["message"])

    internal = bool(dest_ip and _is_private(dest_ip))
    if dest_ip:
        add("dest", "Destination", "info",
            f"{dest_ip} is {'an internal (private)' if internal else 'an external (public)'} address."
            + ("" if internal else " External egress normally leaves via the hub firewall / NAT."))
    elif is_fqdn:
        add("dest", "Destination", "info",
            f"'{dest}' is an FQDN — resolve it to an IP (enable the live test) for a full routing trace.")

    # ── 4. Routing (source UDR → destination) ───────────────────────────────
    through_fw = False
    if src.get("found") and dest_ip:
        rl = azure_tools.route_lookup(src.get("route_table_id"), dest_ip)
        if not rl.get("has_udr"):
            add("route", "Routing (source UDR)", "info", rl.get("message", "System routes apply."))
        elif rl.get("match"):
            m = rl["match"]
            nh, nhip = m["next_hop_type"], m.get("next_hop_ip")
            through_fw = (nh == "VirtualAppliance")
            add("route", "Routing (source UDR)", "ok" if nh != "None" else "warn",
                f"Longest-match route '{m['name']}' ({m['prefix']}) → {nh}"
                + (f" via {nhip}" if nhip else "")
                + (" — traffic is steered to the hub firewall." if through_fw else
                   (" — traffic is BLACK-HOLED (next hop None)." if nh == "None" else "."))
                + "  (UDR only; system/BGP routes not shown.)", rl)
        else:
            add("route", "Routing (source UDR)", "info",
                f"No specific UDR route to {dest_ip} in table '{rl.get('table')}' "
                f"({rl.get('route_count')} route(s)) — Azure system routing applies "
                f"(local VNet / peering / default Internet).", rl)

    # ── 5. Firewall (destination coverage) ──────────────────────────────────
    if dest_ip:
        fwd = azure_tools.find_firewall_rules_for_address(dest_ip)
        if fwd.get("success"):
            matches = fwd.get("matches", [])
            denies = [m for m in matches if m.get("action") == "Deny"]
            allows = [m for m in matches if m.get("action") == "Allow"]
            if denies:
                add("firewall", "Firewall policy", "fail",
                    f"A DENY rule matches {dest_ip}: " + ", ".join(m["name"] for m in denies[:3])
                    + ". This blocks the traffic.", fwd)
            elif allows:
                add("firewall", "Firewall policy", "ok",
                    f"{len(allows)} ALLOW rule(s) cover {dest_ip} (e.g. '{allows[0]['name']}')."
                    + ("" if through_fw else " (Only relevant if the path goes via the firewall.)"), fwd)
            else:
                add("firewall", "Firewall policy", "warn" if through_fw else "info",
                    f"No firewall rule matches {dest_ip}."
                    + (" Since routing steers this to the firewall, it is likely blocked by default — "
                       "a firewall rule may be needed." if through_fw else
                       " Traffic doesn't appear to traverse the firewall, so this may not matter."), fwd)
        else:
            add("firewall", "Firewall policy", "info", fwd.get("message", ""))

    # ── 6. Return path (internal destination) ───────────────────────────────
    if internal and dest_ip and src_ip:
        dloc = azure_tools.locate_ip(dest_ip)
        if dloc.get("found"):
            rr = azure_tools.route_lookup(dloc.get("route_table_id"), src_ip)
            if rr.get("has_udr") and rr.get("match"):
                add("return", "Return path (destination → source)", "ok",
                    f"Destination subnet '{dloc['subnet']}' routes back toward {src_ip}: "
                    f"{rr['match']['prefix']} → {rr['match']['next_hop_type']}.", rr)
            elif rr.get("has_udr"):
                add("return", "Return path (destination → source)", "warn",
                    f"Destination subnet '{dloc['subnet']}' has a UDR but no explicit return route to "
                    f"{src_ip} — asymmetric routing is possible (relies on system/peering routes).", rr)
            else:
                add("return", "Return path (destination → source)", "info",
                    f"Destination subnet '{dloc['subnet']}' has no UDR — system routes handle the return.")
        else:
            add("return", "Return path", "warn",
                f"Could not locate {dest_ip} in known VNets — it may be in another subscription; "
                f"verify the return route manually.")

    # ── 7. External reachability (live) ─────────────────────────────────────
    if run_live and not internal and (dest_ip or is_fqdn):
        if port:
            res = _live("rnd", "telnet", dest, port)
        elif dest.startswith(("http://", "https://")):
            res = _live("rnd", "curl", dest)
        else:
            res = _live("rnd", "ping", dest)
        if res.get("success"):
            add("reach", "External reachability (from connector VM)",
                "ok" if res.get("verdict") == "reachable" else "warn",
                f"{res.get('headline', '')} {res.get('guidance', '')}".strip()
                + "  (Tested from a connector VM, not the exact source.)", res)
        elif res.get("message"):
            add("reach", "External reachability (from connector VM)", "info", res["message"])

    return {"steps": steps, **_verdict(steps),
            "meta": {"source": source, "destination": dest, "port": port}}


def _llm_available() -> bool:
    p = (cfg.AGENT_PROVIDER or "").lower()
    if p == "anthropic":
        return bool(cfg.ANTHROPIC_API_KEY)
    if p in ("openai", "byom"):
        return bool(cfg.OPENAI_API_KEY or cfg.OPENAI_BASE_URL)
    return False


def _clean_llm(text: str) -> str:
    """Drop reasoning-model chain-of-thought (<think>…</think>) that some models
    emit before the answer, so only the final English answer is shown."""
    if not text:
        return text
    text = re.sub(r"(?is)<think>.*?</think>", "", text)          # closed blocks
    text = re.sub(r"(?is)^.*?</think>", "", text)                # dangling open block
    text = text.replace("<think>", "").replace("</think>", "")
    return text.strip()


def _llm_complete(system: str, user: str) -> str:
    """One-shot completion via the configured LLM (reuses the admin agent's client)."""
    import agent_admin as ag
    provider = (cfg.AGENT_PROVIDER or "").lower()
    client = ag._get_client()
    if provider == "anthropic":
        resp = client.messages.create(
            model=cfg.ANTHROPIC_MODEL or "claude-sonnet-4-6", max_tokens=700,
            system=system, messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")
    else:
        resp = client.chat.completions.create(
            model=cfg.OPENAI_MODEL or "gpt-4o", max_tokens=700,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        text = resp.choices[0].message.content or ""
    return _clean_llm(text)


def summarize(report: dict, details: dict) -> str:
    """Ask the LLM for a plain-language explanation + recommended fix. Returns
    None if no LLM is configured or the call fails (best-effort)."""
    if not _llm_available():
        return None
    d = details or {}
    findings = "\n".join(f"- [{s['status'].upper()}] {s['title']}: {s['detail']}"
                         for s in report.get("steps", []))
    system = (
        "You are a senior Azure network engineer helping an operations admin triage a "
        "connectivity ticket in an Azure hub-and-spoke network (Azure Firewall in the hub, "
        "user-defined routes on spoke subnets, Azure Private DNS). You are given an automated, "
        "read-only diagnosis. Respond in ENGLISH ONLY. Do NOT include any reasoning, chain-of-thought "
        "or <think> tags — output only the final answer. Write a SHORT plain-language answer "
        "(max ~130 words, no preamble): "
        "(1) what is MOST LIKELY wrong (or that the path looks healthy), citing the specific findings, "
        "and (2) a concrete recommended fix / next action for the admin. Remember the checks read "
        "control-plane config and can't see the source VM/pod or Azure system/BGP routes, so hedge "
        "appropriately and suggest what to verify on the source when the path looks clear.")
    user = (f"Reported issue: {d.get('issue') or '(none given)'}\n"
            f"Source: {d.get('source') or d.get('source_resource') or '(none)'} ({d.get('source_kind') or 'ip'})\n"
            f"Destination: {d.get('destination') or '(none)'}"
            + (f" port {d.get('dest_port')}" if d.get('dest_port') else "") + "\n"
            f"Overall verdict: {report.get('verdict')}\n\nFindings:\n{findings}")
    try:
        return _llm_complete(system, user)
    except Exception as exc:
        log.error("netdiag.summarize failed: %s", exc)
        return None


def _verdict(steps: list) -> dict:
    """Roll the step statuses up into an overall verdict + likely cause."""
    fails = [s for s in steps if s["status"] == "fail"]
    warns = [s for s in steps if s["status"] == "warn"]
    if fails:
        return {"verdict": "blocked",
                "cause": "Likely cause: " + fails[0]["detail"]}
    if warns:
        return {"verdict": "attention",
                "cause": "Needs attention: " + "; ".join(w["title"] for w in warns) + "."}
    return {"verdict": "clear",
            "cause": "No blocking misconfiguration found in routing, DNS or firewall along the checked path."}
