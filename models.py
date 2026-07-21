"""
SQLAlchemy models for Spoke Request workflow and subnet inventory.
"""
import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class RequestStatus:
    CIDR_REQUESTED            = "CIDR_REQUESTED"
    CIDR_ASSIGNED             = "CIDR_ASSIGNED"
    VNET_CREATED              = "VNET_CREATED"
    HUB_INTEGRATION_NEEDED    = "HUB_INTEGRATION_NEEDED"
    HUB_INTEGRATION_IN_PROGRESS = "HUB_INTEGRATION_IN_PROGRESS"
    HUB_INTEGRATED            = "HUB_INTEGRATED"
    CANCELLED                 = "CANCELLED"

    # Generic / per-type workflow statuses (non-VNET request types)
    SUBMITTED         = "SUBMITTED"
    IN_REVIEW         = "IN_REVIEW"
    IN_PROGRESS       = "IN_PROGRESS"
    RULE_IMPLEMENTED  = "RULE_IMPLEMENTED"
    ZPA_ROUTE_ADDED   = "ZPA_ROUTE_ADDED"
    SPOKE_UDR_UPDATED = "SPOKE_UDR_UPDATED"
    NSG_UPDATED       = "NSG_UPDATED"
    FW_RULES_UPDATED  = "FW_RULES_UPDATED"
    SUBNET_ALLOCATED  = "SUBNET_ALLOCATED"
    RESOURCES_REMOVED = "RESOURCES_REMOVED"
    CIDR_RELEASED     = "CIDR_RELEASED"
    AKS_DEPLOYED      = "AKS_DEPLOYED"
    COMPLETED         = "COMPLETED"
    REJECTED          = "REJECTED"

    # Ordered workflow steps (not including CANCELLED)
    # Active workflow order. HUB_INTEGRATION_NEEDED is retired (kept as a constant
    # only for backward compatibility with any legacy rows) — VNET_CREATED is the
    # state where the admin runs hub integration.
    ORDERED = [
        CIDR_REQUESTED,
        CIDR_ASSIGNED,
        VNET_CREATED,
        HUB_INTEGRATION_IN_PROGRESS,
        HUB_INTEGRATED,
    ]

    _LABELS = {
        CIDR_REQUESTED:              "CIDR Requested",
        CIDR_ASSIGNED:               "CIDR Assigned",
        VNET_CREATED:                "VNET Created",
        HUB_INTEGRATION_NEEDED:      "Hub Integration Needed",
        HUB_INTEGRATION_IN_PROGRESS: "Hub Integration In Progress",
        HUB_INTEGRATED:              "Hub Integrated",
        CANCELLED:                   "Cancelled",
        SUBMITTED:                   "Submitted",
        IN_REVIEW:                   "In Review",
        IN_PROGRESS:                 "In Progress",
        RULE_IMPLEMENTED:            "Rule Implemented",
        ZPA_ROUTE_ADDED:             "ZPA Route Added",
        SPOKE_UDR_UPDATED:           "Spoke UDR Updated",
        NSG_UPDATED:                 "NSG Updated",
        FW_RULES_UPDATED:            "Firewall Rules Updated",
        SUBNET_ALLOCATED:            "Subnet Allocated",
        RESOURCES_REMOVED:           "Resources Removed",
        CIDR_RELEASED:               "CIDR Released",
        AKS_DEPLOYED:                "Cluster Deployed",
        COMPLETED:                   "Completed",
        REJECTED:                    "Rejected",
    }

    _COLORS = {
        CIDR_REQUESTED:              "warning",
        CIDR_ASSIGNED:               "info",
        VNET_CREATED:                "primary",
        HUB_INTEGRATION_NEEDED:      "warning",
        HUB_INTEGRATION_IN_PROGRESS: "info",
        HUB_INTEGRATED:              "success",
        CANCELLED:                   "danger",
        SUBMITTED:                   "warning",
        IN_REVIEW:                   "info",
        IN_PROGRESS:                 "info",
        RULE_IMPLEMENTED:            "primary",
        ZPA_ROUTE_ADDED:             "primary",
        SPOKE_UDR_UPDATED:           "primary",
        NSG_UPDATED:                 "primary",
        FW_RULES_UPDATED:            "primary",
        SUBNET_ALLOCATED:            "primary",
        RESOURCES_REMOVED:           "primary",
        CIDR_RELEASED:               "primary",
        AKS_DEPLOYED:                "primary",
        COMPLETED:                   "success",
        REJECTED:                    "danger",
    }

    @classmethod
    def label(cls, status: str) -> str:
        return cls._LABELS.get(status, status)

    @classmethod
    def color(cls, status: str) -> str:
        return cls._COLORS.get(status, "secondary")


