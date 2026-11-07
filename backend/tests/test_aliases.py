"""一物多码：库存只记我们的 SKU；大统华 Item#、Supreme 外库代码、供应商编码、QuickBooks 品名都是对照。
场景取自真实单据：Supreme Logistics BOL S26-39513（Vatee-001 油条 40 箱、Sesame001 芝麻球 84 箱…）与我们开给大统华的发票。"""
import uuid

from test_agent import call, connect, enable


def K():
    return str(uuid.uuid4())


def master(client, h):
    return client.get("/api/inv/master", headers=h).json()


def ids(client, h):
    m = master(client, h)
    return {s["code"]: s["id"] for s in m["skus"]}, {w["code"]: w for w in m["warehouses"]}


def bal(client, h, sku_id, wh_id, bucket="on_hand"):
    rows = client.get(f"/api/inv/stock?warehouse_id={wh_id}", headers=h).json()
    rows = rows["rows"] if isinstance(rows, dict) else rows
    return round(sum(r.get(bucket, 0) for r in rows if r["sku_id"] == sku_id), 4)


def test_ALS01_supreme_bol_with_aliases_to_quickbooks(client, login):
    ops = login("ops")
    b = client.post("/api/batches", json={"kind": "sku_upsert", "rows": [
        {"code": "QQ-YOUTIAO", "name": "油条", "category": "成品", "base_unit": "箱"},
        {"code": "QQ-SESAME", "name": "芝麻球", "category": "成品", "base_unit": "箱"},
        {"code": "QQ-MANGBUN", "name": "芒果西柚西米包", "category": "成品", "base_unit": "箱"}]}, headers=ops).json()
    client.post(f"/api/batches/{b['id']}/apply", json={"selected": [0, 1, 2]}, headers=ops)
    sk, wh = ids(client, ops)
    assert wh["SUPREME"]["tracked"] is True and wh["SUPREME"]["kind"] == "external"

    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    # 1) ChatGPT 提交对照：外库、大统华、QuickBooks、国内供应商（16 袋 = 1 箱）
    res = call(client, tok, "propose_item_aliases", {"source_summary": "BOL S26-39513 + 发票 QQUSA20261108", "rows": [
        {"sku_code": "QQ-YOUTIAO", "party": "SUPREME", "code": "Vatee-001", "name": "FRZ CANTONESE FRIED DOUGH STICK", "unit": "箱", "factor": 1},
        {"sku_code": "QQ-YOUTIAO", "party": "T&T", "code": "538949", "name": "CHINESE DONUT", "pack_size": "120g×4×16/CS", "unit": "case", "factor": 1},
        {"sku_code": "QQ-YOUTIAO", "party": "QuickBooks", "name": "油条CHINESE DONUT"},
        {"sku_code": "QQ-YOUTIAO", "party": "某食品厂", "party_kind": "supplier", "code": "YT-120", "name": "广式油条 120g", "unit": "袋", "factor": 0.0625},
        {"sku_code": "Vatee-001", "party": "SUPREME", "code": "Vatee-001"}]})["structuredContent"]
    assert res["summary"] == {"new": 4, "error": 1} and "我们自己的 SKU" in " ".join(res["errors"][0]["problems"])
    bid = res["confirm_url"].split("batch=")[1]
    assert client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1, 2, 3]}, headers=ops).json()["status"] == "partial"
    # any party's code finds our SKU: lookup, scan, global search, ChatGPT
    assert client.get("/api/inv/scan/Vatee-001", headers=ops).json()["code"] == "QQ-YOUTIAO"
    assert client.get("/api/inv/scan/538949", headers=ops).json()["sku_id"] == sk["QQ-YOUTIAO"]
    assert any(r["type"] == "sku" and "QQ-YOUTIAO" in r["title"] for r in client.get("/api/search?q=YT-120", headers=ops).json())
    al = call(client, tok, "list_item_aliases", {"sku_code": "QQ-YOUTIAO"})["structuredContent"]
    assert {(a["party_kind"], a["code"] or a["name"]) for a in al["aliases"]} >= {("warehouse", "Vatee-001"), ("customer", "538949"),
                                                                                    ("accounting", "油条CHINESE DONUT"), ("supplier", "YT-120")}
    assert "SUPREME:Vatee-001" in call(client, tok, "get_master_data", {"query": "QQ-YOUTIAO"})["structuredContent"]["skus"][0]["aliases"]

    # 2) Supreme 期初库存（外库报表是他们的代码）：Vatee-001 认得；Sesame001 不认得 → 确认页选一次并记住
    res = call(client, tok, "propose_stock_counts", {"source_summary": "Supreme 11/1 库存报表", "rows": [
        {"warehouse_code": "SUPREME", "party_code": "Vatee-001", "qty": 40},
        {"warehouse_code": "SUPREME", "party_code": "Sesame001", "party_name": "FRZ SESAME BALLS", "qty": 50}]})["structuredContent"]
    assert res["summary"] == {"set": 1, "unmapped": 1}
    bid = res["confirm_url"].split("batch=")[1]
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1], "overrides": {"1": {"sku_id": sk["QQ-SESAME"]}}}, headers=ops).json()
    assert r["status"] == "applied"
    sup = wh["SUPREME"]["id"]
    assert bal(client, ops, sk["QQ-YOUTIAO"], sup) == 40 and bal(client, ops, sk["QQ-SESAME"], sup) == 50

    # 3) BOL：外库代码 + Lot；Sesame001 已记住；芝麻球账面只有 50，BOL 84 → 例外需原因；Mang-Bun-001 选一次
    bol = {"from_code": "SUPREME", "note": "BOL S26-39513", "expected_date": "2026-11-03", "source_summary": "BOL S26-39513 照片", "lines": [
        {"party_code": "Vatee-001", "party_name": "FRZ CANTONESE FRIED DOUGH STICK", "lot_code": "2610-2228", "qty": 40},
        {"party_code": "Sesame001", "lot_code": "2610-1110", "qty": 84},
        {"party_code": "Mang-Bun-001", "party_name": "FRZ MANGO POMELO SAGO BUN", "lot_code": "2609-0811", "qty": 80}]}
    res = call(client, tok, "propose_inbound", bol)["structuredContent"]
    assert res["summary"] == {"mapped": 2, "unmapped": 1}
    assert any("账面不够：差 34" in " ".join(w["note"]) for w in res["warnings"])
    bid = res["confirm_url"].split("batch=")[1]
    body = {"selected": [0, 1, 2], "overrides": {"2": {"sku_id": sk["QQ-MANGBUN"]}}}
    no = client.post(f"/api/batches/{bid}/apply", json=body, headers=ops)
    assert no.status_code == 409 and no.json()["detail"]["code"] == "source_short"
    # Mang bun has no stock at Supreme at all → also covered by the reason
    r = client.post(f"/api/batches/{bid}/apply", json={**body, "shortfall_reason": "Supreme 期初报表漏了 11/1 后入库的货"}, headers=ops).json()
    assert r["status"] == "applied"
    tr = next(t for t in client.get("/api/inv/transfers", headers=ops).json() if t["id"] == r["result"]["transfer_id"])
    assert "例外" in tr["note"] and "BOL S26-39513" in tr["note"]
    # Supreme emptied for these SKUs (unlotted opening stock relabeled to the BOL lot); 597 has them in transit
    assert bal(client, ops, sk["QQ-YOUTIAO"], sup) == 0 and bal(client, ops, sk["QQ-SESAME"], sup) == 0
    w597 = wh["WH597"]["id"]
    assert bal(client, ops, sk["QQ-YOUTIAO"], w597, "in_transit") == 40
    # the Mang-Bun mapping was remembered for next time
    again = call(client, tok, "propose_inbound", {**bol, "lines": bol["lines"][2:], "source_summary": "下一张"})["structuredContent"]
    assert again["summary"] == {"mapped": 1}
    client.post(f"/api/batches/{again['confirm_url'].split('batch=')[1]}/reject", json={}, headers=ops)
    # receive at 597
    client.post(f"/api/inv/transfers/{tr['id']}/receive", json={"idempotency_key": K(), "lines": [
        {"line_id": l["id"], "qty_received": l["qty_shipped"], "qty_damaged": 0} for l in tr["lines"]]}, headers=ops)
    assert bal(client, ops, sk["QQ-YOUTIAO"], w597) == 40

    # 4) 大统华 PO 用他们的 Item#：已对上；两张 PO 发货后合成一张 QuickBooks 发票
    oids = []
    for po, lines in (("623030901", [{"item_no": "538949", "description": "CHINESE DONUT", "unit": "case", "qty": 10, "price": 46.5}]),
                      ("623030902", [{"item_no": "539950", "description": "FRIED SESAME BAL", "unit": "case", "qty": 5, "price": 52, "sku_code": "QQ-SESAME"}])):
        res = call(client, tok, "propose_order", {"po_no": po, "required_date": "2026-11-10", "site": "Chino Hills", "source_summary": po, "lines": lines})["structuredContent"]
        assert res["summary"] == ({"mapped": 1} if po.endswith("901") else {"new": 1})
        r = client.post(f"/api/batches/{res['confirm_url'].split('batch=')[1]}/apply", json={"selected": [0]}, headers=ops).json()
        oid = r["result"]["order_id"]
        so = client.get(f"/api/orders/{oid}", headers=ops).json()
        client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": so["lines"][0]["id"], "qty": so["lines"][0]["qty_ordered"]}]}, headers=ops)
        oids.append(oid)
    inv = client.post("/api/orders/quickbooks", json={"order_ids": oids, "invoice_no": "QQUSA20261108", "invoice_date": "2026-11-04",
                                                      "customer_name": "T&T Supermarket US Inc."}, headers=ops).json()
    assert inv["rows"] == 2 and inv["total"] == 725.0 and inv["due_date"] == "2026-12-04"
    lines = inv["csv"].lstrip("﻿").splitlines()
    assert lines[0].startswith("InvoiceNo,Customer,InvoiceDate,DueDate")
    assert "油条CHINESE DONUT" in lines[1] and "538949 CHINESE DONUT" in lines[1] and "PO 623030901 / 623030902" in lines[1]
    assert "11/04/2026" in lines[1] and "12/04/2026" in lines[1]
    assert any("QQ-SESAME" in w for w in inv["warnings"])  # no QuickBooks name yet → flagged
    assert client.get(f"/api/orders/{oids[0]}", headers=ops).json()["customer_accounting_name"] == "T&T Supermarket US Inc."


