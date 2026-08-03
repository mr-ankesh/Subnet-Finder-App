"""
Builds the prefill payload for the Storage Account request form, per
advisor_kb/mapping/storage_request_mapping.yaml. Prefill-only: this module
never calls _create_service_request()/_validate_storage_request() — it only
produces a dict the requester.html JS uses to populate [data-detail] fields
that remain fully editable. See advisor_kb/mapping's own prefill_contract.

IMPORTANT — form-field reality check (verified by reading the actual
templates/requester.html markup, not assumed from the KB alone): the KB's
semantic vocabulary doesn't always match this app's actual form field names
or <option> values. Four real mismatches found and translated here:

  1. identity_type:   KB "UserAssigned"      -> form option "user"
  2. encryption_type:  KB "CMK"               -> form option "customer_managed"
  3. sku:              storage_premium_temporary's design.sku is "Premium_ZRS",
                        which had no matching <option> on the form (only
                        Premium_LRS existed) — fixed by adding the option to
                        requester.html and to config.py's STORAGE_SKUS (a
                        genuine pre-existing gap this surfaced, not a new
                        restriction; _validate_storage_request only WIDENED).
  4. ServiceClass:     the KB's own mapping (criticality -> Bronze/Silver/Gold/
                        Platinum) uses vocabulary the form's actual
                        `service_class` <select> doesn't offer at all (its
                        options are Standard/Business Critical/Mission
                        Critical) — and the KB explicitly flags this mapping
                        as "inferred, not quoted from the design documents."
                        Rather than silently force either vocabulary onto a
                        field neither confirms, this is left for the user to
                        pick themselves; it's listed as a manual item, not
                        silently guessed.

Key Vault field name: mapping.yaml's `key_vault_name` is the form's
`cmk_keyvault_name`; `key_name` is `cmk_key_name`.

build_prefill() above stays storage-only and untouched. build_prefill_aks()/
build_prefill_vm() (six-service expansion) follow the exact same discipline
against templates/requester.html's real AKS/VM sections — two more real
mismatches found there:

  5. AKS/VM's own "ApplicationName" tag source: the KB's tag_mappings say
     `{from: application_name}`, but neither form has an `application_name`
     field at all — both use a field literally called `project` instead
     (same free-text purpose, different name).
  6. VM auth_mode: KB derives `admin_password_at_deploy` for Windows, but the
     form's real `auth_mode` <select> only offers `ssh_key`/`password`.

Neither builder prefills `cluster_name`/`vm_base_name`/`resource_group`/
`subscription_id`/`vnet_name`/`subnet_name` — the KB's own user_must_provide
lists categorize these as things the advisor never sources from an answer
(the same restraint storage's own build already applies to those same
fields), so writing something there anyway would be inventing a mapping the
KB doesn't sanction.
"""
import re

from advisor.catalog_loader import get_mapping
from advisor.condition_eval import evaluate, evaluate_safe, AttrDict

STORAGE_SERVICE = "storage_account"
AKS_SERVICE = "aks_cluster"
VM_SERVICE = "vm_create"

_IDENTITY_TYPE_FORM_VALUE = {"UserAssigned": "user", "SystemAssigned": "system"}
_ENCRYPTION_TYPE_FORM_VALUE = {"CMK": "customer_managed", "MMK": "microsoft_managed"}

_SERVICE_CLASS_BY_CRITICALITY = {"low": "Bronze", "medium": "Silver", "high": "Gold", "critical": "Platinum"}
_ENV_LABEL = {"dev": "Development", "tst": "Test", "uat": "UAT", "prd": "Production", "snd": "Sandbox"}

# VM's decision matrix derives auth_mode = linux -> ssh_key,
# windows -> admin_password_at_deploy; the real form's auth_mode <select>
# only has ssh_key/password. If VM_REQUIRE_SSH_KEY (Settings -> VM Defaults)
# forces SSH-only, the advisor doesn't know that live setting, so this
# translation is a best-effort default, flagged in the checklist, not a
# guarantee the requester never has to change it.
_VM_AUTH_MODE_FORM_VALUE = {"ssh_key": "ssh_key", "admin_password_at_deploy": "password"}


def _title(s):
    return (s or "").strip().title() if isinstance(s, str) else s


def _resolve_path(root: dict, path: str):
    """Walk a dotted path like 'pattern.design.sku' against a namespace dict
    whose top-level values may be AttrDict-wrapped."""
    parts = path.split(".")
    cur = root.get(parts[0])
    for p in parts[1:]:
        if cur is None:
            return None
        cur = cur.get(p) if isinstance(cur, dict) else getattr(cur, p, None)
    return cur