class RequestType:
    """Request kinds available in the requester portal, each with its own workflow."""
    VNET_NEW          = "vnet_new"
    FIREWALL_POLICY   = "firewall_policy"
    HUB_INTEGRATION   = "hub_integration"
    ZPA_RND_ROUTING   = "zpa_rnd_routing"
    ZPA_OTHER_ROUTING = "zpa_other_routing"
    ZPA_NMO_ROUTING   = "zpa_nmo_routing"
    SUBNET_ADDITIONAL = "subnet_additional"
    VNET_DECOMMISSION = "vnet_decommission"
    DNS               = "dns"
    AKS_CLUSTER       = "aks_cluster"
    NETWORK_ISSUE     = "network_issue"
    OTHER             = "other"

    ALL = [VNET_NEW, FIREWALL_POLICY, HUB_INTEGRATION, ZPA_RND_ROUTING,
           ZPA_OTHER_ROUTING, ZPA_NMO_ROUTING, SUBNET_ADDITIONAL,
           VNET_DECOMMISSION, DNS, AKS_CLUSTER, NETWORK_ISSUE, OTHER]

    _LABELS = {
        VNET_NEW:          "New VNET",
        FIREWALL_POLICY:   "Firewall Policy",
        HUB_INTEGRATION:   "Hub Integration",
        ZPA_RND_ROUTING:   "Routing from ZPA R&D",
        ZPA_OTHER_ROUTING: "Routing from Other ZPA",
        ZPA_NMO_ROUTING:   "Routing from NMO ZPA",
        SUBNET_ADDITIONAL: "New Subnet in Existing VNET",
        VNET_DECOMMISSION: "VNET Decommission",
        DNS:               "DNS / Private DNS Link",
        AKS_CLUSTER:       "AKS Cluster",
        NETWORK_ISSUE:     "Report Network Issue",
        OTHER:             "Other Request",
    }

    _DESCRIPTIONS = {
        VNET_NEW:          "Request a CIDR and onboard a new spoke VNET.",
        FIREWALL_POLICY:   "Add, modify or delete a firewall policy rule.",
        HUB_INTEGRATION:   "VNET already exists — request hub peering/integration only.",
        ZPA_RND_ROUTING:   "Make your spoke routable via the ZPA R&D connector.",
        ZPA_OTHER_ROUTING: "Make your spoke routable via another ZPA connector.",
        ZPA_NMO_ROUTING:   "Make your spoke routable via the NMO ZPA connector (routes, NSG and firewall lists).",
        SUBNET_ADDITIONAL: "Carve an additional subnet inside an onboarded VNET.",
        VNET_DECOMMISSION: "Retire a spoke: remove peering/routes and release the CIDR.",
        DNS:               "DNS record or Private DNS zone link for your spoke.",
        AKS_CLUSTER:       "Deploy a managed Kubernetes (AKS) cluster into your spoke subnet.",
        NETWORK_ISSUE:     "Report a connectivity problem — the network team diagnoses the path (routing, DNS, firewall).",
        OTHER:             "Anything that doesn't fit the categories above.",
    }

    _ICONS = {   # bootstrap-icons names for the picker cards
        VNET_NEW:          "diagram-3",
        FIREWALL_POLICY:   "bricks",
        HUB_INTEGRATION:   "sign-turn-right",
        ZPA_RND_ROUTING:   "shield-lock",
        ZPA_OTHER_ROUTING: "shield-plus",
        ZPA_NMO_ROUTING:   "shield-shaded",
        SUBNET_ADDITIONAL: "grid-1x2",
        VNET_DECOMMISSION: "trash3",
        DNS:               "globe2",
        AKS_CLUSTER:       "boxes",
        NETWORK_ISSUE:     "wifi-off",
        OTHER:             "chat-square-text",
    }

    # Per-type ordered workflow steps. CANCELLED / REJECTED are terminals for all.
    WORKFLOWS = {
        VNET_NEW:          RequestStatus.ORDERED,
        FIREWALL_POLICY:   [RequestStatus.SUBMITTED, RequestStatus.IN_REVIEW,
                            RequestStatus.RULE_IMPLEMENTED, RequestStatus.COMPLETED],
        HUB_INTEGRATION:   [RequestStatus.SUBMITTED, RequestStatus.HUB_INTEGRATION_IN_PROGRESS,
                            RequestStatus.HUB_INTEGRATED],
        ZPA_RND_ROUTING:   [RequestStatus.SUBMITTED, RequestStatus.ZPA_ROUTE_ADDED,
                            RequestStatus.SPOKE_UDR_UPDATED, RequestStatus.COMPLETED],
        ZPA_OTHER_ROUTING: [RequestStatus.SUBMITTED, RequestStatus.ZPA_ROUTE_ADDED,
                            RequestStatus.SPOKE_UDR_UPDATED, RequestStatus.COMPLETED],
        ZPA_NMO_ROUTING:   [RequestStatus.SUBMITTED, RequestStatus.ZPA_ROUTE_ADDED,
                            RequestStatus.SPOKE_UDR_UPDATED, RequestStatus.NSG_UPDATED,
                            RequestStatus.FW_RULES_UPDATED, RequestStatus.COMPLETED],
        SUBNET_ADDITIONAL: [RequestStatus.SUBMITTED, RequestStatus.SUBNET_ALLOCATED,
                            RequestStatus.COMPLETED],
        VNET_DECOMMISSION: [RequestStatus.SUBMITTED, RequestStatus.RESOURCES_REMOVED,
                            RequestStatus.CIDR_RELEASED, RequestStatus.COMPLETED],
        DNS:               [RequestStatus.SUBMITTED, RequestStatus.IN_PROGRESS,
                            RequestStatus.COMPLETED],
        AKS_CLUSTER:       [RequestStatus.SUBMITTED, RequestStatus.AKS_DEPLOYED,
                            RequestStatus.COMPLETED],
        NETWORK_ISSUE:     [RequestStatus.SUBMITTED, RequestStatus.IN_PROGRESS,
                            RequestStatus.COMPLETED],
        OTHER:             [RequestStatus.SUBMITTED, RequestStatus.IN_PROGRESS,
                            RequestStatus.COMPLETED],
    }

    TERMINALS = [RequestStatus.CANCELLED, RequestStatus.REJECTED]

    @classmethod
    def label(cls, t: str) -> str:
        return cls._LABELS.get(t, t or "New VNET")

    @classmethod
    def description(cls, t: str) -> str:
        return cls._DESCRIPTIONS.get(t, "")

    @classmethod
    def icon(cls, t: str) -> str:
        return cls._ICONS.get(t, "card-list")

    @classmethod
    def workflow(cls, t: str) -> list:
        return cls.WORKFLOWS.get(t, RequestStatus.ORDERED)

    @classmethod
    def initial_status(cls, t: str) -> str:
        return cls.workflow(t)[0]


