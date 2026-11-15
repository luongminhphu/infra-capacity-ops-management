"""Chuẩn hoá BOM: layouts (Dell Solutions Configurator, Cisco CCW, generic / HPE), classification,
per-unit summaries, prices, Excel output with formulas, API rules. Synthetic files only."""
import io

import pytest
from openpyxl import Workbook, load_workbook

from app import bom, excel_bom

from test_spend_opex import _login, admin, app_, editor  # noqa: F401  (fixtures)


def _xlsx(rows, title="BOM"):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def dell_configurator():
    h = ["", "Group Name", "Group ID", "Product Name", "Product Qty", "Unit Price (List)", "Extended Price (List)", "Module Name",
         "Option ID", "Option Name", "Option List Price", "SKUs", "Qty", "Parent Order Code"]
    o = lambda module, name, price, sku, qty: ["", "", "", "", "", "", "", module, "X", name, price, sku, qty, ""]  # noqa: E731
    return _xlsx([["Solution Name: DEMO_DC"], ["Solution List Price: $34,700.00"], h,
                  ["", "Group 1", "1", "PowerEdge R770 - [ASPER770] ", 2, 17000, 34000],
                  o("Base", "PowerEdge R770 Server", 5000, "210-AAAA", 1),
                  o("Processor", "Intel Xeon 6 Performance 6515P 2.3G, 16C/32T", 2000, "338-A", 1),
                  o("Additional Processor", "Intel Xeon 6 Performance 6515P 2.3G, 16C/32T", 2000, "338-A", 1),
                  o("Memory Capacity", "32GB RDIMM, 6400MT/s, Dual Rank", 2400, "370-B", 8),
                  o("Hard Drives", "3.84TB SSD SATA Read Intensive 6Gbps", 2000, "400-C", 4),
                  o("Boot Optimized Storage Cards", "BOSS-N1 controller card with 2 M.2 960GB (RAID 1)", 600, "403-D", 1),
                  o("OCP 3.0 Network Adapters", "Broadcom 57414 Dual Port 25GbE SFP28 Adapter, OCP 3.0 NIC", 400, "540-E", 2),
                  o("Fibre Channel Adapters", "QLogic 2772 Dual Port 32Gb Fibre Channel HBA", 600, "403-F", 1),
                  o("Optics & Cables for Network Cards", "SFP28 SR Optic, 25GbE", 100, "407-G", 4),
                  o("Power Supply", "Dual, Fully Redundant(1+1), Hot-Plug Power Supply, 1100W", 300, "450-H", 1),
                  o("Operating System", "No Operating System", 0, "611-I", 1),
                  o("Extended Service", "ProSupport and Next Business Day Onsite Service, 60 Month(s)", 1600, "865-J", 1),
                  ["", "Group 1", "1", "Dell Automation Platform Virtual - [DAP] ", 1, 700, 700],
                  o("Dell Automation Platform Outcome Base", "Dell Automation Platform Virtual, NativeEdge Base", 0, "210-K", 1),
                  o("Orchestrator Licenses", "On-Prem Orchestrator License (valid for 1 Instance) - 1 Year", 700, "143-L", 1)])


def cisco_ccw():
    h = ["Line Number", "Part Number", "Description", "Service Duration (Months)", "Estimated Lead Time (Days)", "Unit List Price",
         "Qty", "Unit Net Price", "Discount(%)", "Extended Net Price"]
    return _xlsx([["Estimate Name: DEMO-CAMPUS", "", "", "", "", "Price List: Global Price List - US (USD)"], [], h,
                  ["1.0", "C9300-48UXM-A", "Catalyst 9300 48-port mGig UPOE, Network Advantage", "---", 14, 10000, 2, 6000, 40, 12000],
                  ["1.1", "C9300-NM-8X", "Catalyst 9300 8 x 10GE Network Module", "---", 14, 1500, 2, 900, 40, 1800],
                  ["1.2", "SFP-10G-SR", "10GBASE-SR SFP Module", "---", 14, 500, 8, 300, 40, 2400],
                  ["1.3", "C9300-DNA-A-3Y", "C9300 DNA Advantage, 48-Port, 3 Year Term License", 36, 0, 3000, 2, 1800, 40, 3600],
                  ["1.4", "CON-SNT-C930048U", "SNTC-8X5XNBD Catalyst 9300 48-port", 36, 0, 700, 2, 600, 14, 1200],
                  ["2.0", "DS-C9148T-24PETK9", "MDS 9148T 32G FC switch, w/ 24 active ports", "---", 21, 20000, 2, 12000, 40, 24000],
                  ["2.1", "DS-SFP-FC32G-SW", "32 Gbps Fibre Channel SW SFP+, LC", "---", 21, 400, 48, 240, 40, 11520]])