def _resolve_from(from_spec, ns: dict):
    if isinstance(from_spec, list):
        return [_resolve_from(f, ns) for f in from_spec]
    if "." in from_spec:
        return _resolve_path(ns, from_spec)
    return ns.get(from_spec)


def _label_of_option(question_id: str, value) -> str:
    from advisor.catalog_loader import get_questions
    for q in get_questions(STORAGE_SERVICE)["questions"]:
        if q["id"] == question_id:
            for opt in q.get("options", []):
                if opt["value"] == value:
                    return opt["label"]
    return str(value) if value is not None else ""


def _compose_business_justification(ns: dict, pattern: dict) -> str:
    purpose_label = _label_of_option("purpose", ns.get("purpose"))
    consumer = ns.get("consumer") or []
    consumer_labels = ", ".join(_label_of_option("consumer", c) for c in consumer) or "the requesting workload"
    application_name = ns.get("application_name") or "the application"
    capacity_estimate = ns.get("capacity_estimate") or "not specified"
    retention_period = ns.get("retention_period")
    retention_clause = f"Retention required: {retention_period}." if retention_period else ""
    return (
        f"{purpose_label} for {application_name}, accessed by {consumer_labels}. "
        f"Estimated size {capacity_estimate}. {retention_clause} "
        f"Recommended by AlMadar AI Architecture Advisor "
        f"(pattern: {pattern['id']}, KB {pattern.get('kb_version', '1.0.0')})."
    ).strip()


def build_prefill(pattern: dict, answers: dict, rule_result: dict) -> dict:
    """Returns {"fields": {...}, "tags": {...}, "user_must_provide": [...],
    "follow_on_requests": [...], "unmapped_note": str|None}."""
    derived = AttrDict(rule_result.get("derived") or {})
    pattern_ns = AttrDict(pattern)
    ns = {**answers, "derived": derived, "pattern": pattern_ns,
          "session": AttrDict({}), "region_justification": answers.get("region_justification")}

    fields = {}

    fields["region"] = answers.get("region") or "uaenorth"
    if fields["region"] != "uaenorth" and answers.get("region_justification"):
        fields["region_justification"] = answers["region_justification"]

    fields["storage_kind"] = pattern["design"].get("storage_kind", "StorageV2")

    sku = pattern["design"].get("sku")
    if not sku:
        # storage_archive_retention's design has no single sku (replication
        # is descriptive: "LRS or GRS") -- default to the cheaper of the two
        # KB-sanctioned options; still fully editable on the form.
        sku = "Standard_LRS"
    fields["sku"] = sku

    fields["access_tier"] = derived.access_tier or pattern["design"].get("access_tier", "Hot")
    fields["public_network_access"] = "Disabled"

    if answers.get("vnet_name"):
        fields["vnet_name"] = answers["vnet_name"]
    if answers.get("subnet_name"):
        fields["subnet_name"] = answers["subnet_name"]

    fields["identity_type"] = _IDENTITY_TYPE_FORM_VALUE.get("UserAssigned", "user")
    fields["encryption_type"] = _ENCRYPTION_TYPE_FORM_VALUE.get("CMK", "customer_managed")
    if answers.get("key_vault_name"):
        fields["cmk_keyvault_name"] = answers["key_vault_name"]
    if answers.get("key_name"):
        fields["cmk_key_name"] = answers["key_name"]

    data_protection = pattern["design"].get("data_protection", {})
    if data_protection.get("blob_soft_delete") or data_protection.get("container_soft_delete"):
        fields["soft_delete"] = True
    if data_protection.get("versioning") == "enabled":
        fields["blob_versioning"] = True
    if "change_feed" in data_protection:
        fields["change_feed"] = data_protection["change_feed"] == "enabled"

    fields["application_name"] = answers.get("application_name") or ""
    fields["business_unit"] = answers.get("business_unit") or ""
    if answers.get("criticality"):
        fields["criticality"] = _title(answers["criticality"])
    if answers.get("data_classification"):
        fields["data_classification"] = _title(answers["data_classification"])
    fields["owner_email"] = answers.get("owner_email") or ""
    fields["sovereignty"] = "Standard"
    if answers.get("environment"):
        fields["env"] = _ENV_LABEL.get(answers["environment"], answers["environment"])

    fields["business_justification"] = _compose_business_justification(ns, pattern)

    tags = {
        "ApplicationName": fields.get("application_name", ""),
        "BusinessUnit": fields.get("business_unit", ""),
        "Criticality": fields.get("criticality", ""),
        "DataClassification": fields.get("data_classification", ""),
        "Owner": fields.get("owner_email", ""),
        "Environment": fields.get("env", ""),
        "Sovereignty": "Standard",
    }
    if answers.get("criticality"):
        tags["ServiceClass"] = _SERVICE_CLASS_BY_CRITICALITY.get(answers["criticality"])

    mapping = get_mapping(STORAGE_SERVICE)
    user_must_provide = []
    for item in mapping["user_must_provide"]:
        field = item["field"]
        skip = item.get("skip_if", "")
        if "captured during the conversation" in skip and fields.get(field):
            continue
        user_must_provide.append(item)

    # service_class isn't in mapping.yaml's user_must_provide list at all, but
    # this module deliberately never prefills it (see module docstring point
    # 4 — the KB's Bronze/Silver/Gold/Platinum scale doesn't match the form's
    # actual Standard/Business Critical/Mission Critical options), and it's a
    # required field on the form. Flag it explicitly rather than silently
    # leaving a required field blank with no explanation.
    user_must_provide.append({
        "field": "service_class", "blocking": True,
        "why": "The advisor can't reliably prefill this — its KB-suggested Bronze/Silver/"
               "Gold/Platinum scale doesn't match this form's Standard/Business Critical/"
               "Mission Critical options. Pick the one that fits.",
    })

    follow_on = []
    for req in mapping["follow_on_requests"]:
        if req.get("always_required"):
            include = True
        elif req.get("include_if"):
            if "escalation flag is set" in req["include_if"]:
                include = bool(rule_result.get("escalations"))
            else:
                include = evaluate_safe(req["include_if"], ns)
        else:
            include = True
        if not include:
            continue
        entry = dict(req)
        if req["request_type"] == "dns":
            entry["zone_name"] = derived.private_dns_zone
            entry["vnet_name"] = answers.get("vnet_name")
        follow_on.append(entry)

    return {
        "request_type": mapping.get("target_request_type", STORAGE_SERVICE),
        "fields": fields,
        "tags": tags,
        "user_must_provide": user_must_provide,
        "follow_on_requests": follow_on,
    }


