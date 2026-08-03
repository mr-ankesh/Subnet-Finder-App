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
"""
from advisor.catalog_loader import get_mapping
from advisor.condition_eval import evaluate, AttrDict

_IDENTITY_TYPE_FORM_VALUE = {"UserAssigned": "user", "SystemAssigned": "system"}
_ENCRYPTION_TYPE_FORM_VALUE = {"CMK": "customer_managed", "MMK": "microsoft_managed"}

_SERVICE_CLASS_BY_CRITICALITY = {"low": "Bronze", "medium": "Silver", "high": "Gold", "critical": "Platinum"}
_ENV_LABEL = {"dev": "Development", "tst": "Test", "uat": "UAT", "prd": "Production", "snd": "Sandbox"}


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
    for q in get_questions()["questions"]:
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

    mapping = get_mapping()
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
                include = evaluate(req["include_if"], ns)
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
        "fields": fields,
        "tags": tags,
        "user_must_provide": user_must_provide,
        "follow_on_requests": follow_on,
    }
