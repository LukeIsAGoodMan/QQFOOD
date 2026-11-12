"""合并商品：同一个东西被建成了两个商品（如国内采购名“三樱辣椒段”和卖给大统华的“剁椒”）。

做法（流水不改写）：
- 库存：记一笔“商品合并”过账，把被并商品每个仓 / Lot / 状态桶的余额转到保留商品的同名 Lot 上；旧流水原样保留可追溯。
- 引用：订单行、采购行、调拨行、预留、称重箱、客户 Item# 对照、商品对照、安全库存、配方改指向保留商品。
- 被并商品：停用，记 merged_into；它的品名、编码改为“…（已并入 X）”腾出来，并记成保留商品的“旧称”对照，
  以后单据上出现旧名 / 旧码照样认得出。
"""
import uuid
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .core import audit
from .models import (Box, CustomerItem, InventoryTxn, Lot, POLine, Recipe, Reservation, SafetyStock, Sku, SkuAlias, SOLine,
                     TransferLine, User)


def usage(db: Session, s: Sku) -> dict:
    """合并 / 停用前给人看的：这个商品现在被哪些东西用着。"""
    bal = defaultdict(float)
    for wh, bucket, q in db.query(InventoryTxn.warehouse_id, InventoryTxn.bucket, func.sum(InventoryTxn.qty)).filter_by(sku_id=s.id) \
            .group_by(InventoryTxn.warehouse_id, InventoryTxn.bucket):
        if abs(q or 0) > 1e-9:
            bal[bucket] += q
    return {
        "stock": {k: round(v, 3) for k, v in bal.items()},
        "has_stock": any(abs(v) > 1e-9 for v in bal.values()),
        "txns": db.query(func.count(InventoryTxn.id)).filter_by(sku_id=s.id).scalar(),
        "lots": db.query(func.count(Lot.id)).filter_by(sku_id=s.id).scalar(),
        "order_lines": db.query(func.count(SOLine.id)).filter_by(sku_id=s.id).scalar(),
        "po_lines": db.query(func.count(POLine.id)).filter_by(sku_id=s.id).scalar(),
        "transfer_lines": db.query(func.count(TransferLine.id)).filter_by(sku_id=s.id).scalar(),
        "reservations": db.query(func.count(Reservation.id)).filter_by(sku_id=s.id, active=True).scalar(),
        "aliases": db.query(func.count(SkuAlias.id)).filter_by(sku_id=s.id).scalar()
                   + db.query(func.count(CustomerItem.id)).filter_by(sku_id=s.id).scalar(),
    }


def check(db: Session, src: Sku, dst: Sku) -> list[str]:
    """不能合并的原因（空 = 可以）。每条都给出替代做法。"""
    errs = []
    if src.id == dst.id:
        errs.append("不能和自己合并")
    if src.merged_into:
        errs.append(f"「{src.name}」已经合并过了")
    if not dst.active:
        errs.append(f"「{dst.name}」已停用，请选一个在用的商品作为保留商品")
    if src.base_unit != dst.base_unit and usage(db, src)["has_stock"]:
        errs.append(f"单位不同（{src.base_unit} / {dst.base_unit}）且「{src.name}」还有库存：先把它盘点为 0、在「{dst.name}」上按 {dst.base_unit} 盘入，再合并")
    for lot in db.query(Lot).filter_by(sku_id=src.id):
        twin = db.query(Lot).filter_by(sku_id=dst.id, lot_code=lot.lot_code).first()
        if twin and twin.quality_status != lot.quality_status:
            errs.append(f"Lot {lot.lot_code} 两边质量状态不同（{lot.quality_status} / {twin.quality_status}）：先在质量页处理成一致再合并")
    return errs


