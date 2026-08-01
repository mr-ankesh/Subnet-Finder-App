"""
Assert-based coverage of Storage Account request validation
(app._validate_storage_request) — name/container/IP-rule rules, option
membership, and the live-Azure checks, with azure_tools stubbed out so this
runs with no real Azure reachability and no test framework, consistent with
this repo's "no test suite" convention (see scripts/preview_notification_email.py
for the same import-a-module-directly, no-pytest pattern).

Usage:
    ./.venv/bin/python scripts/test_storage_validation.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app                          # noqa: E402
import azure_tools                  # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL: {label}")


def base_details(**overrides):
    d = {
        "storage_account_name": "stgtest001", "resource_group": "rg-test",
        "subscription_id": "00000000-0000-0000-0000-000000000000", "region": "uaenorth", "env": "Dev",
        "storage_kind": "StorageV2", "sku": "Standard_LRS", "access_tier": "Hot",
        "public_network_access": "Disabled", "identity_type": "system", "encryption_type": "microsoft_managed",
    }
    d.update(overrides)
    return d


# Every live-Azure call inside _validate_storage_request stubbed to "everything
# is reachable and available" by default — individual tests override as needed.
AVAILABLE = {"success": True, "available": True}
NOT_AVAILABLE = {"success": True, "available": False, "reason": "AlreadyExists", "message": "Name already in use."}
VNETS_OK = {"success": True, "vnets": [{"name": "vnet-x", "resource_group": "rg-test", "location": "uaenorth"}]}
SUBNETS_OK = {"success": True, "subnets": [{"name": "subnet-x"}]}
KEYS_OK = {"success": True, "keys": [{"name": "key-x", "versions": ["v1", "v2"]}]}


def run_validate(details):
    with patch.object(azure_tools, "check_storage_name_availability", return_value=AVAILABLE), \
         patch.object(azure_tools, "list_vnets", return_value=VNETS_OK), \
         patch.object(azure_tools, "list_subnets", return_value=SUBNETS_OK), \
         patch.object(azure_tools, "list_keyvault_keys", return_value=KEYS_OK):
        return app._validate_storage_request(details)


def main():
    # Name rules
    check("valid name passes", run_validate(base_details()) is None)
    check("uppercase name rejected", run_validate(base_details(storage_account_name="StgTest")) is not None)
    check("too-short name rejected", run_validate(base_details(storage_account_name="ab")) is not None)
    check("hyphen in name rejected", run_validate(base_details(storage_account_name="stg-test")) is not None)

    # Option membership
    check("bad storage_kind rejected", run_validate(base_details(storage_kind="Bogus")) is not None)
    check("bad sku rejected", run_validate(base_details(sku="Bogus")) is not None)
    check("bad access_tier rejected", run_validate(base_details(access_tier="Bogus")) is not None)
    check("bad public_network_access rejected",
          run_validate(base_details(public_network_access="Bogus")) is not None)
    check("bad identity_type rejected", run_validate(base_details(identity_type="Bogus")) is not None)
    check("bad encryption_type rejected", run_validate(base_details(encryption_type="Bogus")) is not None)

    # Region justification
    check("non-standard region without justification rejected",
          run_validate(base_details(region="eastus")) is not None)
    check("non-standard region with justification passes",
          run_validate(base_details(region="eastus", region_justification="needed for X")) is None)

    # Identity / CMK presence
    check("user identity without id rejected",
          run_validate(base_details(identity_type="user")) is not None)
    check("user identity with id passes",
          run_validate(base_details(identity_type="user",
                                    user_assigned_identity_id="/subscriptions/x/.../uami")) is None)
    check("CMK without vault/key rejected",
          run_validate(base_details(encryption_type="customer_managed")) is not None)
    check("CMK with vault/key passes",
          run_validate(base_details(encryption_type="customer_managed",
                                    cmk_keyvault_name="kv-x", cmk_key_name="key-x")) is None)
    check("CMK with unknown key rejected",
          run_validate(base_details(encryption_type="customer_managed",
                                    cmk_keyvault_name="kv-x", cmk_key_name="no-such-key")) is not None)

    # Allowed IPs — validity + dedup
    check("valid IP/CIDR list passes",
          run_validate(base_details(allowed_ips="20.1.2.3, 10.0.0.0/24")) is None)
    check("invalid IP rejected",
          run_validate(base_details(allowed_ips="not-an-ip")) is not None)
    check("duplicate IP rejected",
          run_validate(base_details(allowed_ips="20.1.2.3, 20.1.2.3")) is not None)

    # Containers — naming + dedup + cap
    check("valid container names pass",
          run_validate(base_details(containers='["raw-data", "curated"]')) is None)
    check("uppercase container name rejected",
          run_validate(base_details(containers='["Bad-Name"]')) is not None)
    check("too-short container name rejected",
          run_validate(base_details(containers='["ab"]')) is not None)
    check("duplicate container name rejected",
          run_validate(base_details(containers='["dup", "dup"]')) is not None)
    check("malformed containers JSON rejected",
          run_validate(base_details(containers='not json')) is not None)
    check("too many containers rejected",
          run_validate(base_details(containers=str([f"c{i}" for i in range(20)]).replace("'", '"'))) is not None)

    # Object replication
    check("replication missing fields rejected",
          run_validate(base_details(replication_enabled="true")) is not None)
    check("replication with all fields, source not in own containers rejected",
          run_validate(base_details(
              containers='["raw-data"]', replication_enabled="true",
              replication_source_container="not-a-container",
              replication_destination_account="dest001",
              replication_destination_container="dest-container")) is not None)
    # Destination account must appear to already EXIST (available=False) for
    # this case — only the main account name check should report available=True.
    def _name_check(sub, name):
        return NOT_AVAILABLE if name == "dest001" else AVAILABLE
    with patch.object(azure_tools, "check_storage_name_availability", side_effect=_name_check), \
         patch.object(azure_tools, "list_vnets", return_value=VNETS_OK), \
         patch.object(azure_tools, "list_subnets", return_value=SUBNETS_OK), \
         patch.object(azure_tools, "list_keyvault_keys", return_value=KEYS_OK):
        check("replication with valid source container passes",
              app._validate_storage_request(base_details(
                  containers='["raw-data"]', replication_enabled="true",
                  replication_source_container="raw-data",
                  replication_destination_account="dest001",
                  replication_destination_container="dest-container")) is None)

    # Live-Azure checks: name unavailable, VNet/subnet not found
    with patch.object(azure_tools, "check_storage_name_availability", return_value=NOT_AVAILABLE), \
         patch.object(azure_tools, "list_vnets", return_value=VNETS_OK), \
         patch.object(azure_tools, "list_subnets", return_value=SUBNETS_OK), \
         patch.object(azure_tools, "list_keyvault_keys", return_value=KEYS_OK):
        check("unavailable name rejected", app._validate_storage_request(base_details()) is not None)

    with patch.object(azure_tools, "check_storage_name_availability", return_value=AVAILABLE), \
         patch.object(azure_tools, "list_vnets", return_value={"success": True, "vnets": []}), \
         patch.object(azure_tools, "list_subnets", return_value=SUBNETS_OK), \
         patch.object(azure_tools, "list_keyvault_keys", return_value=KEYS_OK):
        check("VNet not found rejected",
              app._validate_storage_request(base_details(vnet_name="vnet-x", subnet_name="subnet-x")) is not None)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
