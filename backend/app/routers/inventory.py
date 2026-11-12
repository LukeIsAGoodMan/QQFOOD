"""Inventory ledger, transfers, quarantine/release, production batches, trace (PRD §6.1, §6.2).

Rules:
- Every quantity change is an InventoryTxn inside an InventoryPosting guarded by a unique idempotency key.
- Corrections are reversals, never edits.
- Available = on_hand of released lots − active reservations on released stock. Quarantined/pending lots and the
  `unverified` bucket (未核数) are shown separately and never count as available.
- Transfer request does not move stock. Ship: source on_hand −, destination in_transit +. Receive: in_transit −,
  destination on_hand +. Short/damaged stays in transit until written off.
"""
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..categories import SKU_CATEGORIES, normalize as norm_category
from ..core import audit, current_user, get_or_404, notify, require
from ..db import get_db
from ..models import (InventoryPosting, InventoryTxn, Lot, ProductionBatch, QualityEvent, Reservation, SafetyStock,
                      Sku, Task, Transfer, TransferLine, User, Warehouse)

router = APIRouter(prefix="/api/inv", tags=["inventory"])


# ---------- helpers ----------
class Dup(Exception):
    def __init__(self, posting):
        self.posting = posting


def begin_posting(db: Session, u: User, key: str, kind: str, source_type=None, source_id=None, note="") -> InventoryPosting:
    if not key or len(key) < 8:
        raise HTTPException(400, "缺少幂等键")
    existing = db.query(InventoryPosting).filter_by(idempotency_key=key).first()
    if existing:
        raise Dup(existing)
    p = InventoryPosting(idempotency_key=key, kind=kind, source_type=source_type, source_id=source_id, note=note,
                         created_by=u.id)
    db.add(p)
    try:
        db.flush()
    except IntegrityError:  # concurrent duplicate
        db.rollback()
        raise Dup(db.query(InventoryPosting).filter_by(idempotency_key=key).first())
    return p


def txn(db, posting, sku_id, wh_id, bucket, qty, lot_id=None):
    if qty == 0:
        return
    db.add(InventoryTxn(posting_id=posting.id, sku_id=sku_id, lot_id=lot_id, warehouse_id=wh_id, bucket=bucket, qty=qty))


def balance(db: Session, sku_id: str, wh_id: str, bucket: str, lot_id: str | None) -> float:
    q = db.query(func.coalesce(func.sum(InventoryTxn.qty), 0)).filter_by(sku_id=sku_id, warehouse_id=wh_id, bucket=bucket)
    q = q.filter(InventoryTxn.lot_id.is_(None)) if lot_id is None else q.filter_by(lot_id=lot_id)
    return float(q.scalar() or 0)


def assert_usable(db: Session, lot_id: str | None, action: str):
    if not lot_id:
        return
    lot = db.get(Lot, lot_id)
    if lot and lot.quality_status != "released":
        label = {"quarantine": "已隔离", "pending": "待放行"}[lot.quality_status]
        raise HTTPException(409, f"批次 {lot.lot_code} {label}，禁止{action}")


def reserved_on(db, sku_id, wh_id, lot_id) -> float:
    q = db.query(func.coalesce(func.sum(Reservation.qty), 0)).filter_by(sku_id=sku_id, warehouse_id=wh_id, active=True)
    q = q.filter(Reservation.lot_id.is_(None)) if lot_id is None else q.filter_by(lot_id=lot_id)
    return float(q.scalar() or 0)


def get_or_create_lot(db: Session, sku_id: str, lot_code: str | None, expiry: str | None = None,
                      status: str = "released") -> Lot | None:
    if not lot_code:
        return None
    lot = db.query(Lot).filter_by(sku_id=sku_id, lot_code=lot_code.strip()).first()
    if not lot:
        lot = Lot(sku_id=sku_id, lot_code=lot_code.strip(), expiry=expiry, quality_status=status)
        db.add(lot)
        db.flush()
    return lot


def posting_out(db: Session, p: InventoryPosting, duplicate=False):
    rows = db.query(InventoryTxn).filter_by(posting_id=p.id).all()
    return {"posting_id": p.id, "kind": p.kind, "duplicate": duplicate,
            "txns": [{"sku_id": t.sku_id, "lot_id": t.lot_id, "warehouse_id": t.warehouse_id, "bucket": t.bucket,
                      "qty": t.qty} for t in rows]}


