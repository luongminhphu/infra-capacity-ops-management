"""DEMO ServiceDesk Plus (rule 2): 60 VM requests since 10/2025 whose body is an HTML table — standard, with other labels,
with a missing column, with merged cells — and 5 requests written as free text. Same JSON shape as the API v3 answers
(list + detail), so the collector and the parsers run exactly as against a real server."""
import datetime as dt
import random
from typing import Any, Dict, List, Optional, Tuple

from . import ItsmConfig

DEMO_TEMPLATES = ("Yêu cầu cấp máy ảo", "Yêu cầu tăng tài nguyên máy ảo")
START = dt.datetime(2025, 10, 1, 2, 0)
DEPARTMENTS = ("Khối Công nghệ", "Khối Ngân hàng số", "Khối Vận hành", "Phòng Dữ liệu")
PROJECTS = ("Core Banking", "Mobile App", "Data Lake", "CRM", "Thanh toán QR")
OSES = ("Ubuntu 22.04", "RHEL 9", "Windows Server 2022", "Oracle Linux 8")
LABELS = {
    "std": ("Tên VM", "Môi trường", "Hệ điều hành", "vCPU", "RAM (GB)", "Disk (GB)", "Số lượng"),
    "alt": ("Hostname", "Env", "OS", "CPU", "Bộ nhớ", "Ổ cứng", "SL"),
    "nodisk": ("Tên máy ảo", "Môi trường", "HĐH", "Số core", "RAM", "Số lượng"),
}


def _ms(t: dt.datetime) -> Dict[str, str]:
    v = int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    return {"value": str(v), "display_value": t.strftime("%d/%m/%Y %I:%M %p")}


def _table(rnd: random.Random, style: str, n: int, prefix: str) -> str:
    labels = LABELS["std" if style == "merged" else style]
    env = rnd.choice(("PROD", "UAT", "DEV"))
    rows = []
    for i in range(n):
        os_, vcpu, ram, disk = rnd.choice(OSES), rnd.choice((2, 4, 8, 16)), rnd.choice((4, 8, 16, 32, 64)), rnd.choice((80, 100, 200, 500))
        name = f"{prefix}-{env.lower()}-{i + 1:02d}"
        cells = {"std": [name, env, os_, vcpu, ram, disk, 1], "alt": [name, env, os_, f"{vcpu} vCPU", f"{ram} GB", f"{disk}GB", 1],
                 "nodisk": [name, env, os_, vcpu, f"{ram}GB", 1]}["std" if style == "merged" else style]
        if style == "merged":                      # environment cell merged down the rows (rowspan)
            cells = [name, (f'<td rowspan="{n}">{env}</td>' if i == 0 else None), os_, vcpu, ram, disk, 1]
            rows.append("<tr>" + "".join(c if isinstance(c, str) and c.startswith("<td") else f"<td>{c}</td>"
                                         for c in cells if c is not None) + "</tr>")
            continue
        rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in labels) + "</tr>"
    return f'<table border="1">{head}{"".join(rows)}</table>'


def requests() -> List[Dict[str, Any]]:
    """The 65 demo requests as SDP detail objects, oldest first."""
    out = []
    for i in range(65):
        rnd = random.Random(f"sdp-demo/{i}")
        created = START + dt.timedelta(days=i * 2.3, hours=rnd.randint(0, 8))
        updated = created + dt.timedelta(hours=rnd.randint(2, 96))
        project, dept = rnd.choice(PROJECTS), rnd.choice(DEPARTMENTS)
        resize = i % 7 == 3
        template = DEMO_TEMPLATES[1 if resize else 0]
        prefix = project.lower().replace(" ", "")[:8]
        if i >= 60:                                 # 5 requests written as free text (no table)
            body = (f"<p>Chào team hạ tầng,</p><p>Nhờ team tạo giúp {rnd.randint(1, 3)} VM {rnd.choice(OSES)} "
                    f"{rnd.choice((4, 8))} vCPU, {rnd.choice((8, 16))}GB RAM, ổ {rnd.choice((100, 200))}GB cho dự án {project} "
                    f"môi trường UAT.</p><p>Cảm ơn!</p>")
            subject = f"Nhờ tạo VM cho {project}"
        else:
            style = ("std", "alt", "nodisk", "merged")[i % 4]
            body = (f"<p>Dự án: <b>{project}</b> — {dept}</p>" + _table(rnd, style, rnd.randint(1, 4), prefix)
                    + '<p onclick="x()">Ghi chú: cần trước ngày go-live.</p><script>alert(1)</script>')
            subject = f"{'Tăng tài nguyên' if resize else 'Cấp máy ảo'} — {project}"
        status = "Đã đóng" if updated < dt.datetime(2026, 10, 1) else rnd.choice(("Đang xử lý", "Chờ duyệt", "Đã đóng"))
        req = {"id": str(110000 + i), "subject": subject, "template": {"name": template, "id": "3" if resize else "2"},
               "requester": {"name": f"Người yêu cầu {i % 12 + 1}", "email_id": f"user{i % 12 + 1}@corp.example",
                             "department": {"name": dept}},
               "status": {"name": status}, "approval_status": {"name": "Approved" if status == "Đã đóng" else "Pending Approval"},
               "created_time": _ms(created), "last_updated_time": _ms(updated), "description": body,
               "udf_fields": {"udf_sline_301": project, "udf_pick_302": rnd.choice(("PROD", "UAT"))}}
        if status == "Đã đóng":
            req["resolved_time"] = _ms(updated)
        out.append(req)
    return sorted(out, key=lambda r: int(r["last_updated_time"]["value"]))


class DemoSdp:
    """Same interface as sdp.SdpClient (list_page / get) over the demo requests."""

    def __init__(self, cfg: ItsmConfig):
        self.cfg = cfg
        self.all = requests()

    def list_page(self, after_ms: Optional[int], start_index: int) -> Tuple[List[Dict[str, Any]], bool]:
        rows = [r for r in self.all if (not self.cfg.templates or r["template"]["name"] in self.cfg.templates)
                and (after_ms is None or int(r["last_updated_time"]["value"]) > after_ms)]
        page = rows[start_index - 1:start_index - 1 + 100]
        return [{k: r[k] for k in ("id", "subject", "template", "last_updated_time")} for r in page], start_index - 1 + 100 < len(rows)

    def get(self, req_id: str) -> Dict[str, Any]:
        return next(r for r in self.all if r["id"] == str(req_id))
