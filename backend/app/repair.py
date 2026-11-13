"""历史补录送货的库存修复（只能由老板 / 系统管理员在系统里手动执行，先预览再确认）。

问题：期初 / 盘点时录的是“已经扣掉 11/4 送货后的净结存”（如卤水料包 81 箱、剁椒 89 箱），
之后又把 11/4 的送货补录成发货单，发货时系统又扣了一次（变成 78 / 88）。

做法（不删除、不改写任何记录）：
- 预览：列出所有扣过库存、还没冲销的送货单；标出“疑似重复扣减”的——业务日期早于某次盘点，但录入时间晚于那次盘点
  （盘点数已经包含了这次送货的影响）。给出每个商品现在的结存、修复后的结存。
- 执行：先备份数据库，再给选中的送货单记一笔冲销流水（把多扣的加回来），送货单标“补录时误扣，已冲销”，留审计。
  同一张送货单只能冲销一次（幂等）。送货单本身、PO、签收凭证都不动。
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .core import audit
from .models import InventoryPosting, InventoryTxn, Lot, SalesOrder, Shipment, Sku, User, Warehouse

CONFIRM = "确认修复"


def _day(dt) -> str:
    return dt.date().isoformat() if dt else ""


def _aware(t):
    return t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)


def _on_hand(db, sku_id, wh_id) -> float:
    return float(db.query(func.coalesce(func.sum(InventoryTxn.qty), 0)).filter_by(sku_id=sku_id, warehouse_id=wh_id, bucket="on_hand").scalar() or 0)


def preview(db: Session) -> dict:
    skus = {s.id: s for s in db.query(Sku)}
    whs = {w.id: w for w in db.query(Warehouse)}
    counts = [p for p in db.query(InventoryPosting).filter(InventoryPosting.kind == "count", InventoryPosting.reversed_by_posting_id.is_(None))]
    count_txns: dict[str, list] = {}
    for p in counts:
        for t in db.query(InventoryTxn).filter_by(posting_id=p.id, bucket="on_hand"):
            count_txns.setdefault(p.id, []).append((t.sku_id, t.warehouse_id))
    out = []
    for sh in db.query(Shipment).filter(Shipment.inventory_effect == "issued").order_by(Shipment.shipped_at):
        p = db.get(InventoryPosting, sh.posting_id) if sh.posting_id else None
        if not p or p.reversed_by_posting_id:
            continue
        txns = db.query(InventoryTxn).filter_by(posting_id=p.id).all()
        if not txns:
            continue
        so = db.get(SalesOrder, sh.order_id)
        biz = sh.shipped_on or _day(sh.shipped_at)
        per: dict[tuple, float] = {}
        for t in txns:
            per[(t.sku_id, t.warehouse_id)] = per.get((t.sku_id, t.warehouse_id), 0) + t.qty
        lines, reasons = [], []
        for (sid, wid), q in per.items():
            hit = [c for c in counts if (sid, wid) in count_txns.get(c.id, [])
                   and _aware(c.created_at) < _aware(p.created_at) and (c.effective_at or _day(c.created_at)) >= biz]
            for c in hit:
                reasons.append(f"「{skus[sid].name}」在 {c.effective_at or _day(c.created_at)} 盘点 / 期初（{c.note or c.kind}）——"
                               f"这次送货业务日期 {biz} 在它之前，却是盘点之后（{_day(_aware(p.created_at))}）才录入并扣库存")
            now = _on_hand(db, sid, wid)
            lines.append({"sku": skus[sid].name, "unit": skus[sid].base_unit, "warehouse": whs[wid].name, "deducted": round(-q, 4),
                          "on_hand_now": round(now, 4), "on_hand_after_fix": round(now - q, 4), "matched_count": bool(hit)})
        out.append({"shipment_id": sh.id, "code": sh.code, "delivery_no": sh.delivery_no, "order": so.code if so else "", "po_no": so.po_no if so else "",
                    "business_date": biz, "recorded_at": p.created_at, "status": sh.status, "lines": lines,
                    "suspect": any(l["matched_count"] for l in lines), "reasons": reasons})
    return {"shipments": out, "suspects": sum(1 for x in out if x["suspect"]), "confirm_phrase": CONFIRM,
            "note": "只列出扣过库存、还没冲销的送货单。疑似重复的才建议修复；不确定的先核对实物再决定。"}


def apply(db: Session, u: User, shipment_ids: list[str], reason: str, confirm: str) -> dict:
    from .routers.admin import backup_db
    from .routers.inventory import Dup, begin_posting, txn
    if u.role not in ("boss", "sys_admin"):
        raise HTTPException(403, "只有老板或系统管理员可以执行库存修复")
    if confirm.strip() != CONFIRM:
        raise HTTPException(400, f"请输入「{CONFIRM}」以确认")
    if not reason.strip():
        raise HTTPException(400, "请写修复原因（如：11/5 期初 81 箱已是扣掉 11/4 送货后的净结存，补录送货不应再扣）")
    if not shipment_ids:
        raise HTTPException(400, "没有选择送货单")
    backup = backup_db()
    done, skipped = [], []
    for sid in shipment_ids:
        sh = db.get(Shipment, sid)
        p = db.get(InventoryPosting, sh.posting_id) if sh and sh.posting_id else None
        if not sh or not p or sh.inventory_effect != "issued" or p.reversed_by_posting_id:
            skipped.append({"id": sid, "why": "不存在、没扣过库存或已冲销"})
            continue
        try:
            r = begin_posting(db, u, f"histfix:{sh.id}", "reversal", "posting", p.id, f"历史补录误扣冲销 {sh.code}：{reason}")
        except Dup:
            skipped.append({"id": sid, "why": "已修复过"})
            continue
        r.effective_at = sh.shipped_on or _day(sh.shipped_at)
        moved = []
        for t in db.query(InventoryTxn).filter_by(posting_id=p.id):
            txn(db, r, t.sku_id, t.warehouse_id, t.bucket, -t.qty, t.lot_id)
            moved.append({"sku": t.sku_id, "lot": t.lot_id, "qty": -t.qty})
        r.reverses_posting_id, p.reversed_by_posting_id = p.id, r.id
        sh.inventory_effect = "reversed_historical"
        audit(db, u, "inventory.historical_fix", "shipment", sh.id, posting=p.id, reversal=r.id, reason=reason, restored=moved, backup=backup)
        done.append(sh.code)
    db.commit()
    return {"fixed": done, "skipped": skipped, "backup": backup}