# ---------- master data ----------
@router.get("/master")
def master(u: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import Customer, CustomerItem
    t = db.query(Customer).filter_by(code="TNT").first()
    tnt = {}
    for ci in (db.query(CustomerItem).filter(CustomerItem.customer_id == t.id, CustomerItem.item_no != "").order_by(CustomerItem.updated_at) if t else []):
        tnt.setdefault(ci.sku_id, ci.item_no)
    return {
        "warehouses": [{"id": w.id, "code": w.code, "name": w.name, "kind": w.kind, "tracked": w.tracked, "active": w.active}
                       for w in db.query(Warehouse).order_by(Warehouse.tracked.desc(), Warehouse.name)],
        "skus": [{"id": s.id, "code": s.code, "name": s.name, "category": s.category, "base_unit": s.base_unit,
                  "conversions": s.conversions, "barcode": s.barcode, "tnt_item_no": tnt.get(s.id, "")} for s in db.query(Sku).order_by(Sku.name)],
        "lots": [{"id": l.id, "sku_id": l.sku_id, "lot_code": l.lot_code, "expiry": l.expiry,
                  "quality_status": l.quality_status} for l in db.query(Lot).order_by(Lot.lot_code)],
    }


class SkuIn(BaseModel):
    code: str = ""  # 可空：以品名为主，系统自动编号
    name: str
    category: str = "未分类"
    base_unit: str = "箱"
    conversions: dict = {}
    barcode: str | None = None


@router.post("/skus")
def create_sku(body: SkuIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.master")
    from .batches import _norm, next_sku_code
    if any(_norm(x.name) == _norm(body.name) for x in db.query(Sku)):
        raise HTTPException(409, f"已有同名商品「{body.name}」")
    cat = norm_category(body.category, body.name)
    if cat is None:
        raise HTTPException(400, f"分类需为 {'/'.join(SKU_CATEGORIES)}")
    body.category = cat
    body.code = body.code.strip().upper() or next_sku_code(db)
    if db.query(Sku).filter_by(code=body.code).first():
        raise HTTPException(409, "SKU 编码已存在")
    s = Sku(**body.model_dump())
    db.add(s)
    audit(db, u, "sku.created", "sku", s.id, code=body.code)
    db.commit()
    return {"id": s.id}


class SkuPatch(BaseModel):
    category: str | None = None
    name: str | None = None


@router.patch("/skus/{sid}")
def patch_sku(sid: str, body: SkuPatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """改分类 / 品名（库存页“补分类”、商品页）。改名不影响库存和历史单据。"""
    require(u, "inventory.master")
    from .batches import _norm
    s = get_or_404(db, Sku, sid, "商品不存在")
    before = {"category": s.category, "name": s.name}
    if body.category is not None:
        cat = norm_category(body.category, s.name)
        if cat is None:
            raise HTTPException(400, f"分类需为 {'/'.join(SKU_CATEGORIES)}")
        s.category = cat
    if body.name is not None and body.name.strip() != s.name:
        n = body.name.strip()
        if not n:
            raise HTTPException(400, "品名不能为空")
        if any(_norm(x.name) == _norm(n) for x in db.query(Sku).filter(Sku.id != s.id)):
            raise HTTPException(409, f"已有同名商品「{n}」")
        s.name = n
    audit(db, u, "sku.updated", "sku", s.id, before=before, after={"category": s.category, "name": s.name})
    db.commit()
    return {"id": s.id, "category": s.category, "name": s.name}


class SafetyIn(BaseModel):
    sku_id: str
    warehouse_id: str
    qty: float


@router.put("/safety-stock")
def set_safety(body: SafetyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.master")
    ss = db.query(SafetyStock).filter_by(sku_id=body.sku_id, warehouse_id=body.warehouse_id).first()
    if ss:
        ss.qty = body.qty
    else:
        db.add(SafetyStock(**body.model_dump()))
    audit(db, u, "safety_stock.set", "sku", body.sku_id, warehouse=body.warehouse_id, qty=body.qty)
    db.commit()
    return {"ok": True}


class WarehouseIn(BaseModel):
    code: str
    name: str
    kind: str = "external"
    tracked: bool = True
    active: bool = True


@router.post("/warehouses")
def create_warehouse(body: WarehouseIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """新增仓库或来源地。tracked=False 表示不在本系统管账（例如多伦多、温哥华），只用于调入来源。"""
    require(u, "inventory.master")
    if body.kind not in ("main", "external", "company"):
        raise HTTPException(400, "类型无效")
    if db.query(Warehouse).filter_by(code=body.code.strip().upper()).first():
        raise HTTPException(409, "编码已存在")
    w = Warehouse(code=body.code.strip().upper(), name=body.name.strip(), kind=body.kind, tracked=body.tracked, active=body.active)
    db.add(w)
    audit(db, u, "warehouse.created", "warehouse", w.id, code=w.code, tracked=w.tracked)
    db.commit()
    return {"id": w.id}


@router.patch("/warehouses/{wid}")
def update_warehouse(wid: str, body: WarehouseIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.master")
    w = get_or_404(db, Warehouse, wid)
    if w.tracked and not body.tracked:
        has = db.query(func.count(InventoryTxn.id)).filter_by(warehouse_id=wid, bucket="on_hand").scalar()
        if has:
            raise HTTPException(409, "该仓库已有库存流水，不能改为不管账")
    w.name, w.kind, w.tracked, w.active = body.name.strip(), body.kind, body.tracked, body.active
    audit(db, u, "warehouse.updated", "warehouse", w.id, tracked=w.tracked, active=w.active)
    db.commit()
    return {"ok": True}


@router.get("/scan/{code}")
def scan(code: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """扫码解析：支持 SKU 编码/条码、Lot 码（LOT:<sku>:<lot> 或 lot_code）、调拨单号。"""
    code = code.strip()
    if code.startswith("LOT:"):
        _, sku_code, lot_code = (code.split(":", 2) + ["", ""])[:3]
        sku = db.query(Sku).filter_by(code=sku_code).first()
        lot = db.query(Lot).filter_by(sku_id=sku.id, lot_code=lot_code).first() if sku else None
        if lot:
            return {"type": "lot", "lot_id": lot.id, "sku_id": lot.sku_id, "lot_code": lot.lot_code,
                    "quality_status": lot.quality_status}
    sku = db.query(Sku).filter((Sku.code == code) | (Sku.barcode == code) | (Sku.code == code.upper())).first()
    if sku:
        return {"type": "sku", "sku_id": sku.id, "code": sku.code, "name": sku.name}
    from .aliases import lookup
    exact = [h for h in lookup(db, code) if any(v["exact"] for v in h["via"])]
    if len(exact) == 1:  # 外库 / 大统华 / 供应商的编码 → 我们的 SKU
        h = exact[0]
        v = next(v for v in h["via"] if v["exact"])
        return {"type": "sku", "sku_id": h["sku"]["id"], "code": h["sku"]["code"], "name": h["sku"]["name"], "via": f"{v['party']} {v['code']}"}
    lots = db.query(Lot).filter_by(lot_code=code).all()
    if len(lots) == 1:
        l = lots[0]
        return {"type": "lot", "lot_id": l.id, "sku_id": l.sku_id, "lot_code": l.lot_code, "quality_status": l.quality_status}
    if len(lots) > 1:
        return {"type": "ambiguous", "candidates": [{"lot_id": l.id, "sku_id": l.sku_id} for l in lots]}
    tr = db.query(Transfer).filter_by(code=code).first()
    if tr:
        return {"type": "transfer", "transfer_id": tr.id}
    from ..models import Box, PurchaseOrder, SalesOrder
    po = db.query(PurchaseOrder).filter_by(code=code).first()
    if po:
        return {"type": "po", "po_id": po.id, "code": po.code}
    bx = db.query(Box).filter_by(serial=code.upper()).first()
    if bx:
        return {"type": "box", "box_id": bx.id, "serial": bx.serial, "sku_id": bx.sku_id, "weight": bx.weight, "status": bx.status}
    so = db.query(SalesOrder).filter((SalesOrder.code == code.upper()) | (SalesOrder.po_no == code)).first()
    if so:
        return {"type": "so", "order_id": so.id, "code": so.code}
    raise HTTPException(404, f"未识别的编码：{code}")


# ---------- stock view ----------
@router.get("/stock")
def stock(warehouse_id: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(InventoryTxn.sku_id, InventoryTxn.warehouse_id, InventoryTxn.lot_id, InventoryTxn.bucket,
                 func.sum(InventoryTxn.qty)).group_by(InventoryTxn.sku_id, InventoryTxn.warehouse_id,
                                                      InventoryTxn.lot_id, InventoryTxn.bucket)
    if warehouse_id:
        q = q.filter(InventoryTxn.warehouse_id == warehouse_id)
    lots = {l.id: l for l in db.query(Lot).all()}
    skus = {s.id: s for s in db.query(Sku).all()}
    whs = {w.id: w for w in db.query(Warehouse).all()}
    agg = defaultdict(lambda: {"on_hand": 0.0, "blocked": 0.0, "in_transit": 0.0, "unverified": 0.0, "reserved": 0.0,
                               "lots": defaultdict(lambda: {"on_hand": 0.0, "in_transit": 0.0, "unverified": 0.0})})
    for sku_id, wh_id, lot_id, bucket, qty in q:
        a = agg[(sku_id, wh_id)]
        lot = lots.get(lot_id)
        status = lot.quality_status if lot else "released"
        if bucket == "on_hand":
            a["on_hand"] += qty
            if status != "released":
                a["blocked"] += qty
        elif bucket == "in_transit":
            a["in_transit"] += qty
        else:
            a["unverified"] += qty
        a["lots"][lot_id][bucket] += qty
    for r in db.query(Reservation).filter_by(active=True):
        if (r.sku_id, r.warehouse_id) in agg or not warehouse_id or r.warehouse_id == warehouse_id:
            lot = lots.get(r.lot_id)
            if not lot or lot.quality_status == "released":  # 隔离批次已计入不可用，避免重复扣减
                agg[(r.sku_id, r.warehouse_id)]["reserved"] += r.qty
    safety = {(s.sku_id, s.warehouse_id): s.qty for s in db.query(SafetyStock).all()}
    out = []
    for (sku_id, wh_id), a in agg.items():
        sku, wh = skus[sku_id], whs[wh_id]
        available = a["on_hand"] - a["blocked"] - a["reserved"]
        ss = safety.get((sku_id, wh_id))
        out.append({
            "sku_id": sku_id, "sku_code": sku.code, "sku_name": sku.name, "sku_category": sku.category, "unit": sku.base_unit,
            "conversions": sku.conversions, "warehouse_id": wh_id, "warehouse": wh.name, "warehouse_kind": wh.kind,
            "on_hand": round(a["on_hand"], 3), "blocked": round(a["blocked"], 3), "reserved": round(a["reserved"], 3),
            "available": round(available, 3), "in_transit": round(a["in_transit"], 3),
            "unverified": round(a["unverified"], 3), "safety_stock": ss,
            "below_safety": ss is not None and available < ss,
            "lots": [{"lot_id": lid, "lot_code": lots[lid].lot_code if lid in lots else "(无批次)",
                      "quality_status": lots[lid].quality_status if lid in lots else "released",
                      "expiry": lots[lid].expiry if lid in lots else None, **{k: round(v, 3) for k, v in b.items()}}
                     for lid, b in a["lots"].items() if any(abs(v) > 1e-9 for v in b.values())],
        })
    # SKUs with safety stock but zero ledger rows still need to show up (缺货)
    for (sku_id, wh_id), ss in safety.items():
        if (sku_id, wh_id) not in agg and (not warehouse_id or wh_id == warehouse_id):
            sku, wh = skus[sku_id], whs[wh_id]
            out.append({"sku_id": sku_id, "sku_code": sku.code, "sku_name": sku.name, "sku_category": sku.category, "unit": sku.base_unit,
                        "conversions": sku.conversions, "warehouse_id": wh_id, "warehouse": wh.name,
                        "warehouse_kind": wh.kind, "on_hand": 0, "blocked": 0, "reserved": 0, "available": 0,
                        "in_transit": 0, "unverified": 0, "safety_stock": ss, "below_safety": ss > 0, "lots": []})
    out.sort(key=lambda r: (not r["below_safety"], r["sku_name"], r["warehouse"]))
    return {"rows": out, "computed_at": datetime.now(timezone.utc)}


@router.get("/postings")
def postings(sku_id: str | None = None, lot_id: str | None = None, limit: int = 100,
             u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(InventoryPosting)
    if sku_id or lot_id:
        sub = db.query(InventoryTxn.posting_id)
        if sku_id:
            sub = sub.filter_by(sku_id=sku_id)
        if lot_id:
            sub = sub.filter_by(lot_id=lot_id)
        q = q.filter(InventoryPosting.id.in_(sub))
    names = {x.id: x.display_name for x in db.query(User).all()}
    out = []
    for p in q.order_by(InventoryPosting.created_at.desc()).limit(limit):
        d = posting_out(db, p)
        d.update({"source_type": p.source_type, "source_id": p.source_id, "note": p.note, "by": names.get(p.created_by),
                  "at": p.created_at, "reverses": p.reverses_posting_id, "reversed_by": p.reversed_by_posting_id})
        out.append(d)
    return out


# ---------- receive / issue / adjust / verify-count ----------
class ReceiveIn(BaseModel):
    idempotency_key: str
    sku_id: str
    warehouse_id: str
    qty: float
    unit: str | None = None
    lot_code: str | None = None
    expiry: str | None = None
    counted: bool = True  # False = 估计/未核数 → unverified bucket
    source_type: str | None = None
    source_id: str | None = None
    note: str = ""


def to_base(db: Session, sku_id: str, qty: float, unit: str | None) -> float:
    sku = get_or_404(db, Sku, sku_id, "SKU 不存在")
    if not unit or unit == sku.base_unit:
        return qty
    per = (sku.conversions or {}).get(unit)
    if not per:
        raise HTTPException(400, f"商品主档未定义 {unit} 与 {sku.base_unit} 的换算，不能默认相等")
    return qty / float(per)


@router.post("/receive")
def receive(body: ReceiveIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.post")
    if body.qty <= 0:
        raise HTTPException(400, "数量需大于 0")
    try:
        p = begin_posting(db, u, body.idempotency_key, "receive", body.source_type, body.source_id, body.note)
    except Dup as d:
        return posting_out(db, d.posting, True)
    qty = to_base(db, body.sku_id, body.qty, body.unit)
    lot = get_or_create_lot(db, body.sku_id, body.lot_code, body.expiry)
    txn(db, p, body.sku_id, body.warehouse_id, "on_hand" if body.counted else "unverified", qty, lot.id if lot else None)
    audit(db, u, "inventory.receive", "posting", p.id, qty=qty, counted=body.counted)
    db.commit()
    return posting_out(db, p)


class IssueIn(BaseModel):
    idempotency_key: str
    sku_id: str
    warehouse_id: str
    lot_id: str | None = None
    qty: float
    unit: str | None = None
    order_ref: str = ""
    customer: str = ""
    reason: str = "出库"
    note: str = ""


@router.post("/issue")
def issue(body: IssueIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.post")
    if body.qty <= 0:
        raise HTTPException(400, "数量需大于 0")
    assert_usable(db, body.lot_id, "出库")
    try:
        p = begin_posting(db, u, body.idempotency_key, "issue", "order" if body.order_ref else "manual",
                          body.order_ref or None, f"{body.customer} {body.note}".strip())
    except Dup as d:
        return posting_out(db, d.posting, True)
    qty = to_base(db, body.sku_id, body.qty, body.unit)
    have = balance(db, body.sku_id, body.warehouse_id, "on_hand", body.lot_id)
    if qty > have + 1e-9:
        db.rollback()
        raise HTTPException(409, f"账面库存不足：现有 {have}")
    txn(db, p, body.sku_id, body.warehouse_id, "on_hand", -qty, body.lot_id)
    # consume matching reservation
    if body.order_ref:
        for r in db.query(Reservation).filter_by(order_ref=body.order_ref, sku_id=body.sku_id, active=True):
            r.active = False
    audit(db, u, "inventory.issue", "posting", p.id, qty=qty, order=body.order_ref)
    db.commit()
    return posting_out(db, p)


class CountIn(BaseModel):
    idempotency_key: str
    sku_id: str
    warehouse_id: str
    lot_id: str | None = None
    counted_qty: float
    reason: str


@router.post("/count")
def count(body: CountIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """盘点/核数：未核数量清零，实盘量进入 on_hand，差异形成调整流水。"""
    require(u, "inventory.post")
    if not body.reason.strip():
        raise HTTPException(400, "盘点调整需填写原因")
    try:
        p = begin_posting(db, u, body.idempotency_key, "count", note=body.reason)
    except Dup as d:
        return posting_out(db, d.posting, True)
    on_hand = balance(db, body.sku_id, body.warehouse_id, "on_hand", body.lot_id)
    unv = balance(db, body.sku_id, body.warehouse_id, "unverified", body.lot_id)
    txn(db, p, body.sku_id, body.warehouse_id, "unverified", -unv, body.lot_id)
    txn(db, p, body.sku_id, body.warehouse_id, "on_hand", body.counted_qty - on_hand, body.lot_id)
    audit(db, u, "inventory.count", "posting", p.id, before_on_hand=on_hand, before_unverified=unv,
          counted=body.counted_qty, reason=body.reason)
    db.commit()
    return posting_out(db, p)


class ReverseIn(BaseModel):
    idempotency_key: str
    reason: str


@router.post("/postings/{pid}/reverse")
def reverse(pid: str, body: ReverseIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """更正已过账记录：生成冲销流水，不覆盖历史。"""
    require(u, "inventory.post")
    orig = get_or_404(db, InventoryPosting, pid)
    if orig.reversed_by_posting_id:
        raise HTTPException(409, "该过账已被冲销")
    if orig.kind in ("transfer_ship", "transfer_receive", "production"):
        raise HTTPException(409, "调拨与生产过账请通过对应单据的差异/更正流程处理")
    if not body.reason.strip():
        raise HTTPException(400, "冲销需填写原因")
    try:
        p = begin_posting(db, u, body.idempotency_key, "reversal", "posting", orig.id, body.reason)
    except Dup as d:
        return posting_out(db, d.posting, True)
    for t in db.query(InventoryTxn).filter_by(posting_id=orig.id):
        txn(db, p, t.sku_id, t.warehouse_id, t.bucket, -t.qty, t.lot_id)
    p.reverses_posting_id = orig.id
    orig.reversed_by_posting_id = p.id
    audit(db, u, "inventory.reverse", "posting", orig.id, reversal=p.id, reason=body.reason)
    db.commit()
    return posting_out(db, p)


# ---------- reservations ----------
class ReserveIn(BaseModel):
    sku_id: str
    warehouse_id: str
    lot_id: str | None = None
    qty: float
    order_ref: str
    customer: str = ""


@router.get("/reservations")
def reservations(u: User = Depends(current_user), db: Session = Depends(get_db)):
    lots = {l.id: l for l in db.query(Lot).all()}
    return [{"id": r.id, "sku_id": r.sku_id, "warehouse_id": r.warehouse_id, "lot_id": r.lot_id, "qty": r.qty,
             "order_ref": r.order_ref, "customer": r.customer,
             "affected": bool(r.lot_id and lots.get(r.lot_id) and lots[r.lot_id].quality_status != "released")}
            for r in db.query(Reservation).filter_by(active=True)]


@router.post("/reservations")
def reserve(body: ReserveIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.reserve")
    assert_usable(db, body.lot_id, "分配")
    r = Reservation(**body.model_dump(), created_by=u.id)
    db.add(r)
    audit(db, u, "inventory.reserve", "reservation", r.id, qty=body.qty, order=body.order_ref)
    db.commit()
    return {"id": r.id}


@router.post("/reservations/{rid}/release")
def release_reservation(rid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.reserve")
    r = get_or_404(db, Reservation, rid)
    r.active = False
    audit(db, u, "inventory.unreserve", "reservation", r.id)
    db.commit()
    return {"ok": True}


# ---------- transfers ----------
def transfer_out(db: Session, t: Transfer):
    whobj = {w.id: w for w in db.query(Warehouse).all()}
    whs = {k: w.name for k, w in whobj.items()}
    skus = {s.id: s for s in db.query(Sku).all()}
    lots = {l.id: l for l in db.query(Lot).all()}
    lines = db.query(TransferLine).filter_by(transfer_id=t.id).all()
    src = whobj.get(t.from_wh)
    return {"id": t.id, "code": t.code, "from_wh": t.from_wh, "to_wh": t.to_wh, "from_name": whs.get(t.from_wh),
            "inbound": bool(src and not src.tracked),  # 调入：来源地不在本系统管账
            "to_name": whs.get(t.to_wh), "status": t.status, "expected_date": t.expected_date, "note": t.note,
            "version": t.version, "created_at": t.created_at,
            "lines": [{"id": l.id, "sku_id": l.sku_id, "sku_code": skus[l.sku_id].code, "sku_name": skus[l.sku_id].name,
                       "unit": skus[l.sku_id].base_unit, "lot_id": l.lot_id,
                       "lot_code": lots[l.lot_id].lot_code if l.lot_id in lots else None,
                       "qty_requested": l.qty_requested, "qty_shipped": l.qty_shipped, "qty_received": l.qty_received,
                       "qty_damaged": l.qty_damaged, "qty_written_off": l.qty_written_off,
                       "in_transit": round(l.qty_shipped - l.qty_received - l.qty_written_off, 3)} for l in lines]}


@router.get("/transfers")
def list_transfers(status: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Transfer)
    if status:
        q = q.filter(Transfer.status.in_(status.split(",")))
    return [transfer_out(db, t) for t in q.order_by(Transfer.created_at.desc()).limit(200)]


@router.get("/transfers/{tid}")
def get_transfer(tid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return transfer_out(db, get_or_404(db, Transfer, tid))


class TLineIn(BaseModel):
    sku_id: str
    lot_id: str | None = None
    lot_code: str | None = None  # 调入时批次常常是对方新给的，直接手填
    expiry: str | None = None
    qty: float


class TransferIn(BaseModel):
    from_wh: str
    to_wh: str
    expected_date: str | None = None
    note: str = ""
    lines: list[TLineIn]
    shipped_now: bool = False  # 对方已发出：按单据数量直接记为在途（常用于多伦多/温哥华调入）
    shortfall_reason: str = ""  # 来源仓账面不足时按单据补记的原因（例外）


@router.post("/transfers")
def create_transfer(body: TransferIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.transfer")
    if body.from_wh == body.to_wh:
        raise HTTPException(400, "来源与目的仓不能相同")
    if not body.lines or any(l.qty <= 0 for l in body.lines):
        raise HTTPException(400, "至少一行且数量大于 0")
    n = db.query(Transfer).count() + 1
    t = Transfer(code=f"TR-{datetime.now().strftime('%y%m%d')}-{n:03d}", from_wh=body.from_wh, to_wh=body.to_wh,
                 expected_date=body.expected_date, note=body.note, created_by=u.id)
    db.add(t)
    db.flush()
    created = []
    for l in body.lines:
        lot_id = l.lot_id
        if not lot_id and (l.lot_code or "").strip():
            lot_id = get_or_create_lot(db, l.sku_id, l.lot_code, l.expiry).id
        tl = TransferLine(transfer_id=t.id, sku_id=l.sku_id, lot_id=lot_id, qty_requested=l.qty)
        db.add(tl)
        created.append(tl)
    db.flush()
    audit(db, u, "transfer.created", "transfer", t.id, code=t.code, shipped_now=body.shipped_now)
    if body.shipped_now:
        p = begin_posting(db, u, f"transfer-create-ship:{t.id}", "transfer_ship", "transfer", t.id)
        _ship_lines(db, u, t, p, [(tl, tl.qty_requested) for tl in created], body.shortfall_reason)
    db.commit()
    return transfer_out(db, t)


def cover_source(db: Session, u: User, p, t: Transfer, sku_id: str, lot_id: str | None, qty: float, reason: str = ""):
    """来源仓（如 Supreme 外库）账面不够时：
    1) 先把该仓“未分批次”的库存认定为这个批次（期初常常没录 Lot）——自动，留审计；
    2) 还不够的部分属于例外：必须写原因，按单据补记到来源仓再调出，留审计。"""
    have = balance(db, sku_id, t.from_wh, "on_hand", lot_id)
    short = round(qty - have, 6)
    if short <= 1e-9:
        return
    relabeled = 0.0
    if lot_id:
        unlot = balance(db, sku_id, t.from_wh, "on_hand", None)
        relabeled = round(min(max(unlot, 0), short), 6)
    added = round(short - relabeled, 6)
    if added > 1e-9 and not (reason or "").strip():
        sku = db.get(Sku, sku_id)
        lot = db.get(Lot, lot_id) if lot_id else None
        msg = (f"来源仓账面不足：{sku.code if sku else ''}{f' 批次 {lot.lot_code}' if lot else ''} "
               f"现有 {round(have + relabeled, 4):g}，本次 {qty:g}。如果对方实际有货（账面没记全），填写原因后可按单据补记。")
        db.rollback()  # 批次等可能是本次新建的，回滚后不能再读
        raise HTTPException(409, {"code": "source_short", "msg": msg})
    if relabeled > 0:
        txn(db, p, sku_id, t.from_wh, "on_hand", -relabeled, None)
        txn(db, p, sku_id, t.from_wh, "on_hand", relabeled, lot_id)
    if added > 1e-9:
        txn(db, p, sku_id, t.from_wh, "on_hand", added, lot_id)
        tag = f"［例外：来源仓账面补记 {added:g}，原因：{reason.strip()}］"
        if tag not in (t.note or ""):
            t.note = f"{t.note or ''} {tag}".strip()
    audit(db, u, "transfer.source_adjusted", "transfer", t.id, sku=sku_id, lot=lot_id, relabeled=relabeled, added=added,
          reason=(reason or "").strip())


def _ship_lines(db: Session, u: User, t: Transfer, p, pairs, shortfall_reason: str = ""):
    """来源仓管账：检查并扣减来源仓；来源地不管账（多伦多/温哥华）：只记目的仓在途。"""
    src = db.get(Warehouse, t.from_wh)
    for l, qty in pairs:
        if qty <= 0:
            continue
        assert_usable(db, l.lot_id, "调拨发运")
        if src.tracked:
            cover_source(db, u, p, t, l.sku_id, l.lot_id, qty, shortfall_reason)
            txn(db, p, l.sku_id, t.from_wh, "on_hand", -qty, l.lot_id)
        txn(db, p, l.sku_id, t.to_wh, "in_transit", qty, l.lot_id)
        l.qty_shipped += qty
    t.status = "shipped" if t.status == "requested" else t.status
    t.version += 1
    audit(db, u, "transfer.shipped", "transfer", t.id, posting=p.id, source_tracked=src.tracked)


class ShipLine(BaseModel):
    line_id: str
    qty: float


class ShipIn(BaseModel):
    idempotency_key: str
    lines: list[ShipLine]
    shortfall_reason: str = ""


@router.post("/transfers/{tid}/ship")
def ship(tid: str, body: ShipIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.transfer")
    t = get_or_404(db, Transfer, tid)
    if t.status in ("received", "cancelled"):
        raise HTTPException(409, f"调拨单状态为 {t.status}，不能发运")
    try:
        p = begin_posting(db, u, body.idempotency_key, "transfer_ship", "transfer", t.id)
    except Dup as d:
        return {**posting_out(db, d.posting, True), "transfer": transfer_out(db, t)}
    lines = {l.id: l for l in db.query(TransferLine).filter_by(transfer_id=t.id)}
    pairs = []
    for sl in body.lines:
        l = lines.get(sl.line_id)
        if not l:
            raise HTTPException(400, "行不存在")
        pairs.append((l, sl.qty))
    _ship_lines(db, u, t, p, pairs, body.shortfall_reason)
    db.commit()
    return {**posting_out(db, p), "transfer": transfer_out(db, t)}


class RecvLine(BaseModel):
    line_id: str
    qty_received: float
    qty_damaged: float = 0


class RecvIn(BaseModel):
    idempotency_key: str
    lines: list[RecvLine]
    note: str = ""


@router.post("/transfers/{tid}/receive")
def receive_transfer(tid: str, body: RecvIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.post")
    t = get_or_404(db, Transfer, tid)
    if t.status not in ("shipped", "partial"):
        raise HTTPException(409, "调拨单尚未发运或已完成")
    try:
        p = begin_posting(db, u, body.idempotency_key, "transfer_receive", "transfer", t.id, body.note)
    except Dup as d:
        return {**posting_out(db, d.posting, True), "transfer": transfer_out(db, t)}
    lines = {l.id: l for l in db.query(TransferLine).filter_by(transfer_id=t.id)}
    discrepancies = []
    for rl in body.lines:
        l = lines.get(rl.line_id)
        if not l:
            raise HTTPException(400, "行不存在")
        open_qty = l.qty_shipped - l.qty_received - l.qty_written_off
        if rl.qty_received < 0 or rl.qty_damaged < 0 or rl.qty_received > open_qty + 1e-9:
            db.rollback()
            raise HTTPException(400, f"实收数量超出在途量 {open_qty}")
        txn(db, p, l.sku_id, t.to_wh, "in_transit", -rl.qty_received, l.lot_id)
        txn(db, p, l.sku_id, t.to_wh, "on_hand", rl.qty_received, l.lot_id)
        l.qty_received += rl.qty_received
        l.qty_damaged += rl.qty_damaged
        if rl.qty_damaged > 0:
            discrepancies.append(f"行 {l.id[-4:]} 损坏 {rl.qty_damaged}")
    remaining = sum(l.qty_shipped - l.qty_received - l.qty_written_off for l in lines.values())
    unshipped = sum(max(l.qty_requested - l.qty_shipped, 0) for l in lines.values())
    t.status = "received" if remaining < 1e-9 and unshipped < 1e-9 else "partial"
    t.version += 1
    if discrepancies or (remaining > 1e-9):
        mgr = db.query(User).filter_by(role="ops_manager", active=True).first()
        task = Task(title=f"调拨差异待处理：{t.code}", kind="exception", source="transfer",
                    description="；".join(discrepancies) + (f"；仍在途 {remaining}" if remaining > 1e-9 else ""),
                    owner_id=mgr.id if mgr else u.id, requires_evidence=True, target_type="transfer", target_id=t.id,
                    done_criteria="差异原因确认（签收单/照片）并完成核销或补发", created_by=u.id)
        db.add(task)
        db.flush()
        notify(db, task.owner_id, task.title, f"/t/{task.id}", dedup_key=f"trdiff:{t.id}")
    audit(db, u, "transfer.received", "transfer", t.id, posting=p.id, status=t.status)
    db.commit()
    return {**posting_out(db, p), "transfer": transfer_out(db, t)}


class WriteOffIn(BaseModel):
    idempotency_key: str
    line_id: str
    qty: float
    reason: str


@router.post("/transfers/{tid}/write-off")
def write_off(tid: str, body: WriteOffIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.post")
    t = get_or_404(db, Transfer, tid)
    l = get_or_404(db, TransferLine, body.line_id)
    open_qty = l.qty_shipped - l.qty_received - l.qty_written_off
    if body.qty <= 0 or body.qty > open_qty + 1e-9 or not body.reason.strip():
        raise HTTPException(400, f"核销数量需在 0-{open_qty} 之间且填写原因")
    try:
        p = begin_posting(db, u, body.idempotency_key, "transfer_loss", "transfer", t.id, body.reason)
    except Dup as d:
        return posting_out(db, d.posting, True)
    txn(db, p, l.sku_id, t.to_wh, "in_transit", -body.qty, l.lot_id)
    l.qty_written_off += body.qty
    lines = db.query(TransferLine).filter_by(transfer_id=t.id).all()
    if all(x.qty_shipped - x.qty_received - x.qty_written_off < 1e-9 and x.qty_requested <= x.qty_shipped for x in lines):
        t.status = "received"
    audit(db, u, "transfer.write_off", "transfer", t.id, qty=body.qty, reason=body.reason)
    db.commit()
    return transfer_out(db, t)


# ---------- quality ----------
class QIn(BaseModel):
    reason: str
    basis: str = ""


@router.post("/lots/{lid}/quarantine")
def quarantine(lid: str, body: QIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "quality.quarantine")
    lot = get_or_404(db, Lot, lid)
    if not body.reason.strip():
        raise HTTPException(400, "隔离需填写原因")
    lot.quality_status, lot.status_reason = "quarantine", body.reason
    db.add(QualityEvent(lot_id=lot.id, action="quarantine", reason=body.reason, basis=body.basis,
                        signature_meaning="确认该批次存在质量疑问，暂停使用", signed_by=u.id))
    affected = db.query(Reservation).filter_by(lot_id=lot.id, active=True).all()
    for q in db.query(User).filter_by(role="quality", active=True):
        notify(db, q.id, f"批次 {lot.lot_code} 已隔离", "/quality")
    audit(db, u, "quality.quarantine", "lot", lot.id, reason=body.reason, affected_orders=[r.order_ref for r in affected])
    db.commit()
    return {"ok": True, "affected_orders": [{"order_ref": r.order_ref, "customer": r.customer, "qty": r.qty} for r in affected]}


@router.post("/lots/{lid}/release")
def release(lid: str, body: QIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "quality.release")
    lot = get_or_404(db, Lot, lid)
    if lot.quality_status == "released":
        raise HTTPException(409, "批次已是放行状态")
    if not body.reason.strip() or not body.basis.strip():
        raise HTTPException(400, "放行需填写理由与依据")
    lot.quality_status, lot.status_reason = "released", body.reason
    db.add(QualityEvent(lot_id=lot.id, action="release", reason=body.reason, basis=body.basis,
                        signature_meaning="我已审核依据，批准该批次放行", signed_by=u.id))
    audit(db, u, "quality.release", "lot", lot.id, reason=body.reason, basis=body.basis)
    db.commit()
    return {"ok": True}


@router.get("/lots/{lid}/quality-events")
def quality_events(lid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    names = {x.id: x.display_name for x in db.query(User).all()}
    return [{"action": e.action, "reason": e.reason, "basis": e.basis, "meaning": e.signature_meaning,
             "by": names.get(e.signed_by), "at": e.signed_at}
            for e in db.query(QualityEvent).filter_by(lot_id=lid).order_by(QualityEvent.signed_at)]


# ---------- production ----------
class PInput(BaseModel):
    lot_id: str
    qty: float


class BatchIn(BaseModel):
    idempotency_key: str
    product_sku_id: str
    warehouse_id: str
    output_lot_code: str
    output_qty: float
    sop_ref: str = ""
    inputs: list[PInput]


@router.get("/batches")
def batches(u: User = Depends(current_user), db: Session = Depends(get_db)):
    lots = {l.id: l for l in db.query(Lot).all()}
    skus = {s.id: s for s in db.query(Sku).all()}
    return [{"id": b.id, "code": b.code, "product": skus[b.product_sku_id].name, "output_lot_id": b.output_lot_id,
             "output_lot": lots[b.output_lot_id].lot_code, "quality_status": lots[b.output_lot_id].quality_status,
             "output_qty": b.output_qty, "sop_ref": b.sop_ref, "created_at": b.created_at,
             "inputs": [{**i, "lot_code": lots[i["lot_id"]].lot_code, "sku": skus[i["sku_id"]].name} for i in b.inputs]}
            for b in db.query(ProductionBatch).order_by(ProductionBatch.created_at.desc()).limit(100)]


@router.post("/batches")
def create_batch(body: BatchIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """生产完工：领用原料批次、产出成品批次。成品默认「待放行」，不能直接发货。"""
    require(u, "production.run")
    if body.output_qty <= 0 or not body.inputs:
        raise HTTPException(400, "需要原料投入与产出数量")
    try:
        p = begin_posting(db, u, body.idempotency_key, "production", "batch", None)
    except Dup as d:
        return posting_out(db, d.posting, True)
    inputs = []
    for i in body.inputs:
        lot = get_or_404(db, Lot, i.lot_id, "原料批次不存在")
        assert_usable(db, lot.id, "领用")
        have = balance(db, lot.sku_id, body.warehouse_id, "on_hand", lot.id)
        if i.qty > have + 1e-9:
            db.rollback()
            raise HTTPException(409, f"原料批次 {lot.lot_code} 账面不足：现有 {have}")
        txn(db, p, lot.sku_id, body.warehouse_id, "on_hand", -i.qty, lot.id)
        inputs.append({"lot_id": lot.id, "sku_id": lot.sku_id, "qty": i.qty})
    if db.query(Lot).filter_by(sku_id=body.product_sku_id, lot_code=body.output_lot_code).first():
        db.rollback()
        raise HTTPException(409, "成品批次号已存在")
    out_lot = get_or_create_lot(db, body.product_sku_id, body.output_lot_code, status="pending")
    out_lot.status_reason = "生产完工，待质量放行"
    n = db.query(ProductionBatch).count() + 1
    b = ProductionBatch(code=f"PB-{datetime.now().strftime('%y%m%d')}-{n:03d}", product_sku_id=body.product_sku_id,
                        output_lot_id=out_lot.id, warehouse_id=body.warehouse_id, output_qty=body.output_qty,
                        inputs=inputs, sop_ref=body.sop_ref, posting_id=p.id, created_by=u.id)
    db.add(b)
    db.flush()
    out_lot.produced_by_batch = b.id
    p.source_id = b.id
    txn(db, p, body.product_sku_id, body.warehouse_id, "on_hand", body.output_qty, out_lot.id)
    for q in db.query(User).filter_by(role="quality", active=True):
        notify(db, q.id, f"成品批次 {out_lot.lot_code} 待放行", "/quality")
    audit(db, u, "production.completed", "batch", b.id, output_lot=out_lot.lot_code, inputs=len(inputs))
    db.commit()
    return {"batch_id": b.id, "code": b.code, "output_lot_id": out_lot.id}


# ---------- trace ----------
@router.get("/trace/{lid}")
def trace(lid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """正向：原料 Lot → 生产批次 → 成品 Lot → 出库/客户；反向：成品 Lot → 生产批次 → 原料 Lot。"""
    lots = {l.id: l for l in db.query(Lot).all()}
    skus = {s.id: s for s in db.query(Sku).all()}
    whs = {w.id: w.name for w in db.query(Warehouse).all()}
    all_batches = db.query(ProductionBatch).all()
    root = lots.get(lid)
    if not root:
        raise HTTPException(404, "批次不存在")

    def lot_info(l):
        return {"lot_id": l.id, "lot_code": l.lot_code, "sku": skus[l.sku_id].name, "sku_code": skus[l.sku_id].code,
                "quality_status": l.quality_status}

    def shipments(lot_id):
        rows = []
        for t in db.query(InventoryTxn).filter_by(lot_id=lot_id, bucket="on_hand").filter(InventoryTxn.qty < 0):
            p = db.get(InventoryPosting, t.posting_id)
            if p.kind in ("issue",) and not p.reversed_by_posting_id:
                rows.append({"type": "出库", "order_ref": p.source_id, "customer": p.note, "qty": -t.qty,
                             "warehouse": whs.get(t.warehouse_id), "at": p.created_at})
            elif p.kind == "transfer_ship":
                tr = db.get(Transfer, p.source_id)
                rows.append({"type": "调拨", "order_ref": tr.code if tr else p.source_id, "customer": whs.get(tr.to_wh) if tr else "",
                             "qty": -t.qty, "warehouse": whs.get(t.warehouse_id), "at": p.created_at})
        for r in db.query(Reservation).filter_by(lot_id=lot_id, active=True):
            rows.append({"type": "已预留", "order_ref": r.order_ref, "customer": r.customer, "qty": r.qty,
                         "warehouse": whs.get(r.warehouse_id), "at": r.created_at})
        return rows

    forward, seen = [], set()

    def walk_fwd(lot_id, depth):
        if lot_id in seen or depth > 6:
            return
        seen.add(lot_id)
        node = {**lot_info(lots[lot_id]), "depth": depth, "shipments": shipments(lot_id), "batches": []}
        forward.append(node)
        for b in all_batches:
            if any(i["lot_id"] == lot_id for i in b.inputs):
                node["batches"].append({"code": b.code, "output_lot": lots[b.output_lot_id].lot_code})
                walk_fwd(b.output_lot_id, depth + 1)

    walk_fwd(lid, 0)
    backward = []

    def walk_back(lot_id, depth):
        l = lots[lot_id]
        if not l.produced_by_batch or depth > 6:
            return
        b = db.get(ProductionBatch, l.produced_by_batch)
        for i in b.inputs:
            backward.append({**lot_info(lots[i["lot_id"]]), "depth": depth + 1, "via_batch": b.code, "qty": i["qty"],
                             "sop_ref": b.sop_ref})
            walk_back(i["lot_id"], depth + 1)

    walk_back(lid, 0)
    audit(db, u, "trace.queried", "lot", lid)
    db.commit()
    return {"root": lot_info(root), "forward": forward, "backward": backward,
            "generated_at": datetime.now(timezone.utc)}
