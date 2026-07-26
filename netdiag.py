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


def _hub_fw_ip() -> str:
    return (cfg.HUB_FIREWALL_PRIVATE_IP or "").strip()


def _is_private_domain(fqdn: str) -> bool:
    """Private if it contains 'privatelink' or ends with a configured private suffix."""
    f = str(fqdn or "").lower().rstrip(".")
    if "privatelink" in f:
        return True
    for s in (cfg.PRIVATE_DNS_SUFFIXES or "").split(","):
        s = s.strip().lower()
        if s and (f == s or f.endswith("." + s)):
            return True
    return False


def _addr_in(entries, ip) -> bool:
    """Does an address list ('*', CIDR, IP, or a-b range) contain the IP?"""
    try:
        q = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    for e in (entries or []):
        e = str(e).strip()
        if e in ("*", "0.0.0.0/0"):
            return True
        if "-" in e and "/" not in e:
            try:
                lo, hi = [ipaddress.ip_address(x.strip()) for x in e.split("-", 1)]
                if lo <= q <= hi:
                    return True
            except (ValueError, TypeError):
                pass
            continue
        try:
            if q in ipaddress.ip_network(e, strict=False):
                return True
        except ValueError:
            pass
    return False


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

    # ── 2. Destination: classify PRIVATE vs PUBLIC, resolve to an IP ─────────
    dest_ip = dest if _is_ip(dest) else None
    dest_class = None                          # "private" | "public"
    if dest_ip:
        dest_class = "private" if _is_private(dest_ip) else "public"
        add("dest", "Destination", "info",
            f"{dest_ip} is {'a private (internal)' if dest_class == 'private' else 'a public (external)'} IP address.")
    elif dest:                                 # FQDN
        if _is_private_domain(dest):
            dest_class = "private"
            r = azure_tools.resolve_private_fqdn(dest)
            if r.get("resolved"):
                dest_ip = (r.get("ip") or [None])[0]
                add("dns", "DNS — private name", "ok",
                    f"'{dest}' is treated as a PRIVATE domain. It resolves in private DNS zone '{r['zone']}' "
                    f"→ {', '.join(r.get('ip') or [])}. Tracing to {dest_ip}.", r)
            else:
                add("dns", "DNS — private name", "fail",
                    f"'{dest}' is a PRIVATE domain but does NOT resolve: {r.get('message', '')} "
                    f"This is the connectivity blocker — fix DNS (create the record / link the zone) first.", r)
        else:
            dest_class = "public"
            add("dns", "DNS — public name", "ok",
                f"'{dest}' is a PUBLIC domain — resolvable on public DNS; treated as an external destination.")

    # ── 3. Next hop for the destination (source UDR) ────────────────────────
    routed_via_fw = False
    unreachable = False
    probe = dest_ip or ("1.1.1.1" if dest_class == "public" else None)
    if src.get("found") and probe:
        rl = azure_tools.route_lookup(src.get("route_table_id"), probe)
        m = rl.get("match") or {}
        nh, nhip = m.get("next_hop_type"), m.get("next_hop_ip")
        if not rl.get("has_udr") or not m:
            add("route", "Next hop (source UDR)", "info",
                (rl.get("message") or f"No UDR route to {probe}.")
                + " System routing applies (local VNet / peering / default Internet); reachability is judged "
                  "by peering below. System/BGP routes aren't visible here.", rl)
        elif nh == "None":
            unreachable = True
            add("route", "Next hop (source UDR)", "fail",
                f"Route '{m['name']}' ({m['prefix']}) → next hop None: traffic to {probe} is BLACK-HOLED "
                f"(dropped). Network unreachable.", rl)
        elif nh == "VirtualAppliance":
            routed_via_fw = True
            is_hub_fw = bool(nhip and _hub_fw_ip() and nhip == _hub_fw_ip())
            add("route", "Next hop (source UDR)", "ok" if is_hub_fw else "warn",
                f"Route '{m['name']}' ({m['prefix']}) → virtual appliance {nhip or '(no IP)'}"
                + (" — the hub firewall." if is_hub_fw
                   else f" — NOT the configured hub firewall IP ({_hub_fw_ip() or 'unset'}); verify this appliance."), rl)
        elif nh == "Internet":
            add("route", "Next hop (source UDR)", "ok" if dest_class == "public" else "warn",
                f"Route '{m['name']}' ({m['prefix']}) → Internet: egress goes straight out (bypassing the firewall).", rl)
        else:                                   # VnetPeering / VirtualNetwork / Gateway / VnetLocal
            add("route", "Next hop (source UDR)", "ok",
                f"Route '{m['name']}' ({m['prefix']}) → {nh}: reached directly over the VNet/peering "
                f"(not via the firewall).", rl)

    # ── 4. Reachability via peering (internal destination) ──────────────────
    dloc = None
    if dest_class == "private" and dest_ip and not unreachable:
        dloc = azure_tools.locate_ip(dest_ip)
        if dloc.get("found"):
            hub_id = azure_tools._hub_vnet_id().lower()
            if src.get("found") and dloc.get("vnet_id", "").lower() == src.get("vnet_id", "").lower():
                add("peering", "Reachability", "ok",
                    f"Destination {dest_ip} is in the SAME VNet ('{dloc['vnet']}') as the source — directly reachable.", dloc)
            else:
                dp = azure_tools.vnet_peerings(dloc["subscription"], dloc["resource_group"], dloc["vnet"])
                dest_remotes = [p["remote_id"] for p in dp.get("peerings", []) if "Connected" in p["state"]]
                dest_to_hub = hub_id in dest_remotes
                src_to_dest = False
                if src.get("found"):
                    sp = azure_tools.vnet_peerings(src["subscription"], src["resource_group"], src["vnet"])
                    src_remotes = [p["remote_id"] for p in sp.get("peerings", []) if "Connected" in p["state"]]
                    src_to_dest = dloc.get("vnet_id", "").lower() in src_remotes
                if src_to_dest:
                    add("peering", "Reachability (peering)", "ok",
                        f"Source VNet is directly peered with the destination VNet '{dloc['vnet']}' — reachable.", dp)
                elif routed_via_fw and dest_to_hub:
                    add("peering", "Reachability (via hub)", "ok",
                        f"Destination VNet '{dloc['vnet']}' is peered to the hub and the source routes via the hub "
                        f"firewall — reachable through the hub.", dp)
                elif routed_via_fw and not dest_to_hub:
                    unreachable = True
                    add("peering", "Reachability (via hub)", "fail",
                        f"NETWORK UNREACHABLE: the source routes via the hub firewall, but destination VNet "
                        f"'{dloc['vnet']}' is NOT peered to the hub — the firewall can't reach it. Peer it to the hub.", dp)
                else:
                    add("peering", "Reachability", "warn",
                        f"Destination VNet '{dloc['vnet']}' isn't directly peered with the source and the source has "
                        f"no firewall route to it — verify the path (spoke-to-spoke needs hub routing).", dp)
        else:
            unreachable = True
            add("peering", "Reachability", "fail",
                f"NETWORK UNREACHABLE: {dest_ip} is a private IP but isn't in any VNet visible/peered to this "
                f"network — it's not reachable from the source.", dloc)

    # ── 5. Firewall rule (only when routed via the firewall) ────────────────
    if routed_via_fw and not unreachable:
        if dest_ip and dest_class == "private":
            fwd = azure_tools.find_firewall_rules_for_address(dest_ip)
            if fwd.get("success"):
                matches = fwd.get("matches", [])
                denies = [x for x in matches if x.get("action") == "Deny" and x.get("match_destination")]
                allows = [x for x in matches if x.get("action") == "Allow" and x.get("match_destination")
                          and (not src_ip or _addr_in(x.get("sources"), src_ip))]
                if denies:
                    add("firewall", "Firewall rule", "fail",
                        f"A DENY rule matches {dest_ip}: " + ", ".join(x["name"] for x in denies[:3])
                        + ". The firewall blocks this.", fwd)
                elif allows:
                    add("firewall", "Firewall rule", "ok",
                        f"An ALLOW rule permits {src_ip + ' -> ' if src_ip else ''}{dest_ip} "
                        f"(e.g. '{allows[0]['name']}').", fwd)
                else:
                    add("firewall", "Firewall rule", "fail",
                        f"No firewall rule allows {src_ip + ' -> ' if src_ip else ''}{dest_ip}. Traffic via the "
                        f"firewall is denied by default — a firewall rule is needed.", fwd)
            else:
                add("firewall", "Firewall rule", "info", fwd.get("message", ""))
        else:
            add("firewall", "Firewall (egress)", "info",
                "Public egress via the hub firewall — ensure an application rule (FQDN) or network rule allows "
                "the source to this destination.")

    # ── 6. Return route (private) / public availability ─────────────────────
    if dest_class == "private" and dest_ip and dloc and dloc.get("found") and not unreachable:
        rr = azure_tools.route_lookup(dloc.get("route_table_id"), src_ip) if src_ip else {"has_udr": None}
        if rr.get("has_udr") and rr.get("match"):
            add("return", "Return route (destination -> source)", "ok",
                f"Destination subnet '{dloc['subnet']}' routes back toward {src_ip}: "
                f"{rr['match']['prefix']} -> {rr['match']['next_hop_type']}. Path is symmetric.", rr)
        elif rr.get("has_udr"):
            add("return", "Return route (destination -> source)", "warn",
                f"Destination subnet '{dloc['subnet']}' has a UDR but no explicit return route to {src_ip} "
                f"— possible asymmetric routing (relies on system/peering routes).", rr)
        elif src_ip:
            add("return", "Return route", "info",
                f"Destination subnet '{dloc['subnet']}' has no UDR — system routes handle the return path.")
    elif dest_class == "public" and not unreachable:
        add("avail", "External availability", "ok",
            "Public destination — considered available on the internet. If egress still fails, it's the "
            "firewall rule or routing, not availability.")

    # ── 7. Live test from a connector VM (optional) ─────────────────────────
    if run_live and dest and not unreachable:
        method = "telnet" if port else ("curl" if dest.startswith(("http://", "https://")) else "ping")
        res = _live("rnd", method, dest, port or None)
        if res.get("success"):
            add("reach", "Live test (from connector VM)",
                "ok" if res.get("verdict") == "reachable" else ("fail" if res.get("verdict") == "unreachable" else "warn"),
                f"{res.get('headline', '')} {res.get('guidance', '')}".strip()
                + "  (From a connector VM, not the exact source.)", res)
        elif res.get("message"):
            add("reach", "Live test (from connector VM)", "info", res["message"])

    return {"steps": steps, **_verdict(steps),
            "meta": {"source": source, "destination": dest, "port": port}}