def merge(db: Session, u: User, src: Sku, dst: Sku, reason: str, source: str = "manual") -> dict:
    from .routers.inventory import begin_posting, txn
    errs = check(db, src, dst)
    if errs:
        raise HTTPException(409, "；".join(errs))
    if not reason.strip():
        raise HTTPException(400, "请写合并原因（如：国内采购名和大统华品名是同一个东西）")
    before = usage(db, src)
    old_name, old_code = src.name, src.code

    # 1) Lot 对应：被并商品的每个 Lot → 保留商品同名 Lot（没有就建，带过去到期日和质量状态）
    lot_map: dict[str | None, str | None] = {None: None}
    for lot in db.query(Lot).filter_by(sku_id=src.id):
        twin = db.query(Lot).filter_by(sku_id=dst.id, lot_code=lot.lot_code).first()
        if not twin:
            twin = Lot(sku_id=dst.id, lot_code=lot.lot_code, expiry=lot.expiry, quality_status=lot.quality_status,
                       status_reason=lot.status_reason)
            db.add(twin)
            db.flush()
        lot_map[lot.id] = twin.id

    # 2) 库存余额转过去（一笔过账，旧流水不动）
    moved = 0
    rows = db.query(InventoryTxn.warehouse_id, InventoryTxn.lot_id, InventoryTxn.bucket, func.sum(InventoryTxn.qty)) \
        .filter_by(sku_id=src.id).group_by(InventoryTxn.warehouse_id, InventoryTxn.lot_id, InventoryTxn.bucket).all()
    rows = [r for r in rows if abs(r[3] or 0) > 1e-9]
    if rows:
        p = begin_posting(db, u, f"merge-{src.id}-{uuid.uuid4().hex[:8]}", "sku_merge", "sku", dst.id,
                          note=f"商品合并：{old_name} → {dst.name}（{reason}）")
        for wh, lot_id, bucket, q in rows:
            txn(db, p, src.id, wh, bucket, -q, lot_id)
            txn(db, p, dst.id, wh, bucket, q, lot_map.get(lot_id))
            moved += 1

    # 3) 引用改指向保留商品
    for model in (SOLine, POLine):
        db.query(model).filter_by(sku_id=src.id).update({"sku_id": dst.id})
    for model in (TransferLine, Reservation, Box):
        for x in db.query(model).filter_by(sku_id=src.id):
            x.sku_id, x.lot_id = dst.id, lot_map.get(x.lot_id, x.lot_id)
    db.query(CustomerItem).filter_by(sku_id=src.id).update({"sku_id": dst.id})
    db.query(SkuAlias).filter_by(sku_id=src.id).update({"sku_id": dst.id})
    for ss in db.query(SafetyStock).filter_by(sku_id=src.id):
        if db.query(SafetyStock).filter_by(sku_id=dst.id, warehouse_id=ss.warehouse_id).first():
            db.delete(ss)  # 保留商品已设的安全库存为准
        else:
            ss.sku_id = dst.id
    rcp = db.query(Recipe).filter_by(output_sku_id=src.id).first()
    if rcp:
        if db.query(Recipe).filter_by(output_sku_id=dst.id).first():
            db.delete(rcp)
        else:
            rcp.output_sku_id = dst.id
    for r in db.query(Recipe):
        if any(l.get("sku_id") == src.id for l in r.lines or []):
            r.lines = [{**l, "sku_id": dst.id} if l.get("sku_id") == src.id else l for l in r.lines]
    for k in ("purchase_pack", "ship_pack", "barcode"):
        if getattr(dst, k) in (None, "") and getattr(src, k) not in (None, ""):
            setattr(dst, k, getattr(src, k))
    if dst.category == "未分类" and src.category != "未分类":
        dst.category = src.category
    dst.conversions = {**(src.conversions or {}), **(dst.conversions or {})}

    # 4) 被并商品停用；旧名 / 旧码腾出来，记成保留商品的“旧称”
    db.add(SkuAlias(sku_id=dst.id, party_kind="other", party="合并前的商品", code=old_code, name=old_name, unit=src.base_unit,
                    factor=1.0, note=f"由 {u.display_name} 合并：{reason}"[:300]))
    src.active, src.merged_into = False, dst.id
    src.name = f"{old_name}（已并入 {dst.name}）"[:200]
    src.code = f"{old_code}~M{uuid.uuid4().hex[:4].upper()}"[:64]
    src.barcode = None
    audit(db, u, "sku.merged", "sku", dst.id, from_sku=src.id, from_name=old_name, from_code=old_code, reason=reason,
          moved_balances=moved, before=before, source=source)
    return {"into": {"id": dst.id, "name": dst.name}, "from": {"id": src.id, "name": old_name, "code": old_code},
            "moved_balances": moved, "repointed": {k: before[k] for k in ("order_lines", "po_lines", "transfer_lines", "reservations", "aliases")}}
