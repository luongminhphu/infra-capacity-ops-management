"""Quotation engine on three real layouts of the same 2-room video-conference quote (FL6, 10/2026):
CMC — bundle line "Phòng họp × 2" priced, then the breakdown of ONE room;
SVT — the same bundle as a heading with its price, breakdown of one room;
HPT — heading without price, every line already × 2.
Software lines are VAT-exempt (KCT / "-"), hardware 8 %. All three must land on the vendor's printed totals."""
from decimal import Decimal as D

from app.spend import quotes as qm

# (description, qty, unit price, VAT printed) for ONE room
CMC_ROOM = [
    ("AirMedia Receiver 3200", 1, 101_860_000, 8_148_800), ("AirMedia Series 3 Connect Adaptor", 1, 31_300_000, 2_504_000),
    ("USB 3.0 Switcher", 1, 27_310_000, 2_184_800), ("USB over Category Cable Extender", 1, 39_850_000, 3_188_000),
    ("FT2 Cable Management System", 1, 33_940_000, 2_715_200), ("Cable Retractor HDMI", 1, 23_310_000, 1_864_800),
    ("Cable Retractor USB", 1, 21_220_000, 1_697_600), ("AC Power Outlet Module", 1, 9_470_000, 757_600),
    ("Crestron Flex Videobar 70", 1, 380_400_000, 30_432_000), ("Room Scheduling Touch Screen", 1, 59_600_000, 4_768_000),
    ("Occupancy Sensor", 1, 19_960_000, 1_596_800), ("Microsoft Teams Rooms Pro license", 1, 17_060_000, None),
    ("XiO Cloud Premium Subscription", 5, 1_710_000, None), ("Vật tư phụ lắp đặt", 1, 14_070_000, 1_125_600),
    ("Phí lắp đặt, cấu hình, triển khai", 1, 28_000_000, 2_240_000),
]
SVT_ROOM = [
    ("AirMedia Receiver 3200", 1, 96_370_000, 7_709_600), ("AirMedia Series 3 Connect Adaptor", 1, 29_610_000, 2_368_800),
    ("USB 3.0 Switcher", 1, 25_840_000, 2_067_200), ("USB over Category Cable Extender", 1, 37_700_000, 3_016_000),
    ("Crestron Flex Videobar 70", 1, 359_890_000, 28_791_200), ("FT2 Cable Management System", 1, 31_630_000, 2_530_400),
    ("Cable Retractor HDMI", 1, 21_720_000, 1_737_600), ("Cable Retractor USB", 1, 19_780_000, 1_582_400),
    ("AC Power Outlet Module", 1, 8_830_000, 706_400), ("Room Scheduling Touch Screen", 1, 56_390_000, 4_511_200),
    ("Occupancy Sensor", 1, 18_880_000, 1_510_400), ("Microsoft Teams Rooms Pro license", 1, 15_900_000, 0),
    ("XiO Cloud Premium Subscription", 5, 1_590_000, 0), ("Vật tư phụ lắp đặt", 1, 13_300_000, 1_064_000),
    ("Thi công lắp đặt cấu hình", 1, 26_700_000, 2_136_000),
]
HPT_LINES = [
    ("AirMedia Receiver 3200", 2, 103_410_000, 16_545_600), ("AirMedia Series 3 Connect Adaptor", 2, 31_770_000, 5_083_200),
    ("USB 3.0 Switcher", 2, 27_730_000, 4_436_800), ("USB over Category Cable Extender", 2, 40_450_000, 6_472_000),
    ("Crestron Flex Videobar 70", 2, 386_160_000, 61_785_600), ("FT2 Cable Management System", 2, 34_570_000, 5_531_200),
    ("Cable Retractor HDMI", 2, 23_740_000, 3_798_400), ("Cable Retractor USB", 2, 21_620_000, 3_459_200),
    ("AC Power Outlet Module", 2, 9_650_000, 1_544_000), ("Room Scheduling Touch Screen", 2, 60_510_000, 9_681_600),
    ("Occupancy Sensor", 2, 20_260_000, 3_241_600), ("Microsoft Teams Rooms Pro license", 2, 16_810_000, None),
    ("XiO Cloud Premium Subscription", 10, 1_680_000, None), ("Vật tư phụ lắp đặt", 2, 14_270_000, 2_283_200),
    ("Chi phí thi công lắp đặt", 2, 30_000_000, 4_800_000),
]


