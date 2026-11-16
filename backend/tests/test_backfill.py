"""历史业务补录：权限、混合明细、单位换算、关联已有记录、防重复、修改差额与作废冲销。
场景：2602/2603 已导入只补单据；2604 未到货不能加库存；猪肉丁 / 鸡丁已交付补原料采购与耗用；Costco 小票混合；调料漏记收货。"""
import uuid

from app.db import SessionLocal
from app.models import InventoryPosting


def K():
    return str(uuid.uuid4())


def M(client, h):
    return client.get("/api/inv/master", headers=h).json()


def wh(client, h, code="WH597"):
    return next(w["id"] for w in M(client, h)["warehouses"] if w["code"] == code)


def onhand(client, h, sid, code="WH597"):
    w = wh(client, h, code)
    return round(sum(r["on_hand"] for r in client.get("/api/inv/stock", headers=h).json()["rows"]
                     if r["sku_id"] == sid and r["warehouse_id"] == w), 4)


def sku(client, h, name, cat="干货", unit="箱", conv=None):
    r = client.post("/api/inv/skus", json={"name": name, "category": cat, "base_unit": unit, "conversions": conv or {}}, headers=h)
    return r.json()["id"] if r.status_code == 200 else next(s["id"] for s in M(client, h)["skus"] if s["name"] == name)


def mk(client, h, **kw):
    body = {"kind": "purchase_receipt", "business_date": "2026-11-01", "reason": "测试补录", **kw}
    r = client.post("/api/backfill", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, h, rec, **kw):
    return client.post(f"/api/backfill/{rec['id']}/confirm", json={"version": rec["version"], **kw}, headers=h)


def line(plan, i=0):
    return plan["lines"][i]


def test_permissions_only_boss_and_admin(client, login):
    for who in ("ops", "wh", "buyer", "prod"):
        h = login(who)
        assert client.get("/api/backfill", headers=h).status_code == 403
        assert client.post("/api/backfill", json={"kind": "expense", "business_date": "2026-11-01", "lines": [{"name": "x"}]},
                           headers=h).status_code == 403
    boss, admin = login("boss"), login("admin")
    rec = mk(client, boss, kind="expense", supplier="HB 权限测试", lines=[{"name": "停车费", "item_type": "费用", "amount": 12}])
    assert client.get("/api/backfill", headers=admin).status_code == 200
    # 别的角色不能确认 / 修改 / 作废别人建的补录
    ops = login("ops")
    assert confirm(client, ops, rec).status_code == 403
    assert client.post(f"/api/backfill/{rec['id']}/void", json={"version": rec["version"], "reason": "x"}, headers=ops).status_code == 403
    assert client.post(f"/api/backfill/{rec['id']}/revise", json={"version": rec["version"], "changes": {"note": "x"}}, headers=ops).status_code == 403
    r = confirm(client, admin, rec)
    assert r.status_code == 200 and r.json()["posting_id"] is None  # 费用：只补单据，不动库存


def test_container_2602_already_imported_doc_only(client, login):
    """2602 柜已导入库存（期初批次），补历史调拨记录时只补单据；想加库存会被拦下（疑似重复）。"""
    h = login("admin")
    a = sku(client, h, "HB 卤水料包")
    code = next(s["code"] for s in M(client, h)["skus"] if s["id"] == a)
    b = client.post("/api/batches", json={"kind": "stock_count", "meta": {"as_of": "2026-11-05"}, "source_note": "2602 柜期初",
                                          "rows": [{"sku_code": code, "warehouse_code": "WH597", "lot_code": "HB2602", "qty": 81}]}, headers=h).json()
    client.post(f"/api/batches/{b['id']}/apply", json={"selected": [0]}, headers=h)
    before = onhand(client, h, a)
    rec = mk(client, h, kind="transfer", from_warehouse_id="TOR", container_no="HB2602", business_date="2026-10-28",
             reason="补 2602 柜调拨单据，库存已在期初导入", lines=[{"sku_id": a, "qty": 84, "unit": "箱"}])
    p = rec["plan"]
    assert any("HB2602" in d or b["code"] in d for d in p["dups"])  # 认出已有的库存导入
    assert line(p)["mode"] == "doc_only" and not line(p)["deltas"]
    assert confirm(client, h, rec).status_code == 200
    assert onhand(client, h, a) == before
    # 同一柜再来一张、想加库存：期初之后的结存已含 → 疑似重复，必须逐行确认；并且疑似重复单据
    rec2 = mk(client, h, kind="transfer", from_warehouse_id="TOR", container_no="HB2602", business_date="2026-10-28",
              reason="重复测试", lines=[{"sku_id": a, "qty": 84, "unit": "箱", "inventory": "post", "lot_code": "HB2602"}])
    l0 = line(rec2["plan"])
    assert l0["suspects"] and l0["deltas"][0]["before"] == before and l0["deltas"][0]["after"] == before + 84
    r = confirm(client, h, rec2)
    assert r.status_code == 409 and "疑似" in r.json()["detail"]["msg"]
    assert onhand(client, h, a) == before