def generic_hpe():
    return _xlsx([["Product #", "Product Description", "Qty", "Unit Price", "Ext. Price"],
                  ["Server", None, None, None, None],
                  ["P52534-B21", "HPE ProLiant DL380 Gen11 8SFF NC CTO Server", 4, 3000, 12000],
                  ["P49610-B21", "Intel Xeon-Gold 6430 2.1GHz 32-core 270W Processor", 8, 2500, 20000],
                  ["P49610-B21 0D1", "Factory Integrated", 8, 0, 0],
                  ["P43328-B21", "HPE 64GB (1x64GB) Dual Rank x4 DDR5-4800 Registered Smart Memory Kit", 32, 600, 19200],
                  ["P40504-B21", "HPE 1.92TB SAS 12G Mixed Use SFF BC Multi Vendor SSD", 16, 900, 14400],
                  ["P26262-B21", "Broadcom BCM57414 Ethernet 10/25Gb 2-port SFP28 OCP3 Adapter", 4, 400, 1600],
                  ["H8QP7E", "HPE 5Y Tech Care Essential DL380 Gen11 HW SVC", 4, 1000, 4000]])


def test_dell_configurator_layout_prices_and_summary():
    d = bom.parse(dell_configurator())
    assert d["layout"] == "dell_configurator" and d["currency"] == "USD" and d["projectName"] == "DEMO_DC"
    srv, sw = d["configs"]
    assert (srv["title"], srv["category"], srv["qty"], srv["priceBasis"]) == ("PowerEdge R770", "server", 2, "per_unit")
    assert srv["total"] == 34000 and sw["category"] == "software" and sw["total"] == 700                 # 17.000 × 2
    assert d["warnings"] == []                                                                        # options add up to the unit price
    s = dict(srv["summary"])
    assert s["CPU"].startswith("2 × Intel Xeon 6")                                                    # Processor + Additional merged
    assert s["RAM"].startswith("256 GB (8 × 32GB")
    assert s["Ổ đĩa"] == "4 × 3.84TB SSD SATA; 2 × 960GB M.2 (BOSS)"
    assert s["Cổng mạng"] == "4 × 25GbE (2 card 2 cổng)" and s["Cổng HBA (FC)"] == "2 × 32Gb FC (1 card 2 cổng)"
    assert s["Module quang"].startswith("4 × SFP28") and "60 Month" in s["Bảo hành / hỗ trợ"]
    assert s["Giá 1 thiết bị"] == "17.000" and s["Thành tiền (2 thiết bị)"] == "34.000" and s["Giá niêm yết 1 thiết bị (file gốc)"] == "17.000"
    assert [i["empty"] for i in srv["items"] if i["desc"] == "No Operating System"] == [True]


def test_cisco_ccw_layout():
    d = bom.parse(cisco_ccw())
    assert d["layout"] == "cisco_ccw" and d["currency"] == "USD"
    sw, san = d["configs"]
    assert sw["category"] == "network" and sw["qty"] == 2 and sw["total"] == 21000                     # net prices
    assert san["category"] == "san_switch" and san["total"] == 35520
    s = dict(sw["summary"])
    assert "48" in s["Cổng"] and "8 × 10GE" in s["Cổng"] and s["License"] == "DNA Advantage"
    assert "SNTC" in s["Bảo hành / hỗ trợ"] and "(36 tháng)" in s["Bảo hành / hỗ trợ"]
    assert dict(san["summary"])["Tốc độ"] == "32Gb FC"


