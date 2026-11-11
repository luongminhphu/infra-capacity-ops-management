"""Virtualisation-platform vocabulary for the sizing engine and reports.

The sizing math is platform-neutral (hosts, cores, threads, RAM, N+x, shared
storage). Only names, licensing units and a few HCI rules differ between
VMware vSphere, Nutanix AHV, Microsoft Hyper-V, Proxmox VE and similar
stacks. Every user-facing string that names a product reads from here, so a
report can be produced for any platform — or in neutral wording ("generic")
when the platform is not decided yet.
"""
from typing import Any, Dict, List

PLATFORMS: Dict[str, Dict[str, Any]] = {
    "generic": {
        "label": "Nền tảng ảo hoá (trung lập)",
        "short": "ảo hoá",
        "hypervisor": "Hypervisor",
        "manager": "Phần mềm quản trị tập trung",
        "manager_note": ["VM trong cụm", "hoặc hệ thống hiện hữu"],
        "ha": "HA (khởi động lại VM)",
        "balancer": "cân bằng tải tự động",
        "migration": "Di chuyển VM trực tuyến",
        "hci": "Lưu trữ HCI",
        "hci_short": "HCI",
        "hci_long": "lưu trữ HCI (gộp ổ đĩa của các host)",
        "hci_license": "Theo nền tảng được chọn",
        "anti_affinity": "quy tắc tách host (anti-affinity)",
        "cpu_compat": "chế độ tương thích CPU giữa các host",
        "hci_min_nodes": 3,
        "license_unit": "core",
        "license_min_per_socket": 16,
        "editions": ["Theo nền tảng được chọn"],
        "license_note": "Mô hình và điều kiện license áp dụng theo chính sách hiện hành của hãng tại thời điểm báo giá.",
        "reserve": {"cores": 2, "ram_gb": 16},
    },
    "vmware": {
        "label": "VMware vSphere",
        "short": "vSphere",
        "hypervisor": "ESXi",
        "manager": "vCenter Server",
        "manager_note": ["VM trong cụm", "hoặc vCenter hiện hữu"],
        "ha": "vSphere HA",
        "balancer": "DRS",
        "migration": "vMotion",
        "hci": "vSAN",
        "hci_short": "vSAN",
        "hci_long": "vSAN (gộp ổ đĩa của các host)",
        "hci_license": "Dung lượng vSAN kèm gói license; phần vượt mua thêm theo TiB",
        "anti_affinity": "quy tắc DRS tách host (VM-VM anti-affinity)",
        "cpu_compat": "EVC",
        "hci_min_nodes": 3,
        "license_unit": "core",
        "license_min_per_socket": 16,
        "editions": ["VMware vSphere Foundation (VVF)", "VMware Cloud Foundation (VCF)", "VMware vSphere Standard (VVS)",
                     "VMware vSphere Enterprise Plus"],
        "license_note": "Loại và điều kiện license VMware (VVF / VCF, dung lượng vSAN kèm theo) áp dụng theo chính sách của Broadcom hiện hành tại thời điểm báo giá.",
        "reserve": {"cores": 2, "ram_gb": 16},
    },
    "nutanix": {
        "label": "Nutanix AHV",
        "short": "Nutanix",
        "hypervisor": "AHV",
        "manager": "Prism Central",
        "manager_note": ["VM trong cụm", "(Prism Element tích hợp)"],
        "ha": "AHV HA",
        "balancer": "ADS (Acropolis Dynamic Scheduling)",
        "migration": "Live Migration",
        "hci": "Nutanix AOS (DSF)",
        "hci_short": "AOS",
        "hci_long": "Nutanix AOS — lưu trữ phân tán (DSF) trên ổ đĩa của các host",
        "hci_license": "Gồm trong license Nutanix Cloud Infrastructure (NCI) theo core; tính năng nâng cao theo gói",
        "anti_affinity": "quy tắc VM-VM anti-affinity",
        "cpu_compat": "chế độ tương thích CPU",
        "hci_min_nodes": 3,
        "license_unit": "core",
        "license_min_per_socket": 0,
        "editions": ["Nutanix Cloud Infrastructure (NCI) Pro", "Nutanix Cloud Infrastructure (NCI) Ultimate", "Nutanix Cloud Platform (NCP)"],
        "license_note": "Gói và điều kiện license Nutanix (NCI / NCP, số core tối thiểu) áp dụng theo chính sách hiện hành tại thời điểm báo giá.",
        # Controller VM (CVM) on every node: typically 8–12 vCPU and 32–48 GB RAM.
        "reserve": {"cores": 4, "ram_gb": 48},
        "overhead_note": "Mỗi host chạy một Controller VM (CVM) dùng khoảng 8–12 vCPU và 32–48 GB RAM.",
        "overhead_min_ram_gb": 32,
    },
    "hyperv": {
        "label": "Microsoft Hyper-V",
        "short": "Hyper-V",
        "hypervisor": "Hyper-V (Windows Server)",
        "manager": "SCVMM / Windows Admin Center",
        "manager_note": ["VM hoặc máy", "quản trị riêng"],
        "ha": "Failover Clustering",
        "balancer": "Dynamic Optimization (SCVMM)",
        "migration": "Live Migration",
        "hci": "Storage Spaces Direct (S2D)",
        "hci_short": "S2D",
        "hci_long": "Storage Spaces Direct — gộp ổ đĩa của các host",
        "hci_license": "Gồm trong Windows Server Datacenter",
        "anti_affinity": "thuộc tính AntiAffinityClassNames / quy tắc tách host",
        "cpu_compat": "chế độ tương thích bộ xử lý",
        "hci_min_nodes": 2,
        "license_unit": "core",
        "license_min_per_socket": 8,   # Windows Server: min 8 core / CPU and 16 core / server
        "license_min_per_host": 16,
        "editions": ["Windows Server Datacenter", "Windows Server Standard", "Azure Stack HCI / Azure Local"],
        "license_note": "Windows Server tính theo core (tối thiểu 8 core / CPU, 16 core / host); bản Datacenter cho phép chạy không giới hạn VM Windows Server. Điều kiện áp dụng theo chính sách Microsoft hiện hành tại thời điểm báo giá.",
        "reserve": {"cores": 2, "ram_gb": 16},
    },
    "proxmox": {
        "label": "Proxmox VE",
        "short": "Proxmox",
        "hypervisor": "Proxmox VE (KVM)",
        "manager": "Proxmox VE (quản trị tích hợp)",
        "manager_note": ["Tích hợp trên", "mọi host"],
        "ha": "Proxmox HA",
        "balancer": "CRS / quy tắc HA",
        "migration": "Live Migration",
        "hci": "Ceph",
        "hci_short": "Ceph",
        "hci_long": "Ceph — lưu trữ phân tán trên ổ đĩa của các host",
        "hci_license": "Mã nguồn mở, gồm trong subscription hỗ trợ",
        "anti_affinity": "quy tắc HA tách host",
        "cpu_compat": "kiểu CPU chung (CPU type) cho VM",
        "hci_min_nodes": 3,
        "license_unit": "socket",
        "license_min_per_socket": 0,
        "editions": ["Proxmox VE Subscription Basic", "Proxmox VE Subscription Standard", "Proxmox VE Subscription Premium",
                     "Proxmox VE Community"],
        "license_note": "Proxmox VE là mã nguồn mở; subscription hỗ trợ tính theo CPU socket. Điều kiện áp dụng theo chính sách hiện hành tại thời điểm báo giá.",
        # Ceph OSD daemons need RAM/CPU on every node (≈ 4–5 GB RAM and ~1 core per OSD).
        "reserve": {"cores": 4, "ram_gb": 32},
        "overhead_note": "Khi dùng Ceph, mỗi ổ đĩa (OSD) cần khoảng 4–5 GB RAM và 1 core trên host.",
        "overhead_min_ram_gb": 24,
    },
}