def _items(rows, vat_known=True):
    out = []
    for desc, qty, price, vat in rows:
        amount = D(qty) * D(price)
        # "-" / KCT on the quote: the AI returns "0" for the tax of exempt lines
        out.append(qm.Line("item", desc, qty=D(qty), unit_price=D(price), amount=amount,
                           vat_amount=(D(vat) if vat is not None else D(0)) if vat_known else None))
    return out


def _codes(findings):
    return {f.code for f in findings}


def test_cmc_bundle_line_with_per_room_breakdown():
    lines = [qm.Line("item", "Phòng họp Video Conference: Teams Room + BYOD", unit="Phòng họp",
                     qty=D(2), unit_price=D(815_900_000), amount=D(1_631_800_000))] + _items(CMC_ROOM)
    # Before: bundle and its breakdown were both counted → 2.447.700.000 (1,5 ×)
    assert qm.items_sum(lines) == D(2_447_700_000)
    notes = qm.normalize_bundles(lines, D(1_631_800_000))
    assert len(notes) == 1 and "2 × 815.900.000" in notes[0]
    assert lines[0].line_type == "note" and all(l.sets == 2 for l in lines[1:])
    f, comp = qm.check(lines, D(1_631_800_000), None, D(126_446_400), D(1_758_246_400), D(8))
    assert comp["subtotal"] == D(1_631_800_000)
    assert comp["vat"] == D(126_446_400)                # 8 % × (815.900.000 − 25.610.000 KCT) × 2
    assert comp["vatRates"] == [0.0, 8.0]
    assert not {"SUBTOTAL", "VAT", "TOTAL"} & _codes(f)


def test_svt_bundle_heading_with_price():
    lines = [qm.Line("note", "Phòng họp Video Conference — không gồm màn hình", unit="Phòng họp",
                     qty=D(2), unit_price=D(770_490_000), amount=D(1_540_980_000)),
             qm.Line("note", "Thiết bị và Phần mềm"), qm.Line("note", "Hệ thống trình chiếu không dây", qty=D(1), unit="Bộ")]
    lines += _items(SVT_ROOM)
    assert qm.items_sum(lines) == D(770_490_000)        # before: half of the vendor's subtotal
    assert qm.normalize_bundles(lines, D(1_540_980_000))
    f, comp = qm.check(lines, D(1_540_980_000), None, D(119_462_400), D(1_660_442_400), D(8))
    assert comp["subtotal"] == D(1_540_980_000)
    assert comp["vat"] == D(119_462_400)
    assert not {"SUBTOTAL", "VAT", "TOTAL"} & _codes(f)


def test_hpt_lines_already_multiplied_and_per_line_vat():
    lines = [qm.Line("note", "Phòng họp smart meeting room — Crestron B70", unit="Gói", qty=D(2))] + _items(HPT_LINES)
    assert qm.normalize_bundles(lines, D(1_658_700_000)) == []          # already × 2: untouched
    assert all(l.sets is None for l in lines)
    f, comp = qm.check(lines, D(1_658_700_000), None, D(128_662_400), D(1_787_362_400), D(8))
    assert comp["subtotal"] == D(1_658_700_000)
    assert comp["vat"] == D(128_662_400)                # 8 % × (1.658.700.000 − 50.420.000)
    assert not {"SUBTOTAL", "VAT", "TOTAL"} & _codes(f)


def test_vat_hint_names_exempt_software_lines_when_no_per_line_tax():
    lines = _items(HPT_LINES, vat_known=False)
    f, comp = qm.check(lines, D(1_658_700_000), None, D(128_662_400), D(1_787_362_400), D(8))
    vat = next(x for x in f if x.code == "VAT")
    assert vat.expected == D(132_696_000)
    assert "Microsoft Teams Rooms Pro license" in vat.message and "50.420.000" in vat.message


def test_subtotal_hint_when_breakdown_is_for_one_room():
    lines = _items(SVT_ROOM)                            # bundle line missing altogether
    assert qm.normalize_bundles(lines, D(1_540_980_000)) == []
    f, _ = qm.check(lines, D(1_540_980_000), None, None, None, D(8))
    sub = next(x for x in f if x.code == "SUBTOTAL")
    assert "gấp 2 lần" in sub.message and "Số bộ = 2" in sub.message


def test_subtotal_hint_names_the_bundle_line():
    # entered by hand like the first AI read of CMC: bundle line + breakdown, no Số bộ
    lines = [qm.Line("item", "Phòng họp VC × 2", qty=D(2), unit_price=D(815_900_000), amount=D(1_631_800_000))] + _items(CMC_ROOM)
    f, _ = qm.check(lines, D(1_631_800_000), None, None, None, D(8))
    sub = next(x for x in f if x.code == "SUBTOTAL")
    assert "Phòng họp VC × 2" in sub.message and "dòng gói" in sub.message


