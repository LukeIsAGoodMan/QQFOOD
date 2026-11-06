"""客户订单与配送（PRD §6.5 ORD-01..03）。

PO（邮件/照片，ChatGPT 提交或手工录入）→ 订单（按行，客户 Item# → 我们 SKU，含换算 factor）
→ 发货：按箱商品按“先到期先出”拣 Lot；称重商品扫箱（每箱实重）→ 出库流水（幂等，隔离/待放行批次拦截）
→ 送达：签收单（或例外原因）、拒收/短少 → 差异任务 → 关闭。金额按实发：箱数 × 单价 或 实重 × 单价。
称重箱：每箱称重打标签（编号、SKU、Lot、重量），可同时入库或只给已在库的 Lot 打标签。
"""
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, notify, require
from ..db import get_db
from ..models import (Box, Customer, CustomerItem, CustomerSite, EvidenceLink, Lot, SalesOrder, Shipment, SOLine, Sku, Task,
                      User, Warehouse)
from .inventory import Dup, assert_usable, balance, begin_posting, get_or_create_lot, txn

router = APIRouter(prefix="/api/orders", tags=["orders"])

SITE_TZ = ZoneInfo("America/Los_Angeles")
OPEN = ["confirmed", "partial"]
STATUS_LABEL = {"confirmed": "待发货", "partial": "部分发货", "shipped": "已发货", "delivered": "已送达", "closed": "已关闭", "cancelled": "已取消"}
POD_PURPOSES = ["签收单", "POD", "收货单照片", "送货照片"]
CATCH_TOLERANCE = 0.10  # 称重行允许比订量多 10%


def _now():
    return datetime.now(timezone.utc)


def today():
    return datetime.now(SITE_TZ).date()


def norm_unit(u) -> str:
    u = str(u or "").strip().lower()
    if u in ("lb", "lbs", "磅", "pound", "pounds"):
        return "磅"
    if u in ("kg", "公斤", "千克"):
        return "公斤"
    if u in ("", "case", "cs", "ctn", "箱", "carton", "pack"):
        return "箱"
    return u


def norm_text(s) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", str(s or "").lower())


def is_catch(sku: Sku | None, unit: str) -> bool:
    return bool(sku and (sku.catch_weight or sku.base_unit in ("磅", "lb")) and unit == "磅")


def default_factor(sku: Sku, unit: str) -> float | None:
    """每个下单单位 = 多少个 SKU 基本单位。"""
    if unit == sku.base_unit or (unit == "磅" and sku.base_unit in ("磅", "lb")):
        return 1.0
    per = (sku.conversions or {}).get(unit)
    return round(1 / float(per), 6) if per else None


def _code(db, model, prefix):
    n = db.query(func.count(model.id)).scalar() + 1
    return f"{prefix}-{datetime.now(SITE_TZ).strftime('%y%m%d')}-{n:03d}"


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


# ---------- customers, sites, item mappings ----------
def find_customer(db, ref: str | None) -> Customer | None:
    if not ref:
        return db.query(Customer).filter_by(code="TNT").first()
    r = ref.strip()
    return (db.query(Customer).filter((Customer.code == r.upper()) | (Customer.id == r)).first()
            or next((c for c in db.query(Customer) if norm_text(r) in norm_text(c.name) or norm_text(c.name) in norm_text(r)), None)
            or (db.query(Customer).filter_by(code="TNT").first() if re.search(r"t\s*&\s*t|tnt|大统华|大統華", r, re.I) else None))


def find_site(db, customer: Customer, ref: str | None) -> CustomerSite | None:
    sites = db.query(CustomerSite).filter_by(customer_id=customer.id, active=True).all()
    if not ref:
        return sites[0] if len(sites) == 1 else None
    r = norm_text(ref)
    for s in sites:
        if (s.store_no and s.store_no in ref) or norm_text(s.name) in r or r in norm_text(s.name):
            return s
    for s in sites:  # loose: first word (e.g. "Chino Hills")
        head = norm_text(s.name.split("-")[0])
        if head and head in r:
            return s
    return None


def match_item(db, customer_id: str, item_no: str | None, description: str | None) -> CustomerItem | None:
    q = db.query(CustomerItem).filter_by(customer_id=customer_id)
    if item_no:
        hit = q.filter_by(item_no=str(item_no).strip()).first()
        if hit:
            return hit
    d = norm_text(description)
    if d:
        for ci in q:
            if norm_text(ci.description) == d:
                return ci
    return None


@router.get("/customers")
def customers(u: User = Depends(current_user), db: Session = Depends(get_db)):
    skus = {s.id: s for s in db.query(Sku)}
    out = []
    for c in db.query(Customer).order_by(Customer.name):
        out.append({"id": c.id, "code": c.code, "name": c.name, "vendor_no": c.vendor_no, "invoice_email": c.invoice_email, "accounting_name": c.accounting_name, "notes": c.notes,
                    "sites": [{k: getattr(s, k) for k in ("id", "name", "store_no", "address", "dept", "contact", "phone", "email", "active")}
                              for s in db.query(CustomerSite).filter_by(customer_id=c.id)],
                    "items": [{"id": i.id, "item_no": i.item_no, "description": i.description, "pack_size": i.pack_size, "order_unit": i.order_unit,
                               "sku_id": i.sku_id, "sku_code": skus[i.sku_id].code if i.sku_id in skus else None, "factor": i.factor,
                               "last_price": i.last_price} for i in db.query(CustomerItem).filter_by(customer_id=c.id).order_by(CustomerItem.description)]})
    return out


