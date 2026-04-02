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

    # Ordered workflow steps (not including CANCELLED)
    ORDERED = [
        CIDR_REQUESTED,
        CIDR_ASSIGNED,
        VNET_CREATED,
        HUB_INTEGRATION_NEEDED,
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
    }

    _COLORS = {
        CIDR_REQUESTED:              "warning",
        CIDR_ASSIGNED:               "info",
        VNET_CREATED:                "primary",
        HUB_INTEGRATION_NEEDED:      "warning",
        HUB_INTEGRATION_IN_PROGRESS: "info",
        HUB_INTEGRATED:              "success",
        CANCELLED:                   "danger",
    }

    @classmethod
    def label(cls, status: str) -> str:
        return cls._LABELS.get(status, status)

    @classmethod
    def color(cls, status: str) -> str:
        return cls._COLORS.get(status, "secondary")


class SpokeRequest(db.Model):
    __tablename__ = "spoke_requests"

    id               = db.Column(db.Integer, primary_key=True)
    cidr_needed      = db.Column(db.String(20),  nullable=False)
    purpose          = db.Column(db.String(500), nullable=False)
    requester_name   = db.Column(db.String(200), nullable=False)
    ip_range         = db.Column(db.String(20),  nullable=False)
    hub_integration  = db.Column(db.Boolean,     nullable=False, default=False)
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

    def pool_key(self):
        return self.ip_range.rsplit(".", 1)[0].rsplit(".", 1)[0] if self.ip_range else ""

    def to_dict(self):
        return {
            "id":               self.id,
            "cidr_needed":      self.cidr_needed,
            "purpose":          self.purpose,
            "requester_name":   self.requester_name,
            "ip_range":         self.ip_range,
            "hub_integration":  self.hub_integration,
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
            "outbound_rules":  self.get_outbound_rules(),
            "vpn_zpa_access":  self.vpn_zpa_access,
        }


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
