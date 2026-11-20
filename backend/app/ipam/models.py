"""Latest IPAM state per source (GestióIP), replaced at each sync like the storage / hardware state tables.
The correlation layer (docs/design/ico-correlation.md) compares these declared IPs with the IPs the sources report."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from ..models import Base, utcnow


class IpamSourceState(Base):
    __tablename__ = "ipam_source_state"
    __table_args__ = (UniqueConstraint("source_key", name="uq_ipam_source_state"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False, default="gestioip")
    name = Column(String(255), nullable=False)
    networks = Column(Integer)
    hosts = Column(Integer)
    captured_at = Column(DateTime)
    last_ok_at = Column(DateTime)
    last_error = Column(Text)
    last_error_at = Column(DateTime)


class IpamNetwork(Base):
    __tablename__ = "ipam_network"
    __table_args__ = (UniqueConstraint("source_key", "cidr", name="uq_ipam_network"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    network_id = Column(String(32))
    cidr = Column(String(64), nullable=False)
    descr = Column(String(255))
    site = Column(String(128))
    category = Column(String(128))
    comment = Column(String(500))
    vlan = Column(String(64))
    rootnet = Column(Boolean, nullable=False, default=False)
    custom = Column(Text, nullable=False, default="{}")       # JSON custom columns
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class IpamHost(Base):
    """One IP entry of the IPAM (unique per source and IP, as in GestióIP)."""
    __tablename__ = "ipam_host"
    __table_args__ = (
        UniqueConstraint("source_key", "ip", name="uq_ipam_host"),
        CheckConstraint("ip_version IN ('v4', 'v6')", name="ck_ipam_host_version"),
        Index("ix_ipam_host_ip", "ip"),
    )

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    host_id = Column(String(32))
    ip = Column(String(64), nullable=False)                    # norm_ip
    ip_version = Column(String(2), nullable=False, default="v4")
    network = Column(String(64))                               # most specific IPAM network (CIDR)
    hostname = Column(String(255))
    descr = Column(String(255))
    site = Column(String(128))
    category = Column(String(128))
    comment = Column(String(500))
    alive = Column(String(32))                                 # up | down | never checked …
    last_response = Column(String(64))
    serial = Column(String(128), index=True)                   # norm_serial of the configured custom column
    mac = Column(String(17))                                   # norm_mac of the configured custom column
    custom = Column(Text, nullable=False, default="{}")
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class IpamVlan(Base):
    __tablename__ = "ipam_vlan"
    __table_args__ = (Index("ix_ipam_vlan_source", "source_key", "number"),)

    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False)
    number = Column(Integer, nullable=False)
    name = Column(String(128))
    descr = Column(String(255))
    captured_at = Column(DateTime, nullable=False, default=utcnow)


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(IpamNetwork.custom, dict, "Cột tuỳ chỉnh của network IPAM", nullable=False)
_json(IpamHost.custom, dict, "Cột tuỳ chỉnh của IP trong IPAM", nullable=False)
