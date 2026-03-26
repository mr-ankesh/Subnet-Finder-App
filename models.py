"""
SQLAlchemy models for Spoke Request workflow.
Uses a separate SQLite file (data/requests.db) so existing subnets.xlsx is untouched.
"""
import json
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ── Status constants ────────────────────────────────────────────────────────
class RequestStatus:
    PENDING           = "pending"            # Step 1 submitted, waiting for agent
    SUBNET_ALLOCATED  = "subnet_allocated"   # Agent found + allocated subnet
    DEPLOYING         = "deploying"          # Requester is deploying the spoke VNET
    COMPLETED         = "completed"          # Requester confirmed deployment done
    CANCELLED         = "cancelled"


STATUS_LABELS = {
    RequestStatus.PENDING:          ("Pending",           "warning"),
    RequestStatus.SUBNET_ALLOCATED: ("Subnet Allocated",  "info"),
    RequestStatus.DEPLOYING:        ("Deploying",         "primary"),
    RequestStatus.COMPLETED:        ("Completed",         "success"),
    RequestStatus.CANCELLED:        ("Cancelled",         "danger"),
}


class SpokeRequest(db.Model):
    __tablename__ = "spoke_requests"

    id               = db.Column(db.Integer, primary_key=True)
    cidr_needed      = db.Column(db.String(20),  nullable=False)   # e.g. "/24"
    purpose          = db.Column(db.String(500), nullable=False)
    requester_name   = db.Column(db.String(200), nullable=False)
    ip_range         = db.Column(db.String(20),  nullable=False)   # "10.110.0.0/16" or "10.119.0.0/16"
    hub_integration  = db.Column(db.Boolean,     nullable=False, default=False)
    status           = db.Column(db.String(30),  nullable=False, default=RequestStatus.PENDING)
    allocated_subnet = db.Column(db.String(50),  nullable=True)    # filled by agent
    teams_notified   = db.Column(db.Boolean,     default=False)
    notes            = db.Column(db.Text,        nullable=True)
    created_at       = db.Column(db.DateTime,    default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime,    default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationship to VNET info (one-to-one, filled at Step 3)
    vnet_info = db.relationship("VnetInfo", back_populates="request", uselist=False, cascade="all, delete-orphan")

    def status_label(self):
        label, _ = STATUS_LABELS.get(self.status, ("Unknown", "secondary"))
        return label

    def status_color(self):
        _, color = STATUS_LABELS.get(self.status, ("Unknown", "secondary"))
        return color

    def pool_key(self):
        """Return the pool key used by the existing subnet finder (e.g. '10.110')."""
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
            "created_at":       self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at":       self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


class VnetInfo(db.Model):
    __tablename__ = "vnet_info"

    id               = db.Column(db.Integer,  primary_key=True)
    request_id       = db.Column(db.Integer,  db.ForeignKey("spoke_requests.id"), nullable=False, unique=True)
    subscription_id  = db.Column(db.String(100), nullable=False)
    vnet_id          = db.Column(db.String(300), nullable=False)   # full ARM resource ID
    vnet_name        = db.Column(db.String(200), nullable=False)
    resource_group   = db.Column(db.String(200), nullable=False)
    region           = db.Column(db.String(100), nullable=False)
    address_space    = db.Column(db.String(100), nullable=False)   # CIDR of deployed spoke
    # Outbound rules stored as JSON: [{"destination": "...", "port": "...", "protocol": "..."}]
    outbound_rules   = db.Column(db.Text,  nullable=True)
    vpn_zpa_access   = db.Column(db.Boolean, default=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

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