def test_generic_hpe_total_quantities():
    d = bom.parse(generic_hpe())
    assert d["layout"] == "hpe"
    (srv,) = d["configs"]
    assert srv["category"] == "server" and srv["qty"] == 4 and srv["qtyMode"] == "total" and srv["total"] == 71200
    s = dict(srv["summary"])
    assert s["CPU"].startswith("2 × Intel Xeon-Gold 6430") and s["RAM"].startswith("512 GB (8 × HPE 64GB")      # 32 / 4 per server
    assert s["Ổ đĩa"].startswith("4 × 1.92TB SSD SAS") and s["Giá 1 thiết bị"] == "17.800"
    assert "Tech Care" in s["Bảo hành / hỗ trợ"]
    assert [i["empty"] for i in srv["items"] if i["desc"] == "Factory Integrated"] == [True]


def test_classification_and_bad_files():
    assert bom.classify("FortiGate 600F", []) == "firewall" and bom.classify("BIG-IP i5800", []) == "load_balancer"
    assert bom.classify("Brocade G620 SAN switch", []) == "san_switch" and bom.classify("PowerStore 1200T", []) == "storage"
    with pytest.raises(ValueError):
        bom.parse(b"PK\x03\x04garbage")
    with pytest.raises(ValueError):
        bom.parse(_xlsx([["a", "b"], [1, 2]]))


def test_excel_output_formulas_and_text_safety():
    d = bom.parse(dell_configurator())
    d["configs"][0]["title"] = "=HYPERLINK(\"http://x\")"
    x = excel_bom.build_bom_xlsx({"project": {"name": "Demo", "currency": "USD", "vatPct": 10, "hideEmpty": True}, "configs": d["configs"]})
    wb = load_workbook(io.BytesIO(x))
    assert wb.sheetnames == ["BOM", "Tổng hợp", "Dữ liệu gốc"]
    ws = wb["BOM"]
    cells = [c for row in ws.iter_rows() for c in row if c.value is not None]
    texts = [str(c.value) for c in cells]
    assert any(t.startswith("I. MÁY CHỦ") for t in texts) and any(t.startswith("II. PHẦN MỀM") for t in texts)
    assert not any("No Operating System" == t for t in texts)                                          # hidden empty option
    title = next(c for c in cells if "HYPERLINK" in str(c.value))
    assert title.data_type == "s" and not str(title.value).startswith("=")                              # vendor text never a formula
    formulas = [t for t in texts if t.startswith("=")]
    assert any("*2" in f for f in formulas)                                                            # 1 unit × 2 units
    assert any(f.startswith("=ROUND(") and "*10/100" in f for f in formulas)                           # VAT 10 %
    assert "17.000 USD" in texts                                                                       # price row in the summary box
    qty_cells = [ws.cell(row=c.row, column=5) for c in ws["E"] if isinstance(c.value, (int, float))]
    assert qty_cells and all(c.number_format == "#,##0" for c in qty_cells if float(c.value).is_integer())  # "2", not "2."


def test_quantity_format_keeps_fraction_only_when_needed():
    assert excel_bom._qty_fmt(2) == "#,##0" and excel_bom._qty_fmt(2.0) == "#,##0"
    assert excel_bom._qty_fmt(1.5) == "#,##0.##" and excel_bom._qty_fmt(None) == "#,##0.##"


