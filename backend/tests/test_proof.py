"""证据提醒：ChatGPT 每次提交都提醒传原件；没有也照常提交，之后补，补的原件自动挂到生成的单据上。"""
from app.db import SessionLocal
from app.models import BusinessRecord, RecordType
from conftest import png
from test_agent import call, connect, enable, rpc


def test_PRF01_remind_submit_without_proof_then_backfill(client, login):
    ops = login("ops")
    enable(client, login, "ops")
    _, tok = connect(client, "ops")
    tools = {t["name"] for t in rpc(client, tok, "tools/list")["result"]["tools"]}
    assert "list_missing_proof" in tools
    res = call(client, tok, "propose_inbound", {"from_code": "TOR", "note": "TOR 第 3 车", "source_summary": "多伦多调货单（用户口述）",
                                                "proof_note": "调货单在司机那，明天补", "lines": [{"sku_code": "RM-SPICE", "qty": 1}]})["structuredContent"]
    pf = res["proof"]
    assert pf["attached"] == 0 and any("BOL" in x for x in pf["suggested"]) and "/u/" in pf["upload_url"]
    assert pf["proof_note"] == "调货单在司机那，明天补" and "不要因为缺证据" in pf["note_for_ai"]
    bid = res["confirm_url"].split("batch=")[1]
    # 没有原件也能确认
    r = client.post(f"/api/batches/{bid}/apply", json={"selected": [0]}, headers=ops).json()
    assert r["status"] == "applied" and r["result"]["transfer_id"]
    tr_id = r["result"]["transfer_id"]
    miss = call(client, tok, "list_missing_proof", {})["structuredContent"]["missing"]
    me = next(m for m in miss if m["target_id"] == bid)
    assert me["proof_note"] == "调货单在司机那，明天补" and me["target_type"] == "batch"
    # 第二天补：用批次单号生成链接 → 上传 → 自动挂到调入单
    code = client.get(f"/api/batches/{bid}", headers=ops).json()["code"]
    link = call(client, tok, "request_upload_link", {"target_type": "batch", "target_id": code, "purpose": "调货单"})["structuredContent"]["upload_url"]
    assert client.post(f"/api/upload-link/{link.split('/u/')[1]}", files={"file": ("bol.png", png(901), "image/png")}).status_code == 200
    assert any(e["purpose"] == "调货单" for e in client.get(f"/api/evidence?target_type=transfer&target_id={tr_id}", headers=ops).json())
    miss = call(client, tok, "list_missing_proof", {})["structuredContent"]["missing"]
    assert not any(m["target_id"] in (bid, tr_id) for m in miss)


def test_PRF02_install_and_purchase_changes_suggest_proof(client, login):
    _, tok = connect(client, "ops")
    db = SessionLocal()
    try:
        rt = next(t for t in db.query(RecordType) if "设备" in t.name)
        rec = db.query(BusinessRecord).filter_by(record_type_id=rt.id).first()
        rid = rec.id
    finally:
        db.close()
    res = call(client, tok, "propose_change", {"target_type": "record", "target_id": rid, "source_summary": "师傅说冷库压缩机装好了",
                                              "changes": [{"field": "status", "new_value": "进行中"}]})["structuredContent"]
    assert res["proof"]["what"] == "安装 / 设备" and any("安装完成照片" in x for x in res["proof"]["suggested"])
    po = call(client, tok, "list_purchase_orders", {})["structuredContent"]
    pos = po.get("purchase_orders") or po.get("orders") or po.get("rows") or []
    if pos:
        res = call(client, tok, "propose_change", {"target_type": "po", "target_id": pos[0]["id"], "source_summary": "供应商说周五到",
                                                  "changes": [{"field": "预计到货", "new_value": "2026-11-20"}]})["structuredContent"]
        assert res["proof"]["what"] == "采购"
    # 生产记录 / 发货 也能生成补传链接
    r = call(client, tok, "request_upload_link", {"target_type": "production", "target_id": "PB-NOPE"})
    assert r.get("isError")