def test_subtotal_hint_names_double_counted_group_total():
    lines = _items(CMC_ROOM) + [qm.Line("item", "Cộng phần lắp đặt", qty=D(1), unit_price=D(42_070_000), amount=D(42_070_000))]
    f, _ = qm.check(lines, D(815_900_000), None, None, None, D(8))
    sub = next(x for x in f if x.code == "SUBTOTAL")
    assert "Cộng phần lắp đặt" in sub.message and "tính trùng" in sub.message


def test_bundle_not_applied_when_it_does_not_explain_the_vendor_subtotal():
    lines = [qm.Line("item", "Phòng họp", qty=D(2), unit_price=D(815_900_000), amount=D(1_631_800_000))] + _items(CMC_ROOM)
    assert qm.normalize_bundles(lines, D(2_000_000_000)) == []
    assert lines[0].line_type == "item" and all(l.sets is None for l in lines)


def test_regular_items_with_quantity_are_not_taken_for_bundles():
    lines = [qm.Line("item", "Switch", qty=D(2), unit_price=D(30_000_000), amount=D(60_000_000)),
             qm.Line("item", "SFP", qty=D(1), unit_price=D(10_000_000), amount=D(10_000_000)),
             qm.Line("item", "Patch cord", qty=D(1), unit_price=D(20_000_000), amount=D(20_000_000))]
    # 10 + 20 = 30 = the switch unit price, but the switch has a price of its own and the vendor subtotal is 90
    assert qm.normalize_bundles(lines, D(90_000_000)) == []
    assert qm.items_sum(lines) == D(90_000_000)


def test_excel_reader_keeps_per_line_vat_and_kct():
    rows = [["STT", "Mô tả", "SL", "Đơn giá", "Thành tiền", "Thuế GTGT (VAT)", "Thành tiền sau thuế"],
            [1, "Bộ thu AirMedia", 1, 101860000, 101860000, 8148800, 110008800],
            [2, "Teams Rooms Pro license", 1, 17060000, 17060000, "-", 17060000],
            ["", "Tổng cộng", None, None, 118920000, None, None]]
    ex = qm.lines_from_rows(rows)
    items = [l for l in ex.lines if l.line_type == "item"]
    assert [l.vat_amount for l in items] == [D(8_148_800), D(0)]
    assert [qm.line_vat_pct(l) for l in items] == [D(8), D(0)]


# ---------------------------------------------------------------- API: AI read of the SVT layout, manual fix
import json  # noqa: E402

from app.spend import ai  # noqa: E402
from test_spend_opex import admin, app_, editor  # noqa: E402,F401  (fixtures)
from test_spend_quotes import KEY, _new_request, _upload  # noqa: E402


def _ai_svt_payload():
    lines = [{"line_type": "note", "description": "Phòng họp Video Conference — không gồm màn hình", "model": "", "unit": "Phòng họp",
              "qty": "2", "unit_price": "770.490.000", "amount": "1.540.980.000", "vat_pct": "", "vat_amount": "119.462.400"}]
    for desc, qty, price, vat in SVT_ROOM:
        lines.append({"line_type": "item", "description": desc, "model": "", "unit": "Bộ", "qty": str(qty),
                      "unit_price": f"{price:,}".replace(",", "."), "amount": f"{qty * price:,}".replace(",", "."),
                      "vat_pct": "0" if vat == 0 else "", "vat_amount": "KCT" if vat == 0 else f"{vat:,}"})
    return {"quote_no": "", "quote_date": "2026-10-05", "valid_until": "", "currency": "VND", "vat_pct": "8",
            "subtotal": "1.540.980.000", "discount": "", "vat": "119.462.400", "total": "1.660.442.400",
            "delivery_terms": "04 đến 08 tuần", "warranty_terms": "01 năm", "payment_terms": "30% / 70%", "lines": lines}