def test_ALS02_manage_aliases_in_app(client, login):
    ops, wh_user = login("ops"), login("staff1")
    sk, wh = ids(client, ops)
    sid = sk["QQ-SESAME"]
    a = client.post("/api/aliases", json={"sku_id": sid, "party_kind": "warehouse", "party": "Supreme 外库",
                                          "code": "Sesame-002", "name": "FRZ SESAME BALLS 2"}, headers=ops)
    assert a.status_code == 200
    rows = client.get(f"/api/aliases?sku_id={sid}", headers=ops).json()
    mine = next(x for x in rows if x["code"] == "Sesame-002")
    assert mine["party"] == "SUPREME"  # stored by warehouse code even when given by name
    # the same Supreme code cannot point to two SKUs
    clash = client.put(f"/api/aliases/{mine['id']}", json={"sku_id": sid, "party_kind": "warehouse", "party": "SUPREME", "code": "Vatee-001"}, headers=ops)
    assert clash.status_code == 409
    # customer-side mapping edits the T&T item table
    c = client.post("/api/aliases", json={"sku_id": sid, "party_kind": "customer", "party": "TNT", "code": "539950", "name": "FRIED SESAME BAL"}, headers=ops)
    assert c.json()["source"] == "customer_item"
    assert client.get("/api/aliases/lookup?q=539950", headers=ops).json()[0]["sku"]["code"] == "QQ-SESAME"
    assert {k["key"] for k in client.get("/api/aliases/parties", headers=ops).json()["kinds"]} == {"customer", "warehouse", "supplier", "accounting", "other"}
    # frontline staff cannot edit master data
    assert client.post("/api/aliases", json={"sku_id": sid, "party_kind": "other", "party": "旧称", "name": "芝麻球（旧）"}, headers=wh_user).status_code == 403
    assert client.delete(f"/api/aliases/{mine['id']}", headers=ops).json()["ok"]
    assert client.post("/api/aliases", json={"sku_id": sid, "party_kind": "warehouse", "party": "NOPE", "code": "x"}, headers=ops).status_code == 400