def test_api(app_, admin, editor):
    r = editor.post("/api/bom/parse", files={"file": ("bom.xlsx", dell_configurator())})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["layoutLabel"] == "Dell Solutions Configurator"
    assert editor.post("/api/bom/parse", files={"file": ("a.pdf", b"%PDF-1.7 x")}).status_code == 415
    cfg = d["configs"][0]
    cfg["category"] = "storage"
    s = editor.post("/api/bom/summary", json=cfg).json()["summary"]
    assert any(k == "Dung lượng thô" for k, _ in s)
    x = editor.post("/api/bom/export", json={"project": {"name": "Dự án A", "currency": "USD"}, "configs": d["configs"]})
    assert x.status_code == 200 and x.content[:2] == b"PK" and "BOM_du_an_a_" in x.headers["content-disposition"]
    assert editor.post("/api/bom/export", json={"configs": []}).status_code == 400
    from fastapi.testclient import TestClient
    assert TestClient(app_).post("/api/bom/export", json={"configs": []}).status_code == 401


def cisco_flat_estimate():
    h = ["Part Number", "Smart Account Mandatory", "Description", "Service Duration (Months)", "Estimated Lead Time (Days)",
         "Unit List Price", "Pricing Term", "Qty", "Unit Net Price", "Disc(%)", "Extended Net Price"]
    return _xlsx([["Price Estimate"], [], ["", "", "", "", "", "", "", "", "All prices are shown in USD"], h,
                  ["C9200L-24T-4X-E", "-", "Catalyst 9200L 24-port data, 4 x 10G, Network Essentials", "---", 30, 3000, "", 4, 3000, 0, 12000],
                  ["CON-SNT-C92L24XE", "-", "SNTC-8X5XNBD Catalyst 9200L 24-port", 12, "N/A", 300, "", 4, 300, 0, 1200],
                  ["C9200L-DNA-E-24-3Y", "-", "C9200L Cisco DNA Essentials, 24-port, 3 Year Term license", 36, "N/A", 500, "", 4, 500, 0, 2000],
                  ["C9200L-STACK-KIT", "-", "Cisco Catalyst 9200L Stack Module", "---", 14, 1000, "", 4, 1000, 0, 4000],
                  ["SFP-10G-SR=", "-", "10GBASE-SR SFP Module", "---", 14, 900, "", 8, 900, 0, 7200],
                  ["C9300-48P-A", "-", "Catalyst 9300 48-port PoE+, Network Advantage", "---", 30, 8000, "", 2, 8000, 0, 16000],
                  ["CON-SNT-C930048P", "-", "SNTC-8X5XNBD Catalyst 9300 48-port PoE+", 12, "N/A", 800, "", 2, 800, 0, 1600],
                  [], ["", "", "", "", "", "", "", "", "Total Price:", "", 44000]])


def hpe_iris():
    h = ["Line#", "Part Number", "Description", "Manufacturer", "Unit Price", "Quantity", "Sub Total", "Discount", "Total", "Price List"]
    hp = "Hewlett Packard Enterprise"
    return _xlsx([h,
                  ["1.0", "SW-A", "HPE Aruba Networking CX 6300M 24p Smart Rate 1/2.5/5/10G Class6 PoE 4SFP56 Switch", hp, 20000, 1, 20000, 0, 20000, "PL (USD)"],
                  ["1.1", "SVC-A", "Aruba 3Y FC NBD Exch HW 6300M SVC [for SW-A]", hp, 1000, 1, 1000, 0, 1000, "PL (USD)"],
                  ["1.2", "PSU-A", "HPE Aruba Networking X372 54VDC 680W Power Supply", hp, 900, 1, 900, 0, 900, "PL (USD)"],
                  ["1.3", "PSU-A      ABB", "  INCLUDED: Power Cord - Europe localization", hp, "incl.", 1, None, None, None, ""],
                  ["2.0", "SW-A", "HPE Aruba Networking CX 6300M 24p Smart Rate 1/2.5/5/10G Class6 PoE 4SFP56 Switch", hp, 20000, 1, 20000, 0, 20000, "PL (USD)"],
                  ["2.1", "SVC-A", "Aruba 3Y FC NBD Exch HW 6300M SVC [for SW-A]", hp, 1000, 1, 1000, 0, 1000, "PL (USD)"],
                  ["2.2", "PSU-A", "HPE Aruba Networking X372 54VDC 680W Power Supply", hp, 900, 1, 900, 0, 900, "PL (USD)"],
                  ["2.3", "PSU-A      ABB", "  INCLUDED: Power Cord - Europe localization", hp, "incl.", 1, None, None, None, ""],
                  ["3.0", "SUB-AP", "HPE Aruba Networking Central AP Foundation 3-year Subscription", hp, 400, 10, 4000, 0, 4000, "PL (USD)"],
                  ["3.1", "SUB-SW", "HPE Aruba Networking Central Switch Class-2 Foundation 3-year Subscription", hp, 1000, 2, 2000, 0, 2000, "PL (USD)"],
                  ["4.0", "DAC-1", "HPE Aruba Networking 10G SFP+ to SFP+ 3m Direct Attach Copper Cable", hp, 300, 4, 1200, 0, 1200, "PL (USD)"],
                  ["", "", "Quote Total", "", "", "", "", "", 51000, ""]])