def test_ai_read_of_bundle_quote_lands_on_vendor_totals(admin, editor, monkeypatch):
    code = _new_request(editor, title="VC 2 phòng FL6", workstream="vc")["code"]
    q = _upload(editor, code, "SVT", b"%PDF-1.7\n% scan", name="svt.pdf").json()["quotations"][0]
    assert admin.put("/api/spend/proposals/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True}).status_code == 200
    monkeypatch.setattr(ai, "QUOTE_CALL", lambda *a: json.dumps(_ai_svt_payload()))
    out = editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/extract").json()
    assert out["computedSubtotal"] == 1_540_980_000                    # was 770.490.000 (one room)
    assert out["vatRates"] == [0.0, 8.0]
    assert not {f["code"] for f in out["findings"]} & {"SUBTOTAL", "VAT", "TOTAL"}
    assert "Đã nhận dạng gói" in out["message"]
    items = [l for l in out["lines"] if l["lineType"] == "item"]
    assert {l["sets"] for l in items} == {2.0} and items[11]["vatAmount"] == 0
    # The comparison counts the breakdown × 2 rooms, like vendors that print SL 2 on every line.
    cmp = editor.get(f"/api/spend/quotes/{code}/comparison").json()
    videobar = next(r for r in cmp["rows"] if "Videobar" in r["description"])
    assert videobar["cells"][str(q["id"])]["amountVnd"] == 719_780_000

    # Manual edit with vi-VN grouped numbers: Số bộ back to 1 on one line → the subtotal check flags it.
    lines = [{**{k: l[k] for k in ("lineType", "description", "model", "unit", "matchKey")},
              "qty": l["qty"], "unitPrice": f"{int(l['unitPrice'] or 0):,}".replace(",", ".") if l["unitPrice"] else "",
              "amount": f"{int(l['amount'] or 0):,}".replace(",", ".") if l["amount"] else "",
              "vatPct": l["vatPct"], "vatAmount": l["vatAmount"], "sets": l["sets"]} for l in out["lines"]]
    lines[1]["sets"] = 1
    body = {"quoteNo": "", "currency": "VND", "vatPct": "8", "statedSubtotal": "1.540.980.000", "statedVat": "119.462.400",
            "statedTotal": "1.660.442.400", "lines": lines, "rowVersion": out["rowVersion"]}
    r = editor.put(f"/api/spend/quotes/{code}/quotations/{q['id']}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["computedSubtotal"] == 1_540_980_000 - 96_370_000
    assert "SUBTOTAL" in {f["code"] for f in r.json()["findings"]}
    bad = dict(body, lines=[dict(lines[1], vatPct="25")], rowVersion=r.json()["rowVersion"])
    assert editor.put(f"/api/spend/quotes/{code}/quotations/{q['id']}", json=bad).status_code == 400


def test_out_of_range_numbers_from_the_ai_are_dropped_not_a_500(admin, editor, monkeypatch):
    """Production 500 (PostgreSQL "numeric field overflow"): the AI put the tax amount 126.446.400 in the quotation's
    VAT rate (NUMERIC(5,2)), or a misaligned scan cell in a quantity. Such numbers are left empty for the checker."""
    code = _new_request(editor, title="VC — số đọc sai", workstream="vc")["code"]
    q = _upload(editor, code, "CMC", b"%PDF-1.7\n% scan", name="cmc.pdf").json()["quotations"][0]
    assert admin.put("/api/spend/proposals/ai", json={"provider": "anthropic", "apiKey": KEY, "allowExternal": True}).status_code == 200
    raw = _ai_svt_payload()
    raw["vat_pct"] = "119.462.400"                     # tax amount read as the rate
    raw["lines"][3]["qty"] = "98.765.432.100.000"      # amount column read as a quantity
    raw["lines"][4]["vat_pct"] = "2.368.800"           # line tax read as a line rate
    monkeypatch.setattr(ai, "QUOTE_CALL", lambda *a: json.dumps(raw))
    r = editor.post(f"/api/spend/quotes/{code}/quotations/{q['id']}/extract")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["computedSubtotal"] == 1_540_980_000 and out["vatPct"] == 8.0      # rate inferred from the lines
    assert "không hợp lý" in out["message"]
    assert out["lines"][3]["qty"] is None and "MISSING" in {f["code"] for f in out["findings"]}
    # The same mistakes typed by hand get a 400 with a sentence, not a 500.
    base = {"quoteNo": "", "currency": "VND", "lines": [], "rowVersion": out["rowVersion"]}
    r = editor.put(f"/api/spend/quotes/{code}/quotations/{q['id']}", json={**base, "vatPct": "126.446.400"})
    assert r.status_code == 400 and "thuế suất" in r.json()["detail"]
    line = {"lineType": "item", "description": "Videobar", "qty": "98.765.432.100.000", "unitPrice": "1", "amount": "1"}
    r = editor.put(f"/api/spend/quotes/{code}/quotations/{q['id']}", json={**base, "lines": [line]})
    assert r.status_code == 400 and "SL quá lớn" in r.json()["detail"]