class CustomerIn(BaseModel):
    code: str
    name: str
    vendor_no: str = ""
    invoice_email: str = ""
    notes: str = ""


@router.post("/customers")
def create_customer(body: CustomerIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.manage")
    if db.query(Customer).filter_by(code=body.code.strip().upper()).first():
        raise HTTPException(409, "客户编码已存在")
    c = Customer(**{**body.model_dump(), "code": body.code.strip().upper()})
    db.add(c)
    audit(db, u, "customer.created", "customer", c.id, name=c.name)
    db.commit()
    return {"id": c.id}


class SiteIn(BaseModel):
    name: str
    store_no: str = ""
    address: str = ""
    dept: str = ""
    contact: str = ""
    phone: str = ""
    email: str = ""
    active: bool = True


@router.post("/customers/{cid}/sites")
def add_site(cid: str, body: SiteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.manage")
    get_or_404(db, Customer, cid)
    s = CustomerSite(customer_id=cid, **body.model_dump())
    db.add(s)
    audit(db, u, "customer.site_added", "customer", cid, site=body.name)
    db.commit()
    return {"id": s.id}


@router.patch("/sites/{sid}")
def update_site(sid: str, body: SiteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.manage")
    s = get_or_404(db, CustomerSite, sid)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    db.commit()
    return {"ok": True}


class ItemMapIn(BaseModel):
    item_no: str = ""
    description: str
    pack_size: str = ""
    order_unit: str = "箱"
    sku_id: str
    factor: float | None = None


def upsert_mapping(db: Session, customer_id: str, body: ItemMapIn, price: float | None = None) -> CustomerItem:
    sku = get_or_404(db, Sku, body.sku_id, "SKU 不存在")
    unit = norm_unit(body.order_unit)
    factor = body.factor or default_factor(sku, unit)
    if not factor or factor <= 0:
        raise HTTPException(400, f"{sku.code} 没有 {unit} 与 {sku.base_unit} 的换算，请填写每{unit}等于多少{sku.base_unit}")
    ci = match_item(db, customer_id, body.item_no, body.description)
    if not ci:
        ci = CustomerItem(customer_id=customer_id, description=body.description)
        db.add(ci)
    ci.item_no, ci.description, ci.pack_size, ci.order_unit = body.item_no.strip(), body.description, body.pack_size, unit
    ci.sku_id, ci.factor, ci.updated_at = sku.id, factor, _now()
    if price is not None:
        ci.last_price = price
    db.flush()
    return ci


@router.post("/customers/{cid}/items")
def map_item(cid: str, body: ItemMapIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.manage")
    ci = upsert_mapping(db, cid, body)
    audit(db, u, "customer.item_mapped", "customer", cid, item_no=ci.item_no, sku=ci.sku_id, factor=ci.factor)
    db.commit()
    return {"id": ci.id}


# ---------- order creation (shared by manual entry and confirmed batches) ----------
class OLineIn(BaseModel):
    item_no: str = ""
    description: str
    pack_size: str = ""
    unit: str = "箱"
    qty: float
    price: float = 0
    sku_id: str | None = None
    factor: float | None = None


class OrderIn(BaseModel):
    customer_id: str
    site_id: str | None = None
    po_no: str
    po_date: str | None = None
    required_date: str | None = None
    category: str = ""
    note: str = ""
    lines: list[OLineIn]


def duplicate_of(db: Session, customer_id: str, po_no: str, po_date: str | None, site_id: str | None) -> SalesOrder | None:
    """系统 PO 号（纯数字）唯一；中央厨房手写单号（如 CH-CK）会重复，需再加下单日期与送货点区分。"""
    q = db.query(SalesOrder).filter(SalesOrder.customer_id == customer_id, SalesOrder.po_no == po_no.strip(), SalesOrder.status != "cancelled")
    if not re.fullmatch(r"\d{6,}", po_no.strip()):
        q = q.filter(SalesOrder.po_date == po_date, SalesOrder.site_id == site_id)
    return q.first()


def create_order(db: Session, u: User, body: OrderIn, source: str = "manual") -> SalesOrder:
    require(u, "order.manage")
    cust = get_or_404(db, Customer, body.customer_id, "客户不存在")
    if not body.po_no.strip():
        raise HTTPException(400, "需要 PO 号")
    dup = duplicate_of(db, cust.id, body.po_no, body.po_date, body.site_id)
    if dup:
        raise HTTPException(409, f"这张 PO 已录入：{dup.code}")
    if not body.lines:
        raise HTTPException(400, "订单没有任何行")
    main = db.query(Warehouse).filter_by(kind="main").first()
    so = SalesOrder(code=_code(db, SalesOrder, "SO"), customer_id=cust.id, site_id=body.site_id, po_no=body.po_no.strip(), po_date=body.po_date,
                    required_date=body.required_date, category=body.category, note=body.note, source=source, created_by=u.id,
                    warehouse_id=main.id if main else None)
    db.add(so)
    db.flush()
    for i, l in enumerate(body.lines):
        if l.qty <= 0 or l.price < 0:
            raise HTTPException(400, f"第 {i + 1} 行数量需大于 0、单价不为负")
        unit = norm_unit(l.unit)
        sku = db.get(Sku, l.sku_id) if l.sku_id else None
        factor = l.factor or (default_factor(sku, unit) if sku else 1.0) or 1.0
        db.add(SOLine(order_id=so.id, line_no=i + 1, item_no=l.item_no.strip(), description=l.description, pack_size=l.pack_size,
                      order_unit=unit, qty_ordered=l.qty, price=l.price, sku_id=sku.id if sku else None, factor=factor,
                      catch_weight=is_catch(sku, unit)))
        if sku:
            upsert_mapping(db, cust.id, ItemMapIn(item_no=l.item_no, description=l.description, pack_size=l.pack_size, order_unit=unit,
                                                  sku_id=sku.id, factor=factor), price=l.price)
    audit(db, u, "order.created", "so", so.id, po_no=so.po_no, lines=len(body.lines), source=source)
    return so


@router.post("")
def create(body: OrderIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    so = create_order(db, u, body)
    db.commit()
    return {"id": so.id, "code": so.code}


# ---------- read ----------
def available_base(db: Session, sku_id: str, wh_id: str) -> float:
    from .inventory import stock
    rows = [r for r in stock(wh_id, None, db)["rows"] if r["sku_id"] == sku_id]
    return rows[0]["available"] if rows else 0.0


def fefo_lots(db: Session, sku_id: str, wh_id: str):
    """可发货的 Lot：已放行、有账面库存；先到期先出（无到期日的排后，再按入库先后）。"""
    out = []
    for lot in db.query(Lot).filter_by(sku_id=sku_id, quality_status="released"):
        q = balance(db, sku_id, wh_id, "on_hand", lot.id)
        if q > 1e-9:
            out.append({"lot_id": lot.id, "lot_code": lot.lot_code, "expiry": lot.expiry, "qty": round(q, 3), "created_at": lot.created_at})
    nolot = balance(db, sku_id, wh_id, "on_hand", None)
    if nolot > 1e-9:
        out.append({"lot_id": None, "lot_code": "(无批次)", "expiry": None, "qty": round(nolot, 3), "created_at": None})
    out.sort(key=lambda x: (x["expiry"] is None, x["expiry"] or "", str(x["created_at"] or "")))
    return out


def line_out(db, l: SOLine, so: SalesOrder, skus, detail=False):
    sku = skus.get(l.sku_id)
    remaining = max(l.qty_ordered - l.qty_shipped, 0)
    shipped_amount = round(max(l.qty_shipped - l.qty_rejected, 0) * l.price, 2)
    d = {"id": l.id, "line_no": l.line_no, "item_no": l.item_no, "description": l.description, "pack_size": l.pack_size,
         "order_unit": l.order_unit, "qty_ordered": l.qty_ordered, "price": l.price, "amount": round(l.qty_ordered * l.price, 2),
         "sku_id": l.sku_id, "sku_code": sku.code if sku else None, "sku_name": sku.name if sku else None, "base_unit": sku.base_unit if sku else None,
         "factor": l.factor, "catch_weight": l.catch_weight, "qty_shipped": round(l.qty_shipped, 3), "boxes_shipped": l.boxes_shipped,
         "qty_rejected": l.qty_rejected, "reject_reason": l.reject_reason, "remaining": round(remaining, 3), "shipped_amount": shipped_amount}
    if detail and sku and so.warehouse_id:
        need_base = remaining * l.factor
        avail = available_base(db, sku.id, so.warehouse_id)
        d["available_base"] = round(avail, 3)
        d["short"] = remaining > 1e-9 and avail + 1e-9 < need_base
        if l.catch_weight:
            d["boxes_in_stock"] = [{"id": b.id, "serial": b.serial, "weight": b.weight, "lot_code": b.lot_id and db.get(Lot, b.lot_id).lot_code,
                                    "blocked": bool(b.lot_id and db.get(Lot, b.lot_id).quality_status != "released")}
                                   for b in db.query(Box).filter_by(sku_id=sku.id, warehouse_id=so.warehouse_id, status="in_stock").order_by(Box.packed_at)]
        else:
            d["lots"] = fefo_lots(db, sku.id, so.warehouse_id)
    return d


def order_out(db: Session, so: SalesOrder, u: User | None = None, detail=True, names=None):
    names = names or _names(db)
    cust = db.get(Customer, so.customer_id)
    site = db.get(CustomerSite, so.site_id) if so.site_id else None
    skus = {s.id: s for s in db.query(Sku)}
    lines = db.query(SOLine).filter_by(order_id=so.id).order_by(SOLine.line_no).all()
    t = today().isoformat()
    open_ = so.status in OPEN
    d = {"id": so.id, "code": so.code, "po_no": so.po_no, "po_date": so.po_date, "required_date": so.required_date, "category": so.category,
         "status": so.status, "status_label": STATUS_LABEL[so.status], "customer": cust.name, "customer_id": cust.id, "customer_code": cust.code, "customer_accounting_name": cust.accounting_name or cust.name,
         "site": site.name if site else None, "site_id": so.site_id, "total": round(sum(l.qty_ordered * l.price for l in lines), 2),
         "shipped_amount": round(sum(max(l.qty_shipped - l.qty_rejected, 0) * l.price for l in lines), 2),
         "overdue": bool(open_ and so.required_date and so.required_date < t),
         "due_soon": bool(open_ and so.required_date and t <= so.required_date <= (today() + timedelta(days=1)).isoformat()),
         "unmapped": sum(1 for l in lines if not l.sku_id), "lines_count": len(lines), "version": so.version, "source": so.source,
         "exception_count": len(so.exceptions or []), "created_at": so.created_at}
    if not detail:
        return d
    wh = db.get(Warehouse, so.warehouse_id) if so.warehouse_id else None
    shipments = db.query(Shipment).filter_by(order_id=so.id).order_by(Shipment.shipped_at).all()
    d.update({
        "note": so.note, "warehouse": wh.name if wh else None, "warehouse_id": so.warehouse_id, "vendor_no": cust.vendor_no,
        "invoice_email": cust.invoice_email,
        "site_detail": {k: getattr(site, k) for k in ("name", "store_no", "address", "dept", "contact", "phone", "email")} if site else None,
        "lines": [line_out(db, l, so, skus, detail=True) for l in lines],
        "shipments": [{"id": s.id, "code": s.code, "status": s.status, "lines": s.lines, "shipped_by": names.get(s.shipped_by), "shipped_at": s.shipped_at,
                       "delivered_by": names.get(s.delivered_by), "delivered_at": s.delivered_at, "note": s.note,
                       "pod": db.query(EvidenceLink).filter(EvidenceLink.target_type == "shipment", EvidenceLink.target_id == s.id,
                                                            EvidenceLink.active, EvidenceLink.purpose.in_(POD_PURPOSES)).count()} for s in shipments],
        "exceptions": [{**e, "by_name": names.get(e.get("by"))} for e in (so.exceptions or [])],
        "can": {"manage": bool(u and can(u, "order.manage")), "ship": bool(u and can(u, "order.ship"))},
    })
    d["short_lines"] = sum(1 for l in d["lines"] if l.get("short"))
    return d


@router.get("")
def list_orders(status: str | None = None, open_only: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(SalesOrder)
    if status:
        q = q.filter(SalesOrder.status.in_(status.split(",")))
    if open_only:
        q = q.filter(SalesOrder.status.in_(OPEN))
    names = _names(db)
    rows = [order_out(db, so, u, detail=False, names=names) for so in q.order_by(SalesOrder.required_date.is_(None), SalesOrder.required_date, SalesOrder.created_at.desc()).limit(300)]
    return rows


@router.get("/{oid}")
def get_order(oid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return order_out(db, get_or_404(db, SalesOrder, oid, "订单不存在"), u)


class OrderPatch(BaseModel):
    version: int
    required_date: str | None = None
    note: str | None = None
    warehouse_id: str | None = None


def apply_order_patch(db: Session, u: User, so: SalesOrder, body: OrderPatch, via: str = "manual"):
    require(u, "order.manage")
    if body.version != so.version:
        raise HTTPException(409, {"msg": "订单已被他人修改，请刷新", "current_version": so.version})
    changed = {}
    for k in ("required_date", "note", "warehouse_id"):
        v = getattr(body, k)
        if v is not None and v != getattr(so, k):
            changed[k] = [getattr(so, k), v]
            setattr(so, k, v)
    if changed:
        so.version += 1
        so.updated_at = _now()
        audit(db, u, "order.updated", "so", so.id, fields=list(changed), via=via)
    return changed


@router.patch("/{oid}")
def update_order(oid: str, body: OrderPatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    so = get_or_404(db, SalesOrder, oid)
    apply_order_patch(db, u, so, body)
    db.commit()
    return order_out(db, so, u)


class MapLineIn(BaseModel):
    sku_id: str
    factor: float | None = None


@router.post("/lines/{lid}/map")
def map_line(lid: str, body: MapLineIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """给未配对的订单行选 SKU，同时记住对照（下次自动配对）。"""
    require(u, "order.manage")
    l = get_or_404(db, SOLine, lid)
    so = db.get(SalesOrder, l.order_id)
    if l.qty_shipped > 0:
        raise HTTPException(409, "已发货的行不能改 SKU")
    ci = upsert_mapping(db, so.customer_id, ItemMapIn(item_no=l.item_no, description=l.description, pack_size=l.pack_size,
                                                      order_unit=l.order_unit, sku_id=body.sku_id, factor=body.factor), price=l.price)
    sku = db.get(Sku, body.sku_id)
    l.sku_id, l.factor, l.catch_weight = sku.id, ci.factor, is_catch(sku, l.order_unit)
    audit(db, u, "order.line_mapped", "so", so.id, line=l.line_no, sku=sku.code)
    db.commit()
    return order_out(db, so, u)


# ---------- ship ----------
class Pick(BaseModel):
    lot_id: str | None = None
    qty: float  # SKU 基本单位


class ShipLine(BaseModel):
    line_id: str
    qty: float | None = None          # 按箱行：发多少（下单单位）；不填且无 picks = 不发
    picks: list[Pick] | None = None   # 不填则按先到期先出自动拣
    box_ids: list[str] = []           # 称重行：扫到的箱


class ShipIn(BaseModel):
    idempotency_key: str
    lines: list[ShipLine]
    note: str = ""
    over_reason: str = ""


def _add_exc(db, so, u, step, reason):
    if not (reason or "").strip():
        raise HTTPException(400, "例外需要填写原因")
    so.exceptions = [*(so.exceptions or []), {"step": step, "reason": reason.strip(), "by": u.id, "at": _now().isoformat()}]
    audit(db, u, "order.exception", "so", so.id, step=step, reason=reason)


@router.post("/{oid}/ship")
def ship(oid: str, body: ShipIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.ship")
    so = get_or_404(db, SalesOrder, oid)
    if so.status not in OPEN:
        raise HTTPException(409, f"订单状态为「{STATUS_LABEL[so.status]}」，不能发货")
    try:
        p = begin_posting(db, u, body.idempotency_key, "issue", "order", so.po_no, f"{so.code} PO {so.po_no}")
    except Dup:
        return {"duplicate": True, "order": order_out(db, so, u)}
    lines = {l.id: l for l in db.query(SOLine).filter_by(order_id=so.id)}
    shipped_rows, over = [], []
    for sl in body.lines:
        l = lines.get(sl.line_id)
        if not l:
            raise HTTPException(400, "行不属于此订单")
        if not l.sku_id:
            raise HTTPException(409, f"第 {l.line_no} 行「{l.description}」还没对上 SKU，请先配对")
        if l.catch_weight:
            if not sl.box_ids:
                continue
            boxes = db.query(Box).filter(Box.id.in_(sl.box_ids)).all()
            if len(boxes) != len(set(sl.box_ids)):
                raise HTTPException(400, "有箱号不存在")
            weight = 0.0
            for b in boxes:
                if b.status != "in_stock" or b.sku_id != l.sku_id or b.warehouse_id != so.warehouse_id:
                    raise HTTPException(409, f"箱 {b.serial} 不可用（已发出、作废、品项或仓库不符）")
                assert_usable(db, b.lot_id, "发货")
                have = balance(db, b.sku_id, b.warehouse_id, "on_hand", b.lot_id)
                if b.weight > have + 1e-6:
                    raise HTTPException(409, f"箱 {b.serial} 所在批次账面不足（{have}），请先核对库存")
                txn(db, p, b.sku_id, b.warehouse_id, "on_hand", -b.weight, b.lot_id)
                b.status, b.shipment_id = "shipped", None
                weight += b.weight
            if l.qty_shipped + weight > l.qty_ordered * (1 + CATCH_TOLERANCE) + 1e-6:
                over.append(f"第 {l.line_no} 行 {round(l.qty_shipped + weight, 2)} 磅超过订量 {l.qty_ordered}")
            l.qty_shipped += weight
            l.boxes_shipped += len(boxes)
            shipped_rows.append({"line_id": l.id, "qty": round(weight, 3), "weight": round(weight, 3), "boxes": [b.id for b in boxes],
                                 "box_serials": [b.serial for b in boxes], "box_weights": [b.weight for b in boxes],
                                 "lots": sorted({db.get(Lot, b.lot_id).lot_code for b in boxes if b.lot_id})})
        else:
            if sl.qty is None and not sl.picks:
                continue
            need_base = (sl.qty if sl.qty is not None else sum(pk.qty for pk in sl.picks) / l.factor) * l.factor
            if need_base <= 0:
                continue
            picks = sl.picks
            if not picks:
                picks, left = [], need_base
                for lot in fefo_lots(db, l.sku_id, so.warehouse_id):
                    take = min(lot["qty"], left)
                    if take > 1e-9:
                        picks.append(Pick(lot_id=lot["lot_id"], qty=take))
                        left -= take
                    if left <= 1e-9:
                        break
                if left > 1e-6:
                    raise HTTPException(409, f"第 {l.line_no} 行「{l.description}」可发库存不足：还差 {round(left, 3)} {db.get(Sku, l.sku_id).base_unit}")
            lots_used = []
            for pk in picks:
                assert_usable(db, pk.lot_id, "发货")
                have = balance(db, l.sku_id, so.warehouse_id, "on_hand", pk.lot_id)
                if pk.qty > have + 1e-6:
                    raise HTTPException(409, f"批次账面不足：现有 {have}")
                txn(db, p, l.sku_id, so.warehouse_id, "on_hand", -pk.qty, pk.lot_id)
                lots_used.append({"lot_id": pk.lot_id, "lot_code": db.get(Lot, pk.lot_id).lot_code if pk.lot_id else "(无批次)", "qty": pk.qty})
            qty_units = round(sum(pk.qty for pk in picks) / l.factor, 4)
            if l.qty_shipped + qty_units > l.qty_ordered + 1e-6:
                over.append(f"第 {l.line_no} 行 发 {round(l.qty_shipped + qty_units, 2)} 超过订量 {l.qty_ordered}")
            l.qty_shipped += qty_units
            shipped_rows.append({"line_id": l.id, "qty": qty_units, "picks": lots_used, "lots": [x["lot_code"] for x in lots_used]})
    if not shipped_rows:
        raise HTTPException(400, "没有填写任何发货数量或扫箱")
    if over:
        # 大统华会拒收超订量的货：默认拦截，确需超发写原因（留例外标签）
        if not body.over_reason.strip():
            raise HTTPException(409, "超出订量（客户可能拒收）：" + "；".join(over) + "。确需超发请填写原因")
        _add_exc(db, so, u, "超订量发货", body.over_reason)
    sh = Shipment(code=_code(db, Shipment, "SH"), order_id=so.id, lines=shipped_rows, posting_id=p.id, shipped_by=u.id, note=body.note)
    db.add(sh)
    db.flush()
    for b in db.query(Box).filter(Box.id.in_([bid for r in shipped_rows for bid in r.get("boxes", [])])):
        b.shipment_id = sh.id
    p.source_type, p.source_id, p.note = "order", so.po_no, f"{so.code} {sh.code} {db.get(Customer, so.customer_id).name}"
    complete = all((l.qty_shipped >= l.qty_ordered * (1 - CATCH_TOLERANCE) - 1e-6) if l.catch_weight else (l.qty_shipped >= l.qty_ordered - 1e-6)
                   for l in lines.values())
    so.status = "shipped" if complete else "partial"
    so.version += 1
    so.updated_at = _now()
    audit(db, u, "order.shipped", "so", so.id, shipment=sh.code, posting=p.id)
    db.commit()
    return {"duplicate": False, "shipment_id": sh.id, "shipment_code": sh.code, "order": order_out(db, so, u)}


# ---------- deliver / discrepancies / close ----------
class RejectLine(BaseModel):
    line_id: str
    qty: float
    reason: str


class DeliverIn(BaseModel):
    rejected: list[RejectLine] = []
    exception_reason: str = ""  # 没有签收单时


@router.post("/shipments/{sid}/deliver")
def deliver(sid: str, body: DeliverIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.ship")
    sh = get_or_404(db, Shipment, sid)
    so = db.get(SalesOrder, sh.order_id)
    if sh.status == "delivered":
        raise HTTPException(409, "已登记送达")
    pod = db.query(EvidenceLink).filter(EvidenceLink.target_type == "shipment", EvidenceLink.target_id == sh.id, EvidenceLink.active,
                                        EvidenceLink.purpose.in_(POD_PURPOSES)).count()
    if not pod:
        if not body.exception_reason.strip():
            raise HTTPException(409, "请上传签收单（POD）或送货照片；确实没有时填写例外原因")
        _add_exc(db, so, u, f"{sh.code} 无签收依据", body.exception_reason)
    lines = {l.id: l for l in db.query(SOLine).filter_by(order_id=so.id)}
    issues = []
    for r in body.rejected:
        l = lines.get(r.line_id)
        if not l or r.qty <= 0:
            continue
        if not r.reason.strip():
            raise HTTPException(400, "拒收需填写原因")
        l.qty_rejected += r.qty
        l.reject_reason = (l.reject_reason + "；" if l.reject_reason else "") + r.reason
        issues.append(f"第 {l.line_no} 行「{l.description}」拒收 {r.qty} {l.order_unit}：{r.reason}")
    sh.status, sh.delivered_by, sh.delivered_at = "delivered", u.id, _now()
    if issues:
        t = Task(title=f"客户拒收/差异：{so.code} PO {so.po_no}", kind="exception", source="order", requires_evidence=True,
                 description="；".join(issues) + "。退回的货请在库存做「收货」（来源选其他：退货）或报废。", owner_id=so.created_by,
                 target_type="so", target_id=so.id, done_criteria="与客户确认（拒收单/沟通记录），完成退货入库或报废", created_by=u.id)
        db.add(t)
        db.flush()
        notify(db, so.created_by, t.title, f"/t/{t.id}")
    open_ship = db.query(Shipment).filter(Shipment.order_id == so.id, Shipment.status != "delivered", Shipment.id != sh.id).count()
    if so.status == "shipped" and not open_ship:
        so.status = "delivered"
    so.version += 1
    audit(db, u, "order.delivered", "so", so.id, shipment=sh.code, rejected=len(issues))
    db.commit()
    return order_out(db, so, u)


class ReasonIn(BaseModel):
    reason: str


@router.post("/{oid}/close")
def close(oid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """按已发结案：剩余不再发（记得通知客户）。"""
    require(u, "order.manage")
    so = get_or_404(db, SalesOrder, oid)
    if so.status in ("closed", "cancelled"):
        raise HTTPException(409, "订单已结束")
    if not body.reason.strip():
        raise HTTPException(400, "需填写原因")
    so.status = "closed"
    so.note = (so.note + "\n" if so.note else "") + f"结案：{body.reason}"
    so.version += 1
    audit(db, u, "order.closed", "so", so.id, reason=body.reason)
    db.commit()
    return order_out(db, so, u)


@router.post("/{oid}/cancel")
def cancel(oid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "order.manage")
    so = get_or_404(db, SalesOrder, oid)
    if db.query(Shipment).filter_by(order_id=so.id).count():
        raise HTTPException(409, "已有发货，不能取消；请结案")
    if not body.reason.strip():
        raise HTTPException(400, "需填写原因")
    so.status = "cancelled"
    so.note = (so.note + "\n" if so.note else "") + f"取消：{body.reason}"
    so.version += 1
    audit(db, u, "order.cancelled", "so", so.id, reason=body.reason)
    db.commit()
    return order_out(db, so, u)


# ---------- weighed boxes (catch weight) ----------
class BoxesIn(BaseModel):
    sku_id: str
    warehouse_id: str | None = None
    lot_code: str | None = None
    expiry: str | None = None
    weights: list[float]
    post_stock: bool = True  # True = 称重入库（这批货还没记过库存）；False = 只给已在库的 Lot 打标签
    idempotency_key: str


@router.get("/boxes/list")
def list_boxes(sku_id: str | None = None, status: str = "in_stock", u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Box).filter_by(status=status)
    if sku_id:
        q = q.filter_by(sku_id=sku_id)
    skus = {s.id: s for s in db.query(Sku)}
    out = []
    for b in q.order_by(Box.packed_at.desc()).limit(500):
        lot = db.get(Lot, b.lot_id) if b.lot_id else None
        out.append({"id": b.id, "serial": b.serial, "sku_id": b.sku_id, "sku_code": skus[b.sku_id].code, "sku_name": skus[b.sku_id].name,
                    "unit": skus[b.sku_id].base_unit, "lot_code": lot.lot_code if lot else None, "expiry": lot.expiry if lot else None,
                    "quality_status": lot.quality_status if lot else "released", "weight": b.weight, "status": b.status, "packed_at": b.packed_at})
    return out


@router.post("/boxes")
def pack_boxes(body: BoxesIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "inventory.post")
    sku = get_or_404(db, Sku, body.sku_id, "SKU 不存在")
    if not (sku.catch_weight or sku.base_unit in ("磅", "lb")):
        raise HTTPException(409, f"{sku.code} 不是称重品（基本单位需为磅）。可在商品主档把基本单位设为磅")
    if not body.weights or any(w <= 0 or w > 200 for w in body.weights):
        raise HTTPException(400, "每箱重量需在 0–200 磅之间")
    wh = db.get(Warehouse, body.warehouse_id) if body.warehouse_id else db.query(Warehouse).filter_by(kind="main").first()
    try:
        p = begin_posting(db, u, body.idempotency_key, "pack", "boxes", None, f"称重打标 {sku.code}")
    except Dup:
        return {"duplicate": True, "boxes": []}
    lot = get_or_create_lot(db, sku.id, body.lot_code, body.expiry)
    lot_id = lot.id if lot else None
    total = round(sum(body.weights), 3)
    if body.post_stock:
        txn(db, p, sku.id, wh.id, "on_hand", total, lot_id)
    else:
        have = balance(db, sku.id, wh.id, "on_hand", lot_id)
        boxed = db.query(func.coalesce(func.sum(Box.weight), 0)).filter_by(sku_id=sku.id, warehouse_id=wh.id, lot_id=lot_id, status="in_stock").scalar()
        if total + boxed > have + 1e-6:
            raise HTTPException(409, f"该批次账面 {have} 磅，已装箱 {round(boxed, 3)} 磅，不够再装 {total} 磅；如果这批货还没入库，请勾选「称重入库」")
    n0 = db.query(func.count(Box.id)).scalar()
    stamp = datetime.now(SITE_TZ).strftime("%y%m%d")
    boxes = []
    for i, w in enumerate(body.weights):
        b = Box(serial=f"BX{stamp}-{n0 + i + 1:05d}", sku_id=sku.id, lot_id=lot_id, warehouse_id=wh.id, weight=round(w, 2), packed_by=u.id)
        db.add(b)
        boxes.append(b)
    p.source_id = boxes[0].serial if boxes else None
    db.flush()
    audit(db, u, "boxes.packed", "sku", sku.id, count=len(boxes), total=total, lot=body.lot_code, post_stock=body.post_stock)
    db.commit()
    return {"duplicate": False, "total": total, "boxes": [{"id": b.id, "serial": b.serial, "weight": b.weight, "lot_code": lot.lot_code if lot else None,
                                                            "sku_code": sku.code, "sku_name": sku.name, "expiry": lot.expiry if lot else None,
                                                            "packed_at": b.packed_at} for b in boxes]}


@router.post("/boxes/{bid}/void")
def void_box(bid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """作废标签（贴错、重称）。不改库存；货物若报废请另做库存调整。"""
    require(u, "inventory.post")
    b = get_or_404(db, Box, bid)
    if b.status != "in_stock":
        raise HTTPException(409, "只有在库的箱可以作废")
    if not body.reason.strip():
        raise HTTPException(400, "需填写原因")
    b.status = "void"
    audit(db, u, "box.voided", "box", b.id, serial=b.serial, reason=body.reason)
    db.commit()
    return {"ok": True}


# ---------- QuickBooks 发票导出（QuickBooks Online「导入发票」CSV） ----------
class QBIn(BaseModel):
    order_ids: list[str]
    invoice_no: str
    invoice_date: str | None = None  # YYYY-MM-DD，默认今天（洛杉矶）
    terms: str = "Net 30"
    customer_name: str | None = None  # QuickBooks 里的客户名；填了会记住
    basis: str = "shipped"  # shipped 按实发（扣拒收） | ordered 按订量


QB_COLUMNS = ["InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Terms", "Memo", "Item(Product/Service)", "ItemDescription",
              "ItemQuantity", "ItemRate", "ItemAmount"]


def quickbooks_invoice(db: Session, u: User, body: QBIn) -> dict:
    import csv
    import io
    from .aliases import accounting_name
    require(u, "order.manage")
    if not body.order_ids:
        raise HTTPException(400, "请选择订单")
    if not body.invoice_no.strip():
        raise HTTPException(400, "请填写发票号")
    orders = [get_or_404(db, SalesOrder, i, "订单不存在") for i in dict.fromkeys(body.order_ids)]
    if len({o.customer_id for o in orders}) > 1:
        raise HTTPException(400, "一张发票只能是同一个客户的订单")
    cust = db.get(Customer, orders[0].customer_id)
    cname = (body.customer_name or "").strip() or cust.accounting_name or cust.name
    if body.customer_name and body.customer_name.strip() != cust.accounting_name:
        cust.accounting_name = body.customer_name.strip()
    try:
        d0 = datetime.strptime(body.invoice_date, "%Y-%m-%d").date() if body.invoice_date else datetime.now(SITE_TZ).date()
    except ValueError:
        raise HTTPException(400, "发票日期格式应为 YYYY-MM-DD")
    m = re.search(r"(\d+)", body.terms or "")
    due = d0 + timedelta(days=int(m.group(1))) if m and "net" in body.terms.lower() else d0
    memo = "PO " + " / ".join(o.po_no for o in orders)
    rows, warnings, missing, total = [], [], set(), 0.0
    for o in orders:
        for l in db.query(SOLine).filter_by(order_id=o.id).order_by(SOLine.line_no):
            qty = (l.qty_shipped - l.qty_rejected) if body.basis == "shipped" else l.qty_ordered
            if qty <= 1e-9:
                if body.basis == "shipped" and l.qty_ordered > 0:
                    warnings.append(f"PO {o.po_no} 第 {l.line_no} 行 {l.description} 没有发货，未开票")
                continue
            sku = db.get(Sku, l.sku_id) if l.sku_id else None
            item = (accounting_name(db, sku.id) if sku else None)
            if not item:
                item = sku.name if sku else l.description
                missing.add(f"{sku.code} {sku.name}" if sku else l.description)
            desc = " ".join(x for x in (l.item_no, l.description, l.pack_size) if x)
            amt = round(qty * l.price, 2)
            total += amt
            rows.append([body.invoice_no.strip(), cname, d0.strftime("%m/%d/%Y"), due.strftime("%m/%d/%Y"), body.terms, memo, item, desc,
                         f"{qty:g}", f"{l.price:.2f}", f"{amt:.2f}"])
    if not rows:
        raise HTTPException(400, "所选订单没有可开票的行（还没发货？可改为按订量）")
    if missing:
        warnings.append("这些商品还没记 QuickBooks 品名，先用了我们的名称（QuickBooks 可能会新建商品）："
                        + "、".join(sorted(missing)) + "。在库存页「商品对照」里加 QuickBooks 一栏即可。")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(QB_COLUMNS)
    w.writerows(rows)
    audit(db, u, "order.invoice_exported", "so", orders[0].id, invoice_no=body.invoice_no.strip(), orders=[o.code for o in orders],
          total=round(total, 2), basis=body.basis)
    return {"filename": f"{body.invoice_no.strip()}.csv", "csv": "﻿" + buf.getvalue(), "rows": len(rows), "total": round(total, 2),
            "customer": cname, "invoice_date": d0.isoformat(), "due_date": due.isoformat(), "warnings": warnings}


@router.post("/quickbooks")
def export_quickbooks(body: QBIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = quickbooks_invoice(db, u, body)
    db.commit()
    return r