class SpokeRequest(db.Model):
    __tablename__ = "spoke_requests"

    id               = db.Column(db.Integer, primary_key=True)
    # Request kind (RequestType.*). Legacy rows default to vnet_new.
    request_type     = db.Column(db.String(30),  nullable=False, default=RequestType.VNET_NEW)
    # Type-specific fields as a JSON dict (non-VNET types)
    details          = db.Column(db.Text,        nullable=True)
    cidr_needed      = db.Column(db.String(20),  nullable=False)
    purpose          = db.Column(db.String(500), nullable=False)
    requester_name   = db.Column(db.String(200), nullable=False)
    requester_email  = db.Column(db.String(200), nullable=True)
    ip_range         = db.Column(db.String(20),  nullable=False)
    hub_integration  = db.Column(db.Boolean,     nullable=False, default=False)
    # 'self' = requester deploys the VNET; 'admin' = admin deploys it for them
    deployment_mode  = db.Column(db.String(10),  nullable=False, default="self")
    status           = db.Column(db.String(40),  nullable=False, default=RequestStatus.CIDR_REQUESTED)
    allocated_subnet = db.Column(db.String(50),  nullable=True)
    notes            = db.Column(db.Text,        nullable=True)
    created_at       = db.Column(db.DateTime,    default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    vnet_info = db.relationship("VnetInfo", back_populates="request", uselist=False, cascade="all, delete-orphan")

    def status_label(self):
        return RequestStatus.label(self.status)

    def status_color(self):
        return RequestStatus.color(self.status)

    def type_label(self):
        return RequestType.label(self.request_type)

    def type_icon(self):
        return RequestType.icon(self.request_type)

    def workflow(self):
        return RequestType.workflow(self.request_type)

    def get_details(self) -> dict:
        if not self.details:
            return {}
        try:
            return json.loads(self.details)
        except Exception:
            return {}

    def set_details(self, d: dict):
        self.details = json.dumps(d or {})

    def pool_key(self):
        return self.ip_range.rsplit(".", 1)[0].rsplit(".", 1)[0] if self.ip_range else ""

    def to_dict(self):
        return {
            "id":               self.id,
            "request_type":     self.request_type or RequestType.VNET_NEW,
            "type_label":       self.type_label(),
            "details":          self.get_details(),
            "cidr_needed":      self.cidr_needed,
            "purpose":          self.purpose,
            "requester_name":   self.requester_name,
            "requester_email":  self.requester_email,
            "ip_range":         self.ip_range,
            "hub_integration":  self.hub_integration,
            "deployment_mode":  self.deployment_mode,
            "status":           self.status,
            "status_label":     self.status_label(),
            "allocated_subnet": self.allocated_subnet,
            "notes":            self.notes,
            "created_at":       self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at":       self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


class VnetInfo(db.Model):
    __tablename__ = "vnet_info"

    id               = db.Column(db.Integer,     primary_key=True)
    request_id       = db.Column(db.Integer,     db.ForeignKey("spoke_requests.id"), nullable=False, unique=True)
    subscription_id  = db.Column(db.String(100), nullable=True)
    vnet_id          = db.Column(db.String(300), nullable=True)
    vnet_name        = db.Column(db.String(200), nullable=True)
    resource_group   = db.Column(db.String(200), nullable=True)
    region           = db.Column(db.String(100), nullable=True)
    address_space    = db.Column(db.String(100), nullable=True)
    subnet_name      = db.Column(db.String(120), nullable=True)
    subnet_size      = db.Column(db.String(10),  nullable=True)   # prefix, e.g. "26"
    subnet_purpose   = db.Column(db.String(200), nullable=True)
    outbound_rules   = db.Column(db.Text,        nullable=True)   # JSON list
    vpn_zpa_access   = db.Column(db.Boolean,     default=False)
    created_at       = db.Column(db.DateTime,    default=datetime.utcnow)

    request = db.relationship("SpokeRequest", back_populates="vnet_info")

    def get_outbound_rules(self):

        if not self.outbound_rules:
            return []
        try:
            return json.loads(self.outbound_rules)
        except Exception:
            return []

    def set_outbound_rules(self, rules: list):
        self.outbound_rules = json.dumps(rules)

    def to_dict(self):
        return {
            "id":              self.id,
            "request_id":      self.request_id,
            "subscription_id": self.subscription_id,
            "vnet_id":         self.vnet_id,
            "vnet_name":       self.vnet_name,
            "resource_group":  self.resource_group,
            "region":          self.region,
            "address_space":   self.address_space,
            "subnet_name":     self.subnet_name,
            "subnet_size":     self.subnet_size,
            "subnet_purpose":  self.subnet_purpose,
            "outbound_rules":  self.get_outbound_rules(),
            "vpn_zpa_access":  self.vpn_zpa_access,
        }


class AppSetting(db.Model):
    """
    Admin-editable config override (see settings_store.py, which reads/writes
    this table via raw sqlite3 so config resolution works without app context).
    Secret values are Fernet-encrypted at rest.
    """
    __tablename__ = "app_settings"

    key        = db.Column(db.Text,    primary_key=True)
    value      = db.Column(db.Text,    nullable=True)
    is_secret  = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.Text,    nullable=True)


class AuditLog(db.Model):
    """
    Immutable operation trail — who did what, when, on which request.
    Written via audit.py (raw sqlite3) so agents and routes can log without
    Flask-SQLAlchemy session scoping; this model mirrors the table for ORM reads.
    """
    __tablename__ = "audit_log"

    id         = db.Column(db.Integer, primary_key=True)
    ts         = db.Column(db.Text,    nullable=False)
    actor      = db.Column(db.Text,    nullable=False)
    actor_role = db.Column(db.Text,    nullable=False, default="system")
    action     = db.Column(db.Text,    nullable=False)
    request_id = db.Column(db.Integer, nullable=True, index=True)
    summary    = db.Column(db.Text,    nullable=True)
    data       = db.Column(db.Text,    nullable=True)


class FwCollection(db.Model):
    """
    Admin-defined firewall rule collection group / rule collection pairs
    (one-time setup with descriptions). Azure itself has no description field
    for these, so the app keeps them here; the request-processing UI merges
    this list with what actually exists in the policy.
    """
    __tablename__ = "fw_collections"
    __table_args__ = (db.UniqueConstraint("rcg", "collection", name="uq_fw_rcg_collection"),)

    id          = db.Column(db.Integer,     primary_key=True)
    rcg         = db.Column(db.String(120), nullable=False)   # rule collection group name
    collection  = db.Column(db.String(120), nullable=False)   # rule collection name
    priority    = db.Column(db.Integer,     nullable=False, default=200)
    action      = db.Column(db.String(10),  nullable=False, default="Allow")  # Allow | Deny
    description = db.Column(db.String(300), nullable=True)
    created_at  = db.Column(db.DateTime,    default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "rcg": self.rcg, "collection": self.collection,
                "priority": self.priority, "action": self.action,
                "description": self.description or ""}


class SubnetRecord(db.Model):
    """
    Persistent record of every allocated (used) or reserved subnet.
    Free space is computed dynamically — only allocated entries are stored.
    """
    __tablename__ = "subnet_records"

    id           = db.Column(db.Integer,     primary_key=True)
    subnet       = db.Column(db.String(50),  nullable=False, unique=True, index=True)
    pool         = db.Column(db.String(20),  nullable=False, index=True)   # e.g. "10.110"
    status       = db.Column(db.String(20),  nullable=False, default="used")  # used | reserved
    purpose      = db.Column(db.String(500), nullable=True)
    requested_by = db.Column(db.String(200), nullable=True)
    allocated_by = db.Column(db.String(200), nullable=True)
    allocated_at = db.Column(db.DateTime,    nullable=True)
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id":           self.id,
            "subnet":       self.subnet,
            "pool":         self.pool,
            "status":       self.status,
            "purpose":      self.purpose      or "",
            "requested_by": self.requested_by or "",
            "allocated_by": self.allocated_by or "",
            "allocated_at": self.allocated_at.strftime("%Y-%m-%d %H:%M:%S") if self.allocated_at else "",
            "Subnet":       self.subnet,
            "Purpose":      self.purpose      or "",
            "RequestedBy":  self.requested_by or "",
            "AllocatedBy":  self.allocated_by or "",
            "AllocationTime": self.allocated_at.strftime("%Y-%m-%d %H:%M:%S") if self.allocated_at else "",
        }