PLATFORM_KEYS = list(PLATFORMS)

# Neutral concept → per-platform name (glossary mapping table).
CONCEPTS: List[tuple] = [
    ("Hypervisor", "hypervisor"),
    ("Quản trị tập trung", "manager"),
    ("Tự khởi động lại VM khi host lỗi (HA)", "ha"),
    ("Cân bằng tải tự động", "balancer"),
    ("Di chuyển VM trực tuyến", "migration"),
    ("Lưu trữ HCI", "hci"),
]


def get(params_or_key: Any) -> Dict[str, Any]:
    key = params_or_key if isinstance(params_or_key, str) else (
        params_or_key.get("platform") if isinstance(params_or_key, dict) else getattr(params_or_key, "platform", "vmware"))
    return PLATFORMS.get(key or "vmware", PLATFORMS["vmware"])


def edition_of(params) -> str:
    """The licence edition to print: the chosen one if it belongs to the platform, else the platform default."""
    pf = get(params)
    ed = (getattr(params, "license_edition", None) if not isinstance(params, dict) else params.get("license_edition")) or ""
    return ed if ed in pf["editions"] else pf["editions"][0]


def meta() -> List[Dict[str, Any]]:
    """For the UI: key, label, editions, default licence minimum and reserves."""
    return [{"key": k, "label": v["label"], "editions": v["editions"], "licenseUnit": v["license_unit"],
             "licenseMinPerSocket": v["license_min_per_socket"], "reserve": v["reserve"], "hci": v["hci"]}
            for k, v in PLATFORMS.items()]