def _first_int(text):
    if text is None:
        return None
    m = re.search(r"\d+", str(text))
    return m.group(0) if m else None


def _resolve_follow_ons(mapping: dict, answers: dict, rule_result: dict, derived) -> list:
    """Shared follow-on-request resolver for the four new builders below.
    Storage's own build_prefill() keeps its original inline loop untouched
    (no risk to its passing checks) — this is the same logic, generalized to
    tolerate the new mapping files' `always` vs `always_required` key drift
    and to fail closed (evaluate_safe) on unparseable include_if strings."""
    ns = {**answers, "derived": derived}
    follow_on = []
    for req in mapping.get("follow_on_requests", []):
        if req.get("always_required") or req.get("always"):
            include = True
        elif req.get("include_if"):
            if "escalation flag is set" in req["include_if"]:
                include = bool(rule_result.get("escalations"))
            else:
                include = evaluate_safe(req["include_if"], ns)
        else:
            include = True
        if include:
            follow_on.append(dict(req))
    return follow_on


def _common_tag_fields(answers: dict, fields: dict) -> dict:
    """ApplicationName/BusinessUnit/Criticality/Environment/Owner/Sovereignty
    tag mapping shared by AKS/VM (their forms use `project`, not
    `application_name` — see module docstring point 5)."""
    return {
        "ApplicationName": fields.get("project", ""),
        "BusinessUnit": answers.get("business_unit") or "",
        "Criticality": fields.get("criticality", ""),
        "Environment": fields.get("env", ""),
        "Owner": fields.get("owner_email", ""),
        "Sovereignty": "Standard",
    }


