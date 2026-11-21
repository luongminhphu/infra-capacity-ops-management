"""Tables of lifecycle & security (ICO v2 G9b)."""
from sqlalchemy import Boolean, CheckConstraint, Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint

from ..models import Base, utcnow
from ..shared.schema import in_check as _in

PRODUCTS = ("esxi", "vcenter")


class LifecycleEogs(Base):
    """End of General Support of one product version line ("8.0", "7.0"…), typed or imported by an admin."""
    __tablename__ = "lifecycle_eogs"
    __table_args__ = (UniqueConstraint("product", "version", name="uq_lifecycle_eogs"),
                      CheckConstraint(_in("product", PRODUCTS), name="ck_lifecycle_eogs_product"))

    id = Column(Integer, primary_key=True)
    product = Column(String(16), nullable=False)
    version = Column(String(16), nullable=False)               # version prefix: "8.0" matches 8.0, 8.0.2, 8.0.3…
    eogs = Column(Date, nullable=False)                         # End of General Support
    eotg = Column(Date)                                         # End of Technical Guidance
    note = Column(String(255))
    updated_by = Column(String(255))
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class VmsaAdvisory(Base):
    """One VMSA for one product version line: builds below fixed_build are exposed. Imported by an admin."""
    __tablename__ = "vmsa_advisory"
    __table_args__ = (UniqueConstraint("vmsa", "product", "version", name="uq_vmsa_advisory"),
                      CheckConstraint(_in("product", PRODUCTS), name="ck_vmsa_product"),
                      CheckConstraint("cvss >= 0 AND cvss <= 10", name="ck_vmsa_cvss"),
                      CheckConstraint("fixed_build > 0", name="ck_vmsa_build"))

    id = Column(Integer, primary_key=True)
    vmsa = Column(String(32), nullable=False)                   # VMSA-2025-0004
    title = Column(String(255))
    cvss = Column(Float, nullable=False)
    product = Column(String(16), nullable=False)
    version = Column(String(16), nullable=False)
    fixed_build = Column(Integer, nullable=False)
    published = Column(Date)
    updated_by = Column(String(255))
    updated_at = Column(DateTime, nullable=False, default=utcnow)


class VcProductState(Base):
    """Version / build of each vCenter (about.*), replaced at each sync."""
    __tablename__ = "vc_product_state"
    __table_args__ = (UniqueConstraint("vcenter_key", name="uq_vc_product_state"),)

    id = Column(Integer, primary_key=True)
    vcenter_key = Column(String(64), nullable=False)
    version = Column(String(32))
    build = Column(String(32))
    full_name = Column(String(255))
    captured_at = Column(DateTime, nullable=False, default=utcnow)


class TlsCertState(Base):
    """Server certificate of one endpoint read by a TLS handshake (target = "<kind>:<host>:<port>"), replaced at each scan."""
    __tablename__ = "tls_cert_state"
    __table_args__ = (UniqueConstraint("target", name="uq_tls_cert_target"),)

    id = Column(Integer, primary_key=True)
    target = Column(String(255), nullable=False)
    kind = Column(String(16), nullable=False)                   # vcenter / esxi / netbackup / ome / storage / ipam / itsm / app
    name = Column(String(255), nullable=False)                  # source name or host name shown to people
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    vcenter_key = Column(String(64))                            # ESXi hosts: their vCenter (CI key)
    not_after = Column(DateTime)
    subject = Column(String(500))
    issuer = Column(String(500))
    san = Column(Text)                                          # JSON list of DNS / IP names
    self_signed = Column(Boolean)
    error = Column(String(500))                                 # could not read (refused, timeout, not TLS)
    checked_at = Column(DateTime, nullable=False, default=utcnow)


from ..shared.stored_text import json_column as _json  # noqa: E402

_json(TlsCertState.san, list, "Tên trong chứng chỉ")