def test_container_2604_in_transit_cannot_add_stock(client, login):
    h = login("admin")
    a = sku(client, h, "HB 2604 辣椒油")
    rec = mk(client, h, kind="transfer", from_warehouse_id="TOR", container_no="HB2604", goods_status="in_transit",
             reason="补 2604 装柜资料", lines=[{"sku_id": a, "qty": 50, "inventory": "post"}])
    assert any("在途" in x for x in line(rec["plan"])["blockers"])
    assert confirm(client, h, rec).status_code == 409
    r = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": rec["version"], "changes": {"lines": [{"id": "L001", "sku_id": a, "qty": 50}]}}, headers=h)
    assert r.status_code == 200
    assert confirm(client, h, r.json()["record"]).status_code == 200
    assert onhand(client, h, a) == 0


def test_purchase_order_cannot_post(client, login):
    h = login("admin")
    a = sku(client, h, "HB 采购单商品")
    rec = mk(client, h, kind="purchase_order", supplier="HB 供应商", lines=[{"sku_id": a, "qty": 10, "inventory": "post"}])
    assert any("订货" in x for x in line(rec["plan"])["blockers"])
    assert confirm(client, h, rec).status_code == 409


def test_meat_delivered_backfill_raw_purchase_and_use(client, login):
    """猪肉丁 300 lb 已交付（送货单发货时已扣成品）：补原料采购收货 +325、生产耗用 −325；成品不能再扣。"""
    h = login("admin")
    raw = sku(client, h, "HB 猪梅头", cat="原料肉", unit="磅")
    fg = sku(client, h, "HB 猪肉丁", cat="冻货", unit="磅")
    # 成品已经发货扣过：先入 300 再按订单发货
    client.post("/api/inv/receive", json={"idempotency_key": K(), "sku_id": fg, "warehouse_id": wh(client, h), "qty": 300, "lot_code": "HBFG"}, headers=h)
    c = next(x for x in client.get("/api/orders/customers", headers=h).json() if x["code"] == "TNT")
    so = client.post("/api/orders", json={"customer_id": c["id"], "site_id": c["sites"][0]["id"], "po_no": "HB-PORK-1", "required_date": "2026-11-10",
                                          "lines": [{"item_no": "8800001", "description": "HB Diced Pork", "qty": 300, "price": 3, "sku_id": fg, "factor": 1}]}, headers=h).json()
    o = client.get(f"/api/orders/{so['id']}", headers=h).json()
    sh = client.post(f"/api/orders/{so['id']}/ship", json={"idempotency_key": K(), "lines": [{"line_id": o["lines"][0]["id"], "qty": 300}]}, headers=h)
    assert sh.status_code == 200, sh.text
    assert onhand(client, h, fg) == 0
    # 1) 原料采购收货：65 lb/箱 × 5 箱
    rec = mk(client, h, kind="purchase_receipt", supplier="HB Restaurant Depot", doc_no="HB-RD-1109", business_date="2026-11-09",
             payment_method="公司卡", total_amount=650, reason="上周猪肉丁原料采购没录",
             lines=[{"sku_id": raw, "qty": 5, "unit": "箱", "factor": 65, "amount": 650, "inventory": "post"}])
    l0 = line(rec["plan"])
    assert l0["deltas"][0]["qty"] == 325 and l0["deltas"][0]["before"] == 0 and l0["deltas"][0]["after"] == 325
    assert l0["deltas"][0]["lot_code"]  # 食品自动编 Lot
    assert confirm(client, h, rec).status_code == 200
    assert onhand(client, h, raw) == 325
    # 2) 生产耗用：原料 −325；成品产出这一行关联了已发货的订单 → 不能再记
    use = mk(client, h, kind="production_use", business_date="2026-11-10", reason="补猪肉丁生产耗用",
             links=[{"type": "order", "id": "HB-PORK-1"}],
             lines=[{"sku_id": raw, "qty": 325, "unit": "lb", "inventory": "post"},
                    {"sku_id": fg, "qty": 300, "unit": "磅", "direction": "output", "inventory": "doc_only"}])
    assert line(use["plan"])["deltas"][0]["qty"] == -325
    assert confirm(client, h, use).status_code == 200
    assert onhand(client, h, raw) == 0 and onhand(client, h, fg) == 0
    # 3) 销售交付补录想再扣成品：关联的送货单发货时已扣 → 拦下
    sale = mk(client, h, kind="sales_delivery", business_date="2026-11-10", reason="重复测试",
              links=[{"type": "order", "id": "HB-PORK-1"}], lines=[{"sku_id": fg, "qty": 300, "inventory": "post"}])
    assert any("已经记过" in b for b in line(sale["plan"])["blockers"])
    assert confirm(client, h, sale).status_code == 409


