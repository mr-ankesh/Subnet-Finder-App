"""
Renders a pattern's Mermaid diagram template (advisor_kb/diagrams/*.mmd) by
placeholder substitution ONLY — this module never generates Mermaid syntax
from scratch (see advisor_kb/diagrams/README.md). Two named, pattern-specific
block-removal rules are also mandated by that same README and implemented
here explicitly (not as generic logic):

  1. The ZPA subgraph (present in storage_blob_private_standard's and
     storage_files_private_standard's diagrams) is removed entirely when
     derived.zpa_routing_required is false — "an unused ZPA box confuses
     more than it explains."
  2. storage_datalake_private's second (blob) private-endpoint block is
     removed unless the analytics engine is confirmed to also use the blob
     API (inferred from access_protocol containing rest_sdk/blobfuse
     alongside abfs — there's no separate question for this, so this is the
     most direct signal already captured).
  3. (six-service expansion) aks_private.mmd's GPU node-pool block is
     removed when gpu_required is false — the same file is shared by both
     aks_private_standard and aks_gpu_nodepool (see diagrams/README.md's own
     "Omit GPU node when gpu_required == false" comment in that file).

New placeholders for the six-service expansion: {BASE} (VM base-name
illustration — vm_workload.mmd), {BACKEND} (AppGW's backend description),
{HOSTNAME} (AppGW's public hostname). Like every other placeholder these
are illustrative diagram labels, not literal recommendations — {BASE} in
particular is never the same value as prefill.py's real vm_base_name field
(which the advisor never sources from any answer; see prefill.py's
docstring), just a readable stand-in for the diagram.

Every substituted value is escaped (< > " `) before insertion — placeholder
values come from user input, and the diagram is rendered client-side with
Mermaid's securityLevel: 'strict', but this module doesn't rely on that
alone. A post-substitution assertion guarantees no raw {TOKEN} survives.
"""
import re

from advisor.catalog_loader import KB_ROOT, load_text_file

_ENV_LABEL = {"dev": "Development", "tst": "Test", "uat": "UAT", "prd": "Production", "snd": "Sandbox"}
_ESCAPE_CHARS = str.maketrans({"<": "&lt;", ">": "&gt;", '"': "&quot;", "`": "&#96;"})

_ZPA_BLOCK_RE = re.compile(
    r'\n\s*subgraph USER\[.*?\n.*?\n\s*end\n', re.DOTALL)
_ZPA_EDGE_RE = re.compile(r'\n\s*ZPA --> SNET\n')
_ZPA_CLASS_RE = re.compile(r',?\s*ZPA\b')

_DATALAKE_PE2_BLOCK_RE = re.compile(r'\n\s*PE2\["Private Endpoint.*?\n')
_DATALAKE_DNS2_BLOCK_RE = re.compile(r'\n\s*DNS2\["privatelink\.blob\.core\.windows\.net"\]\n')
_DATALAKE_PE2_EDGES_RE = re.compile(
    r'\n\s*(SNET --- PE2|PE2 ==>.*|SNET -\.-> DNS2|DNS2 -\.-> PE2)\n')
_DATALAKE_CLASS_RE = re.compile(r'class SA,KV sec\n\s*class PE1,PE2,DNS1,DNS2,SNET net')

_AKS_GPU_NODE_RE = re.compile(r'\n\s*GPU\["GPU node pool.*?\n')
_AKS_GPU_EDGE_RE = re.compile(r'\n\s*SN --- GPU\n')
_AKS_GPU_CLASS_RE = re.compile(r'\n\s*class GPU warn\n')

# environment_full.mmd (Phase 3, the whole-environment composer): its own
# top-of-file comment mandates "OMIT the NET subgraph and CF/AGW entirely
# when exposure == internal_only" — implemented as its own named strip,
# same style as every other pattern-specific removal in this module.
_ENV_NET_SUBGRAPH_RE = re.compile(r'\n\s*subgraph NET\[.*?\n.*?\n\s*end\n', re.DOTALL)
_ENV_AGW_SUBGRAPH_RE = re.compile(r'\n\s*subgraph SN1\[.*?\n.*?\n\s*end\n', re.DOTALL)
_ENV_CF_AGW_EDGES_RE = re.compile(
    r'\n\s*(CF ==>\|"443 only"\| AGW|AGW --> AKS|AGW --> VMS)\n')
_ENV_PUB_CLASS_RE = re.compile(r'\n\s*class CF,AGW pub\n')


def _strip_environment_public_front_door(source: str) -> str:
    source = _ENV_NET_SUBGRAPH_RE.sub("\n", source)
    source = _ENV_AGW_SUBGRAPH_RE.sub("\n", source)
    source = _ENV_CF_AGW_EDGES_RE.sub("\n", source)
    source = _ENV_PUB_CLASS_RE.sub("\n", source)
    source = source.replace("class PE,DNSZ,FW,SN1,SN2,SN3,SN4 net",
                             "class PE,DNSZ,FW,SN2,SN3,SN4 net")
    return source


def _escape(value: str) -> str:
    return str(value).translate(_ESCAPE_CHARS)