def test_cisco_flat_estimate():
    d = bom.parse(cisco_flat_estimate())
    assert d["layout"] == "cisco_ccw" and sum(c["total"] for c in d["configs"]) == 44000
    a, b = d["configs"]
    assert a["title"].startswith("Catalyst 9200L 24-port") and a["qty"] == 4 and a["qtyMode"] == "total" and len(a["items"]) == 5
    assert b["title"].startswith("Catalyst 9300 48-port") and b["qty"] == 2 and b["total"] == 17600
    s = dict(a["summary"])
    assert s["Module quang (SFP)"] == "2 × 10GBASE-SR SFP Module"                                        # 8 for 4 switches
    assert s["License"] == "DNA Essentials" and "(12 tháng)" in s["Bảo hành / hỗ trợ"] and s["Giá 1 thiết bị"] == "6.600"


def test_hpe_iris_merges_identical_devices():
    d = bom.parse(hpe_iris())
    assert d["layout"] == "hpe_iris" and sum(c["total"] for c in d["configs"]) == 51000                   # = Quote Total
    sw, sub, dac = d["configs"]
    assert sw["qty"] == 2 and sw["total"] == 43800 and "Gộp 1 cấu hình" in d["warnings"][0]               # lines 1.0 + 2.0
    assert [i["empty"] for i in sw["items"]] == [False, False, False, True]                              # INCLUDED: power cord
    s = dict(sw["summary"])
    assert s["Nguồn"].startswith("1 × HPE Aruba Networking X372") and "SVC" in s["Bảo hành / hỗ trợ"]
    assert s["Tốc độ"].startswith("1/2.5/5/10GbE") and s["Giá 1 thiết bị"] == "21.900"
    assert sub["category"] == "software" and dict(sub["summary"])["Thành tiền"] == "6.000"
    assert dac["category"] == "accessory" and dac["qty"] == 4


def test_api_rejects_bad_shapes_and_odd_numbers(app_, editor):
    d = editor.post("/api/bom/parse", files={"file": ("bom.xlsx", dell_configurator())}).json()
    cfg = d["configs"][0]
    assert editor.post("/api/bom/summary", json={**cfg, "items": 5, "summary": {}}).status_code == 200      # ignored, no 500
    assert editor.post("/api/bom/export", json={"configs": [{**cfg, "items": "x"}]}).status_code == 400   # nothing left to export
    h = ["", "Group Name", "Group ID", "Product Name", "Product Qty", "Unit Price (List)", "Extended Price (List)", "Module Name",
         "Option ID", "Option Name", "Option List Price", "SKUs", "Qty"]
    odd = _xlsx([h, ["", "G", "1", "PowerEdge R770 - [X]", 1, 1, 1],
                 ["", "", "", "", "", "", "", "Memory Capacity", "X", "32GB RDIMM", "99999999999999", "370-A", "0.00000000000000000001"],
                 ["", "", "", "", "", "", "", "Base", "X", "PowerEdge R770 Server", 1, "210-A", 1]])
    assert editor.post("/api/bom/parse", files={"file": ("odd.xlsx", odd)}).status_code in (200, 422)