def test_costco_mixed_receipt_and_units(client, login):
    """一张 Costco 小票：插头（维修材料，只记采购金额）、手套（生产用品，新建商品并入库）、五香粉（调料，单位换算不明确先拦下）。"""
    h = login("admin")
    spice = sku(client, h, "HB 五香粉（自用）", cat="干货", unit="箱", conv={"包": 20})
    body = {"kind": "purchase_receipt", "supplier": "HB Costco", "doc_no": "HB-COSTCO-1", "business_date": "2026-11-08", "payment_method": "personal",
            "paid_by": "老板", "total_amount": 88.5, "reason": "补 Costco 小票",
            "lines": [{"name": "插头", "item_type": "维修材料", "qty": 2, "unit_price": 9.5},
                      {"name": "HB 一次性手套", "item_type": "生产用品", "qty": 3, "unit": "盒", "amount": 45,
                       "new_sku": {"base_unit": "盒"}, "inventory": "post"},
                      {"sku_id": spice, "item_type": "调料辅料", "qty": 6, "unit": "瓶", "amount": 24.5, "inventory": "post"}]}
    p = client.post("/api/backfill/preview", json=body, headers=h).json()["plan"]
    assert p["lines"][0]["mode"] == "doc_only" and p["lines"][0]["amount"] == 19 and not p["lines"][0]["deltas"]
    assert "新建" in p["lines"][1]["sku_note"] and p["lines"][1]["deltas"][0]["qty"] == 3
    assert any("换算不明确" in b for b in p["lines"][2]["blockers"])
    assert p["lines_sum"] == 88.5
    rec = mk(client, h, **{k: v for k, v in body.items() if k not in ("kind", "business_date", "reason")}, business_date="2026-11-08", reason="补 Costco 小票")
    assert confirm(client, h, rec).status_code == 409  # 五香粉换算不明确
    # 改成 6 包（主档 1 箱 = 20 包）→ 0.3 箱
    lines = rec["lines"]
    lines[2] = {**lines[2], "unit": "包"}
    r = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": rec["version"], "changes": {"lines": lines}}, headers=h).json()
    assert r["plan"]["lines"][2]["deltas"][0]["qty"] == 0.3 and "1 箱 = 20 包" in r["plan"]["lines"][2]["convert"]
    c = confirm(client, h, r["record"])
    assert c.status_code == 200, c.text
    out = c.json()["record"]
    glove = next(s for s in M(client, h)["skus"] if s["name"] == "HB 一次性手套")
    assert glove["category"] == "生产用品" and onhand(client, h, glove["id"]) == 3 and onhand(client, h, spice) == 0.3
    assert out["lines"][1]["sku_id"] == glove["id"] and out["confirmed_by"]
    draft = mk(client, h, kind="expense", supplier="HB 自查", doc_no="HB-SELF-1", lines=[{"name": "x", "amount": 1}])
    assert not any(draft["code"] in x for x in client.get(f"/api/backfill/{draft['id']}", headers=h).json()["plan"]["dups"])  # 不把自己当重复
    # 再建一张同名商品的补录：复用，不重复建档
    p2 = client.post("/api/backfill/preview", json={**body, "doc_no": "HB-COSTCO-2", "total_amount": None,
                                                    "lines": [{"name": "HB 一次性手套", "new_sku": {"base_unit": "盒"}, "qty": 1, "inventory": "post"}]}, headers=h).json()["plan"]
    assert "复用" in p2["lines"][0]["sku_note"]