def _llm_available() -> bool:
    p = (cfg.AGENT_PROVIDER or "").lower()
    if p == "anthropic":
        return bool(cfg.ANTHROPIC_API_KEY)
    if p in ("openai", "byom"):
        return bool(cfg.OPENAI_API_KEY or cfg.OPENAI_BASE_URL)
    return False


# Any CJK (Chinese / Japanese / Korean) character — the tell-tale of leaked
# reasoning from models that "think" in another language before answering.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힣ｦ-ﾟ]")

# Appended to EVERY system prompt so every AI module is English-only by default.
_ENGLISH_GUARD = (
    "\n\nOUTPUT RULES (critical, override anything above that conflicts): "
    "Write the response in ENGLISH ONLY. Never output Chinese, Japanese, Korean or any "
    "non-English characters. Do NOT reveal your reasoning, analysis, or restate the task. "
    "If any thinking precedes your answer, end that thinking with a line reading exactly "
    "'FINAL ANSWER:' and put ONLY the finished answer after it.")


def _has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def _clean_llm(text: str) -> str:
    """Return the model's final English answer, or '' if the output is leaked
    reasoning / non-English.

    Handles reasoning models three ways: strips <think>/<thinking>/<reasoning>/
    <analysis> tag blocks; if a 'FINAL ANSWER:' marker is present, keeps only what
    follows it (salvaging a clean English answer that trailed some reasoning); and
    finally REJECTS (returns '') anything that still contains CJK characters, so a
    Chinese chain-of-thought can never reach the UI."""
    if not text:
        return ""
    # Remove reasoning wrapped in tags (closed blocks and a dangling open block).
    text = re.sub(r"(?is)<(think|thinking|reasoning|analysis)>.*?</\1>", "", text)
    text = re.sub(r"(?is)^.*?</(?:think|thinking|reasoning|analysis)>", "", text)
    text = re.sub(r"(?i)</?(?:think|thinking|reasoning|analysis)>", "", text)
    # If the model marked its final answer, keep only that (drops leading reasoning).
    marks = list(re.finditer(r"(?i)final answer\s*[:：]?", text))
    if marks:
        text = text[marks[-1].end():]
    text = text.strip()
    # Hard guarantee: any residual CJK means leaked/non-English output — reject it.
    if _has_cjk(text):
        return ""
    return text


def _llm_complete(system: str, user: str) -> str:
    """One-shot completion via the configured LLM (reuses the admin agent's client).
    Returns the cleaned, ENGLISH-ONLY final answer, or '' if the model leaked
    non-English reasoning (callers fall back to their template / 'unavailable')."""
    import agent_admin as ag
    provider = (cfg.AGENT_PROVIDER or "").lower()
    client = ag._get_client()
    system = (system or "") + _ENGLISH_GUARD
    if provider == "anthropic":
        resp = client.messages.create(
            model=cfg.ANTHROPIC_MODEL or "claude-sonnet-4-6", max_tokens=1500,
            system=system, messages=[{"role": "user", "content": user}])
        text = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", "") == "text")
    else:
        resp = client.chat.completions.create(
            model=cfg.OPENAI_MODEL or "gpt-4o", max_tokens=1500,
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
        return _llm_complete(system, user) or None   # '' => leaked/non-English, drop it
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