def test_ALS03_manual_transfer_from_tracked_source_shortfall(client, login):
    ops = login("ops")
    sk, wh = ids(client, ops)
    t = client.post("/api/inv/transfers", json={"from_wh": wh["SUPREME"]["id"], "to_wh": wh["WH597"]["id"],
                                                "lines": [{"sku_id": sk["QQ-MANGBUN"], "qty": 5, "lot_code": "X1"}]}, headers=ops).json()
    line = t["lines"][0]["id"]
    no = client.post(f"/api/inv/transfers/{t['id']}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line, "qty": 5}]}, headers=ops)
    assert no.status_code == 409 and no.json()["detail"]["code"] == "source_short"
    ok = client.post(f"/api/inv/transfers/{t['id']}/ship", json={"idempotency_key": K(), "lines": [{"line_id": line, "qty": 5}],
                                                                  "shortfall_reason": "外库实际有货，报表未更新"}, headers=ops)
    assert ok.status_code == 200 and "例外" in ok.json()["transfer"]["note"]


def test_ALS04_name_first_deliver_without_po_then_fill(client, login):
    """以品名为主：商品可以只有品名；先送货（没有 PO、没有大统华 Item#），PO 到了再补到原单上。"""
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    res = call(client, tok, "propose_skus", {"source_summary": "库存表", "rows": [
        {"name": "芋头地瓜丸", "category": "成品", "base_unit": "箱"},
        {"name": "红糖小米糕", "category": "成品", "base_unit": "箱", "tnt_item_no": "777001", "tnt_name": "BROWN SUGAR MILLET CAKE"}]})["structuredContent"]
    assert res["summary"] == {"new": 2}
    client.post(f"/api/batches/{res['confirm_url'].split('batch=')[1]}/apply", json={"selected": [0, 1]}, headers=ops)
    m = {s["name"]: s for s in master(client, ops)["skus"]}
    taro, cake = m["芋头地瓜丸"], m["红糖小米糕"]
    assert taro["code"].startswith("QQ-") and cake["tnt_item_no"] == "777001" and taro["tnt_item_no"] == ""
    # same name again without a code → updates the existing product, adds the T&T number later
    again = call(client, tok, "propose_skus", {"source_summary": "补 Item#", "rows": [{"name": "芋头地瓜丸", "tnt_item_no": "538956"}]})["structuredContent"]
    assert again["summary"] == {"update": 1}
    client.post(f"/api/batches/{again['confirm_url'].split('batch=')[1]}/reject", json={}, headers=ops)  # 先不补，等 PO
    # stock + a delivery with no PO and no Item#: matched by our name
    w = {x["code"]: x["id"] for x in master(client, ops)["warehouses"]}
    client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": taro["id"], "warehouse_id": w["WH597"], "qty": 20, "lot_code": "TARO1"}, headers=ops)
    d = call(client, tok, "propose_order", {"site": "Chino Hills", "required_date": "2026-11-07", "source_summary": "先送货，PO 未到",
                                            "lines": [{"description": "芋头地瓜丸", "unit": "case", "qty": 5, "price": 0}]})["structuredContent"]
    assert d["summary"] == {"mapped": 1} and any("没有 PO 号" in " ".join(x["note"]) for x in d["warnings"])
    r = client.post(f"/api/batches/{d['confirm_url'].split('batch=')[1]}/apply", json={"selected": [0]}, headers=ops).json()
    oid = r["result"]["order_id"]
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert so["po_no"] == "" and so["lines"][0]["item_no"] == ""
    client.post(f"/api/orders/{oid}/ship", json={"idempotency_key": K(), "lines": [{"line_id": so["lines"][0]["id"], "qty": 5}]}, headers=ops)
    # PO arrives (bilingual line, like T&T's) → matched by name, offered to fill the earlier delivery
    po = call(client, tok, "propose_order", {"po_no": "623031000", "po_date": "2026-11-08", "site": "Chino Hills", "source_summary": "PO 到了",
                                             "lines": [{"item_no": "538956", "description": "TARO & SWEET POTATO PASTE BALLS 芋头地瓜丸", "unit": "case", "qty": 5, "price": 60},
                                                       {"item_no": "777001", "description": "BROWN SUGAR MILLET CAKE", "unit": "case", "qty": 2, "price": 40}]})["structuredContent"]
    assert po["summary"] == {"mapped": 2}
    assert any(c["code"] == so["code"] for c in po["possible_earlier_delivery"])
    bid = po["confirm_url"].split("batch=")[1]
    assert any(c["id"] == oid for c in client.get(f"/api/batches/{bid}", headers=ops).json()["fill_candidates"])
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0, 1], "fill_order_id": oid}, headers=ops).json()
    assert r["result"]["order_id"] == oid and any("PO 多了一行" in n for n in r["result"]["fill_notes"])
    so = client.get(f"/api/orders/{oid}", headers=ops).json()
    assert so["po_no"] == "623031000" and so["lines"][0]["item_no"] == "538956" and so["lines"][0]["price"] == 60
    assert so["status"] == "partial" and len(so["lines"]) == 2  # 小米糕 still to ship
    assert {s["name"]: s["tnt_item_no"] for s in master(client, ops)["skus"]}["芋头地瓜丸"] == "538956"
    # manual: type the PO number onto an order later
    d2 = client.post("/api/orders", json={"customer_id": so["customer_id"], "lines": [{"description": "芋头地瓜丸", "qty": 1, "sku_id": taro["id"]}]}, headers=ops).json()
    o2 = client.get(f"/api/orders/{d2['id']}", headers=ops).json()
    p = client.patch(f"/api/orders/{d2['id']}", json={"version": o2["version"], "po_no": "623031001"}, headers=ops).json()
    assert p["po_no"] == "623031001"
    assert client.patch(f"/api/orders/{d2['id']}", json={"version": p["version"], "po_no": "623031000"}, headers=ops).status_code == 409
    # documents from a 3PL / Toronto with only a name are recognised too
    inb = call(client, tok, "propose_inbound", {"from_code": "TOR", "source_summary": "装箱单", "lines": [{"party_name": "红糖小米糕", "qty": 3}]})["structuredContent"]
    assert inb["summary"] == {"new": 1}