def test_unit_conversion_physical_and_conflict(client, login):
    h = login("admin")
    a = sku(client, h, "HB 鸡腿肉", cat="原料肉", unit="磅")
    b = sku(client, h, "HB 料包", conv={"包": 20})
    body = {"kind": "receiving", "business_date": "2026-11-02", "reason": "单位测试", "lines": [
        {"sku_id": a, "qty": 10, "unit": "kg", "inventory": "post", "lot_code": "HBK"},
        {"sku_id": b, "qty": 6, "unit": "包", "factor": 0.5, "inventory": "post"},  # 与主档 1 箱 = 20 包冲突
        {"sku_id": b, "qty": 2, "unit": "袋", "inventory": "post"}]}
    p = client.post("/api/backfill/preview", json=body, headers=h).json()["plan"]
    assert p["lines"][0]["deltas"][0]["qty"] == 22.0462
    assert any("不一致" in x for x in p["lines"][1]["blockers"])
    assert any("不明确" in x for x in p["lines"][2]["blockers"])


def test_idempotent_revise_and_void_with_reversal(client, login):
    h = login("admin")
    a = sku(client, h, "HB 八角（自用）")
    rec = mk(client, h, kind="receiving", supplier="HB 本地供应商", reason="漏记的调料收货",
             lines=[{"sku_id": a, "item_type": "调料辅料", "qty": 10, "inventory": "post", "lot_code": "HB-STAR"}])
    r1 = confirm(client, h, rec)
    assert r1.status_code == 200
    again = client.post(f"/api/backfill/{rec['id']}/confirm", json={"version": rec["version"]}, headers=h).json()
    assert again.get("already") and onhand(client, h, a) == 10
    cur = r1.json()["record"]
    # 修改要写原因；只记差额 −2
    lines = [{**cur["lines"][0], "qty": 8}]
    assert client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"], "changes": {"lines": lines}}, headers=h).status_code == 400
    stale = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"] - 1, "reason": "x", "changes": {"lines": lines}}, headers=h)
    assert stale.status_code == 409
    pv = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"], "reason": "实收 8 箱", "changes": {"lines": lines}, "preview_only": True}, headers=h).json()
    assert pv["plan"]["lines"][0]["deltas"][0]["qty"] == -2 and onhand(client, h, a) == 10
    r2 = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"], "reason": "实收 8 箱", "changes": {"lines": lines}}, headers=h).json()
    assert onhand(client, h, a) == 8 and r2["record"]["posted"][0]["qty"] == 8
    # 改成只补单据（其实已经在别处入账）→ 冲回 8
    cur = r2["record"]
    r3 = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"], "reason": "其实已在盘点入账",
                                                                "changes": {"lines": [{**cur["lines"][0], "inventory": "doc_only"}]}}, headers=h).json()
    assert onhand(client, h, a) == 0 and r3["record"]["posted"] == []
    cur = r3["record"]
    r4 = client.post(f"/api/backfill/{rec['id']}/revise", json={"version": cur["version"], "reason": "确实没入账，恢复",
                                                                "changes": {"lines": [{**cur["lines"][0], "inventory": "post"}]}, "acknowledged_lines": ["L001"]}, headers=h)
    assert r4.status_code == 200, r4.text
    cur = r4.json()["record"]
    assert onhand(client, h, a) == 8
    # 作废：要原因，冲回，旧流水都在
    assert client.post(f"/api/backfill/{rec['id']}/void", json={"version": cur["version"]}, headers=h).status_code == 400
    v = client.post(f"/api/backfill/{rec['id']}/void", json={"version": cur["version"], "reason": "录错了"}, headers=h).json()
    assert v["record"]["status"] == "voided" and onhand(client, h, a) == 0
    db = SessionLocal()
    try:
        ps = db.query(InventoryPosting).filter_by(source_type="backfill", source_id=rec["id"]).all()
        assert len(ps) == 5 and all(p.effective_at == "2026-11-01" for p in ps)
    finally:
        db.close()
    acts = [x["action"] for x in v["record"]["log"]]
    assert acts == ["created", "confirmed", "revised", "revised", "revised", "voided"]
    assert all(x["by_name"] for x in v["record"]["log"])