def build_prefill_aks(pattern: dict, answers: dict, rule_result: dict) -> dict:
    derived = AttrDict(rule_result.get("derived") or {})
    mapping = get_mapping(AKS_SERVICE)

    fields = {}
    fields["region"] = answers.get("region") or "uaenorth"
    if fields["region"] != "uaenorth" and answers.get("region_justification"):
        fields["region_justification"] = answers["region_justification"]

    fields["tier"] = pattern["design"].get("tier", "Standard")
    node_count = _first_int(answers.get("node_count"))
    if node_count:
        fields["node_count"] = node_count
    fields["cmk_encryption"] = True  # locked_fields: encryption CMK — real checkbox on this form

    if answers.get("application_name"):
        fields["project"] = answers["application_name"]
    if answers.get("environment"):
        fields["env"] = _ENV_LABEL.get(answers["environment"], answers["environment"])
    fields["owner_email"] = answers.get("owner_email") or ""
    if answers.get("criticality"):
        fields["criticality"] = _title(answers["criticality"])

    fields["business_justification"] = (
        f"{answers.get('workload_description') or 'Container workload'} for "
        f"{answers.get('application_name') or 'the application'}. Approximately "
        f"{answers.get('node_count') or 'an unspecified number of'} nodes. "
        f"Recommended by AlMadar AI Architecture Advisor "
        f"(pattern: {pattern['id']}, KB {pattern.get('kb_version', '2.0.0')})."
    ).strip()

    tags = _common_tag_fields(answers, fields)

    user_must_provide = [dict(i) for i in mapping.get("user_must_provide", [])]
    provided = {i["field"] for i in user_must_provide}
    # Two required form fields (app.py's TYPE_REQUIRED_DETAILS for
    # AKS_CLUSTER) the KB's own user_must_provide list omits entirely —
    # flagged explicitly rather than silently leaving a required field
    # unmentioned (same precedent as storage's service_class gap).
    if "node_pool_name" not in provided:
        user_must_provide.append({"field": "node_pool_name", "blocking": True,
                                   "why": "Required by the form; not covered by the advisor's KB mapping."})
    if "zpa_rnd_access" not in provided:
        user_must_provide.append({"field": "zpa_rnd_access", "blocking": True,
                                   "why": "Required by the form; not covered by the advisor's KB mapping."})
    # gpu_node_pool has no corresponding form field at all — surfaced as an
    # informational note, never written to a field that doesn't exist.
    if answers.get("gpu_required") or derived.get("pattern_hint") == "aks_gpu_nodepool":
        user_must_provide.append({
            "field": "gpu_node_pool", "blocking": False,
            "why": "This form has no GPU node-pool field yet — configure the GPU pool "
                   "manually after the cluster is created.",
        })

    return {
        "request_type": mapping.get("target_request_type", AKS_SERVICE),
        "fields": fields,
        "tags": tags,
        "user_must_provide": user_must_provide,
        "follow_on_requests": _resolve_follow_ons(mapping, answers, rule_result, derived),
    }


def build_prefill_vm(pattern: dict, answers: dict, rule_result: dict) -> dict:
    derived = AttrDict(rule_result.get("derived") or {})
    mapping = get_mapping(VM_SERVICE)

    fields = {}
    fields["region"] = answers.get("region") or "uaenorth"
    if fields["region"] != "uaenorth" and answers.get("region_justification"):
        fields["region_justification"] = answers["region_justification"]

    if answers.get("vm_count"):
        fields["vm_count"] = answers["vm_count"]

    fields["os_disk_type"] = pattern["design"].get("os_disk_type", "Premium_LRS")

    fields["auth_mode"] = _VM_AUTH_MODE_FORM_VALUE.get(derived.get("auth_mode"), "ssh_key")

    if answers.get("availability") is True:
        fields["zones"] = "1,2,3"

    if answers.get("application_name"):
        fields["project"] = answers["application_name"]
    if answers.get("environment"):
        fields["env"] = _ENV_LABEL.get(answers["environment"], answers["environment"])
    fields["owner_email"] = answers.get("owner_email") or ""
    if answers.get("criticality"):
        fields["criticality"] = _title(answers["criticality"])

    access = answers.get("access_need") or []
    access_labels = ", ".join(a.replace("_", " ") for a in access) or "the requesting workload"
    fields["business_justification"] = (
        f"{answers.get('vm_purpose') or 'Compute workload'} for "
        f"{answers.get('application_name') or 'the application'}. "
        f"{answers.get('vm_count') or 'An unspecified number of'} VM(s), accessed by {access_labels}. "
        f"Recommended by AlMadar AI Architecture Advisor "
        f"(pattern: {pattern['id']}, KB {pattern.get('kb_version', '2.0.0')})."
    ).strip()

    tags = _common_tag_fields(answers, fields)

    user_must_provide = [dict(i) for i in mapping.get("user_must_provide", [])]
    provided = {i["field"] for i in user_must_provide}
    if "os_image" not in provided:
        # map_to_curated_image has no backing data source anywhere in this
        # codebase (confirmed: config.py has no curated VM image list) —
        # never guessed, always left to the form's live Azure image picker.
        user_must_provide.append({
            "field": "os_image", "blocking": True,
            "why": "No curated image list exists yet — pick one from the form's live Azure image picker.",
        })

    return {
        "request_type": mapping.get("target_request_type", VM_SERVICE),
        "fields": fields,
        "tags": tags,
        "user_must_provide": user_must_provide,
        "follow_on_requests": _resolve_follow_ons(mapping, answers, rule_result, derived),
    }