def _placeholder_values(answers: dict) -> dict:
    env = answers.get("environment")
    protocols = answers.get("access_protocol") or []
    protocol_label = " / ".join(p for p in ("smb", "nfs_posix") if p in protocols) or None
    return {
        "APP": answers.get("application_name") or "Your workload",
        "ENGINE": answers.get("application_name") or "Analytics engine",
        "VNET": answers.get("vnet_name") or "<your spoke VNET>",
        "SUBNET": answers.get("subnet_name") or "<your subnet>",
        "SA_NAME": answers.get("storage_account_name") or "<storage account>",
        "ENV": _ENV_LABEL.get(env, env) or "<environment>",
        "PROTOCOL": protocol_label or "SMB / NFS",
        "RETENTION": answers.get("retention_period") or "<retention period>",
        "END_DATE": answers.get("workload_end_date") or "<agreed end date>",
        "BASE": _illustrative_base_name(answers),
        "BACKEND": _backend_label(answers),
        "HOSTNAME": answers.get("public_hostname") or "<public hostname>",
    }


_BACKEND_LABEL = {"aks": "AKS cluster (internal)", "vm": "VM(s) (internal)",
                  "app_service": "App Service (internal)", "other": "Backend (internal)"}


def _backend_label(answers: dict) -> str:
    return _BACKEND_LABEL.get(answers.get("backend"), "Backend (internal)")


def _illustrative_base_name(answers: dict) -> str:
    """Diagram-only illustration, never the real prefilled vm_base_name field
    (which the advisor never sources from an answer — see prefill.py)."""
    name = answers.get("application_name")
    if not name:
        return "vm"
    slug = re.sub(r"[^A-Za-z0-9-]+", "-", name.strip().lower()).strip("-")
    return slug[:20] or "vm"


def _strip_zpa_subgraph(source: str) -> str:
    source = _ZPA_BLOCK_RE.sub("\n", source)
    source = _ZPA_EDGE_RE.sub("\n", source)
    return source


def _engine_confirmed_uses_blob(answers: dict) -> bool:
    protocols = answers.get("access_protocol") or []
    return "abfs" in protocols and any(p in protocols for p in ("rest_sdk", "blobfuse"))


def _strip_datalake_blob_endpoint(source: str) -> str:
    source = _DATALAKE_PE2_BLOCK_RE.sub("\n", source)
    source = _DATALAKE_DNS2_BLOCK_RE.sub("\n", source)
    source = _DATALAKE_PE2_EDGES_RE.sub("\n", source)
    source = _DATALAKE_CLASS_RE.sub("class SA,KV sec\n    class PE1,DNS1,SNET net", source)
    return source


def _strip_aks_gpu_node(source: str) -> str:
    source = _AKS_GPU_NODE_RE.sub("\n", source)
    source = _AKS_GPU_EDGE_RE.sub("\n", source)
    source = _AKS_GPU_CLASS_RE.sub("\n", source)
    return source


def render(pattern: dict, answers: dict, derived: dict) -> str:
    diagram_path = KB_ROOT / "diagrams" / pattern["diagram"]
    source = diagram_path.read_text(encoding="utf-8")

    if pattern["id"] in ("storage_blob_private_standard", "storage_files_private_standard"):
        if not derived.get("zpa_routing_required"):
            source = _strip_zpa_subgraph(source)

    if pattern["id"] == "storage_datalake_private":
        if not _engine_confirmed_uses_blob(answers):
            source = _strip_datalake_blob_endpoint(source)

    if pattern["id"] in ("aks_private_standard", "aks_gpu_nodepool"):
        if not answers.get("gpu_required"):
            source = _strip_aks_gpu_node(source)

    values = _placeholder_values(answers)
    for token, value in values.items():
        source = source.replace(f"{{{token}}}", _escape(value))

    leftover = re.findall(r"\{[A-Z_]+\}", source)
    assert not leftover, f"diagram_builder left unsubstituted placeholders: {leftover}"

    return source


def render_environment(answers: dict, composition: dict) -> str:
    """environment_full.mmd, for the whole-environment composer — NOT tied
    to a single catalog pattern (there isn't one; the environment composes
    several), so this is a parallel entry point to render() rather than a
    reuse of it. Only {ENV}/{VNET_CIDR} are substituted; the public
    front-door subgraph is stripped entirely for an internal_only
    environment, per the diagram's own top-of-file comment."""
    diagram_path = KB_ROOT / "diagrams" / "environment_full.mmd"
    source = diagram_path.read_text(encoding="utf-8")

    if composition["exposure"].get("id") != "public_application":
        source = _strip_environment_public_front_door(source)

    env = answers.get("environment")
    values = {"ENV": _ENV_LABEL.get(env, env) or "<environment>",
              "VNET_CIDR": composition["network_plan"]["vnet_size"]}
    for token, value in values.items():
        source = source.replace(f"{{{token}}}", _escape(value))

    leftover = re.findall(r"\{[A-Z_]+\}", source)
    assert not leftover, f"diagram_builder left unsubstituted placeholders: {leftover}"

    return source