def test_duplicate_receipt_and_negative_stock(client, login):
    h = login("admin")
    a = sku(client, h, "HB 葱", cat="原料菜", unit="磅")
    body = dict(kind="purchase_receipt", supplier="HB 菜市场", doc_no="HB-VEG-7", total_amount=20,
                lines=[{"sku_id": a, "qty": 20, "amount": 20, "inventory": "post"}])
    r = confirm(client, h, mk(client, h, **body))
    assert r.status_code == 200
    dup = mk(client, h, **body)
    assert any("HB-VEG-7" in x or "补录单" in x for x in dup["plan"]["dups"])
    r = confirm(client, h, dup, acknowledged_lines=["L001"])
    assert r.status_code == 409 and "重复单据" in r.json()["detail"]["msg"]
    client.post(f"/api/backfill/{dup['id']}/void", json={"version": dup["version"], "reason": "重复"}, headers=h)
    # 耗用超过账面：拦下；写例外原因可按负数记
    use = mk(client, h, kind="production_use", reason="补耗用", lines=[{"sku_id": a, "qty": 25, "inventory": "post"}])
    assert any("库存不足" in b for b in line(use["plan"])["blockers"])
    assert confirm(client, h, use).status_code == 409
    ok = confirm(client, h, use, negative_reason="收货单还没找到，先记耗用", acknowledged_lines=["L001"])
    assert ok.status_code == 200, ok.text
    assert onhand(client, h, a) == -5


def test_mcp_backfill_role_checked_and_confirm_in_app(client, login):
    from test_agent import call, connect, enable
    for who in ("boss", "ops"):
        enable(client, login, who)
    _, ops_tok = connect(client, "ops")
    r = call(client, ops_tok, "propose_historical_backfill", {"meta": {"kind": "expense", "business_date": "2026-11-01", "reason": "x"},
                                                             "lines": [{"name": "x"}], "source_summary": "x"})
    assert r.get("isError") and "老板或系统管理员" in str(r)
    assert call(client, ops_tok, "list_historical_backfills", {}).get("isError")
    _, tok = connect(client, "boss")
    admin = login("admin")
    a = sku(client, admin, "HB 花椒（自用）")
    res = call(client, tok, "propose_historical_backfill", {
        "meta": {"kind": "receiving", "business_date": "2026-11-03", "supplier": "HB 调料行", "doc_no": "HB-MCP-1", "warehouse": "WH597",
                 "reason": "漏记的调料收货，以后生产用"},
        "lines": [{"sku": "HB 花椒（自用）", "item_type": "调料辅料", "qty": 4, "unit": "箱", "inventory": "post", "lot_code": "HB-MCP"}],
        "source_summary": "供应商送货单照片"})["structuredContent"]
    assert res["status"] == "draft" and "/backfill/" in res["confirm_url"]
    assert res["preview"]["lines"][0]["stock_change"] == ["597 主场地 0 → 4"] and onhand(client, admin, a) == 0
    rec = client.get(f"/api/backfill/{res['code']}", headers=admin).json()
    assert rec["source"].startswith("agent:") and rec["created_by"] == "老板"
    assert confirm(client, login("boss"), rec).status_code == 200 and onhand(client, admin, a) == 4
    # 已确认的：ChatGPT 只能提出修改，人确认后才按差额记
    g = call(client, tok, "get_historical_backfill", {"backfill": res["code"]})["structuredContent"]
    ch = call(client, tok, "propose_backfill_change", {"backfill": res["code"], "version": g["version"], "reason": "实收 3 箱",
                                                       "changes": {"lines": [{"id": "L001", "sku": "HB 花椒（自用）", "item_type": "调料辅料", "qty": 3, "inventory": "post", "lot_code": "HB-MCP"}]}})["structuredContent"]
    assert ch["pending_change"] and onhand(client, admin, a) == 4
    pv = client.post(f"/api/backfill/{rec['id']}/pending/preview", headers=admin).json()
    assert pv["plan"]["lines"][0]["deltas"][0]["qty"] == -1
    cur = client.get(f"/api/backfill/{rec['id']}", headers=admin).json()
    ok = client.post(f"/api/backfill/{rec['id']}/pending/apply", json={"version": cur["version"], "acknowledged_lines": ["L001"]}, headers=login("boss"))
    assert ok.status_code == 200, ok.text
    assert onhand(client, admin, a) == 3 and ok.json()["record"]["pending"] == {}
    assert "经 agent" in ok.json()["record"]["log"][-1]["reason"]
