"""
Assert-based checks for resourcegraph.py's pure logic — no Flask app, no DB,
no Azure needed. Run: python scripts/test_resourcegraph_validation.py

Mirrors scripts/test_storage_validation.py's style (assert-based, no pytest).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from resourcegraph import (
    _walk, _category_for_type, _extract_edges, _expand_subnets,
    _resolve_cmk_edges, build_graph,
)
from collections import defaultdict
import resourcegraph as rg

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAILED: {name}"
    passed += 1
    print(f"  ok: {name}")


# ── _walk: plain path ───────────────────────────────────────────────────
print("_walk")
check("simple nested path", list(_walk({"a": {"b": "id1"}}, "a.b")) == ["id1"])
check("missing path yields nothing", list(_walk({"a": {}}, "a.b")) == [])
check("non-string leaf ignored", list(_walk({"a": {"b": 5}}, "a.b")) == [])

# ── _walk: array expansion ──────────────────────────────────────────────
obj = {"items": [{"id": "id1"}, {"id": "id2"}, {"id": None}]}
check("array expand collects all ids", sorted(_walk(obj, "items[].id")) == ["id1", "id2"])

# ── _walk: ARM sub-resource properties-wrapper fallthrough ──────────────
# Real NICs shape ipConfigurations[] entries as {id, name, properties: {subnet: {...}}}
# — NOT {id, name, subnet: {...}} — discovered against real Azure.
nic_props = {"ipConfigurations": [
    {"id": "ipcfg1", "name": "ipconfig1", "properties": {"subnet": {"id": "subnetA"}}},
]}
check("array item wrapped in its own 'properties' is still found",
      list(_walk(nic_props, "ipConfigurations[].subnet.id")) == ["subnetA"])

# ── _walk: dict-keys-as-ids (identity.userAssignedIdentities) ───────────
obj2 = {"identity": {"userAssignedIdentities": {"/sub/rg/idA": {}, "/sub/rg/idB": {}}}}
check("dict-keys expand", sorted(_walk(obj2, "identity.userAssignedIdentities{}")) ==
      ["/sub/rg/idA", "/sub/rg/idB"])

# ── nested array + dict path (VM data disks) ────────────────────────────
obj3 = {"storageProfile": {"dataDisks": [{"managedDisk": {"id": "diskA"}},
                                          {"managedDisk": {"id": "diskB"}}]}}
check("nested array-then-dict path", sorted(_walk(obj3, "storageProfile.dataDisks[].managedDisk.id")) ==
      ["diskA", "diskB"])

# ── _category_for_type ───────────────────────────────────────────────────
print("\n_category_for_type")
check("VM -> compute", _category_for_type("Microsoft.Compute/virtualMachines") == "compute")
check("VNet -> network", _category_for_type("microsoft.network/virtualNetworks") == "network")
check("subnet -> network (not confused with vnet-only match)",
      _category_for_type("microsoft.network/virtualNetworks/subnets") == "network")
check("AKS -> platform", _category_for_type("Microsoft.ContainerService/managedClusters") == "platform")
check("Storage account -> storage", _category_for_type("Microsoft.Storage/storageAccounts") == "storage")
check("Key Vault -> security", _category_for_type("Microsoft.KeyVault/vaults") == "security")
check("unknown type -> other", _category_for_type("microsoft.something/unknown") == "other")

# ── _extract_edges: declarative REFERENCE_PATHS ──────────────────────────
print("\n_extract_edges")
vm_row = {
    "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
    "type": "microsoft.compute/virtualmachines",
    "properties": {
        "networkProfile": {"networkInterfaces": [{"id": "nic1"}]},
        "storageProfile": {"osDisk": {"managedDisk": {"id": "osdisk1"}},
                            "dataDisks": [{"managedDisk": {"id": "datadisk1"}}]},
    },
}
edges = list(_extract_edges(vm_row))
labels = {(t, l) for _, t, l in edges}
check("VM -> NIC edge present", ("nic1", "uses_nic") in labels)
check("VM -> OS disk edge present", ("osdisk1", "uses_disk") in labels)
check("VM -> data disk edge present", ("datadisk1", "uses_disk") in labels)

# ── _expand_subnets: synthetic subnet nodes from embedded VNet properties ─
print("\n_expand_subnets")
vnet_id = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet1"
subnet_id2 = vnet_id + "/subnets/subnet1"
nsg_id = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg1"
pe_id = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/privateEndpoints/pe1"
vnet_row = {
    "id": vnet_id, "type": "microsoft.network/virtualnetworks", "resourceGroup": "rg", "location": "uaenorth",
    "properties": {"subnets": [{
        "id": subnet_id2, "name": "subnet1",
        "properties": {"networkSecurityGroup": {"id": nsg_id},
                        "privateEndpoints": [{"id": pe_id}]},
    }]},
}
id_map, forward = {}, defaultdict(list)
_expand_subnets(vnet_row, id_map, forward)
check("subnet synthetic node created", subnet_id2.lower() in id_map)
check("subnet -> vnet child_of edge", (vnet_id.lower(), "child_of") in forward[subnet_id2.lower()])
check("subnet -> nsg protected_by edge", (nsg_id.lower(), "protected_by") in forward[subnet_id2.lower()])
check("PE -> subnet attached_to edge (reverse direction of embedding)",
      (subnet_id2.lower(), "attached_to") in forward[pe_id.lower()])

# ── _resolve_cmk_edges: URI-to-KeyVault-resource-ID resolution ───────────
print("\n_resolve_cmk_edges")
kv_id = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.KeyVault/vaults/kv1"
storage_id = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/sa1"
rows = [
    {"id": kv_id, "type": "microsoft.keyvault/vaults", "name": "kv1", "properties": {}},
    {"id": storage_id, "type": "microsoft.storage/storageaccounts", "name": "sa1",
     "properties": {"encryption": {"keyVaultProperties": {"keyVaultUri": "https://kv1.vault.azure.net/"}}}},
]
id_map2, forward2 = {}, defaultdict(list)
_resolve_cmk_edges(rows, id_map2, forward2)
check("storage -> KV encrypted_by edge resolved from URI",
      (kv_id.lower(), "encrypted_by") in forward2[storage_id.lower()])

# ── build_graph: deterministic hop-based truncation (mocked _arg) ────────
print("\nbuild_graph (mocked ARG, no real Azure)")


def make_vm_chain(n):
    """vm0 -> vm1 -> ... each VM's NIC ID happens to equal the next VM's id, purely to build
    a long deterministic chain via the existing uses_nic path for truncation testing."""
    rows = []
    for i in range(n):
        rid = f"/subscriptions/s/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm{i}"
        nic = f"/subscriptions/s/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm{i+1}" if i + 1 < n else None
        props = {"networkProfile": {"networkInterfaces": [{"id": nic}]}} if nic else {}
        rows.append({"id": rid, "name": f"vm{i}", "type": "microsoft.compute/virtualmachines",
                     "resourceGroup": "rg", "location": "uaenorth", "properties": props})
    return rows


chain_rows = make_vm_chain(8)
rg._arg = lambda query, subs: chain_rows  # monkeypatch — no network/token call

result = build_graph("fake-sub", resource_name="vm0", resource_type="microsoft.compute/virtualmachines")
check("root resolved to vm0", result["root"] == chain_rows[0]["id"])
check("hop_limit default respected (3)", result["hop_limit"] == 3)
check("BFS from vm0 with hop_limit=3 includes exactly vm0..vm3 (4 nodes)", len(result["nodes"]) == 4)
check("not truncated when within node cap", result["truncated"] is False)

# type-only seed (no name) — the form allows this; it must not silently
# fall back to "everything in scope" and drop the type filter.
result_type = build_graph("fake-sub", resource_type="microsoft.compute/virtualmachines")
check("type-only seed includes every VM as a hop-0 seed",
      {n["name"] for n in result_type["nodes"] if n["type"] == "microsoft.compute/virtualmachines"} >=
      {"vm0", "vm1", "vm2", "vm3", "vm4", "vm5"})
check("type-only seed has no single root", result_type["root"] is None)

result_missing_type = build_graph("fake-sub", resource_type="microsoft.storage/storageaccounts")
check("type-only seed with no matches returns a clean error, not an empty whole-scope graph",
      result_missing_type.get("error") is not None and len(result_missing_type["nodes"]) == 0)

# now force a tiny node cap to prove deterministic breadth-first truncation
orig_max_nodes = os.environ.get("RESGRAPH_MAX_NODES")
os.environ["RESGRAPH_MAX_NODES"] = "2"
try:
    result2 = build_graph("fake-sub", resource_name="vm0", resource_type="microsoft.compute/virtualmachines")
    check("capped result respects max_nodes (<=2 kept before truncation flags)", len(result2["nodes"]) <= 2)
    check("truncated flag set when cap hit", result2["truncated"] is True)
    check("truncated_at_hop recorded", result2["truncated_at_hop"] is not None)
finally:
    if orig_max_nodes is None:
        os.environ.pop("RESGRAPH_MAX_NODES", None)
    else:
        os.environ["RESGRAPH_MAX_NODES"] = orig_max_nodes

# ── hub_id + tags/subscriptionId (UI-polish additions) ───────────────────
# Direct cfg attribute monkeypatching, not env vars: this sandbox's real DB
# already has HUB_* settings configured for actual hub-spoke automation (a
# DB override), and DB overrides win over env vars in config.py's resolve()
# order — an env-var-only approach silently tests against the real
# configured hub instead of the values this test sets, which is exactly
# what happened the first time this was written (caught by the test itself
# failing against real data, not a mock).
from config import cfg
_hub_keys = ("HUB_SUBSCRIPTION_ID", "HUB_RESOURCE_GROUP", "HUB_VNET_NAME")
_orig_hub = {k: cfg.__dict__.get(k, "__unset__") for k in _hub_keys}


def _set_hub(sub, rg_, name):
    for k, v in zip(_hub_keys, (sub, rg_, name)):
        cfg.__dict__[k] = v


def _restore_hub():
    for k, v in _orig_hub.items():
        if v == "__unset__":
            cfg.__dict__.pop(k, None)
        else:
            cfg.__dict__[k] = v


_set_hub("fake-sub", "rg-hub", "vnet-hub")
try:
    result3 = build_graph("fake-sub", resource_name="vm0", resource_type="microsoft.compute/virtualmachines")
    check("hub_id built from HUB_* settings, no Azure call needed",
          result3["hub_id"] == "/subscriptions/fake-sub/resourceGroups/rg-hub/providers/Microsoft.Network/virtualNetworks/vnet-hub")
finally:
    _restore_hub()

_set_hub("", "", "")
try:
    result4 = build_graph("fake-sub", resource_name="vm0", resource_type="microsoft.compute/virtualmachines")
    check("hub_id blank when HUB_* settings aren't fully configured", result4["hub_id"] == "")
finally:
    _restore_hub()

tagged_rows = [{
    "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network/routeTables/rt1",
    "name": "rt1", "type": "microsoft.network/routetables", "resourceGroup": "rg",
    "location": "uaenorth", "subscriptionId": "s", "tags": {"Owner": "Atul", "Env": "Prod"},
    "properties": {"provisioningState": "Succeeded"},
}]
rg._arg = lambda query, subs: tagged_rows
result5 = build_graph("s", resource_name="rt1", resource_type="microsoft.network/routetables")
node = result5["nodes"][0]
check("tags surfaced in trimmed node properties", node["properties"].get("tags") == {"Owner": "Atul", "Env": "Prod"})
check("subscriptionId surfaced in trimmed node properties", node["properties"].get("subscriptionId") == "s")

print(f"\n{passed} checks passed.")
