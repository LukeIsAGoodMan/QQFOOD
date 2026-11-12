"""采购到收货闭环（PRD §6.5 PUR-01..03，§3.6 审批节点）。

需求（按版本，含余量）→ 报价（挂在版本上，含税运安装费与交期）→ 采购单（按行；超额度需老板签批）→ 下单
→ 收货单（现场按行提交实收/Lot/拒收）→ 有签收单后仓管签名入账（库存流水，幂等）→ 差异任务 / 短收关闭
→ 发票三方核对 → 付款证明（已付款待核对）→ 财务核对签名。
付款证明上传不等于核对完成；核对由财务签名。
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, notify, require
from ..db import get_db
from ..models import (AppSetting, EvidenceLink, InventoryPosting, POLine, PurchaseOrder, Quote, Receipt, RecordHistory,
                      Requisition, RequisitionVersion, Sku, Task, User, Warehouse, gid)
from .inventory import Dup, begin_posting, get_or_create_lot, txn

router = APIRouter(prefix="/api/purchasing", tags=["purchasing"])

SITE_TZ = ZoneInfo("America/Los_Angeles")
CATEGORIES = ["设备", "耗材", "原料包材", "日常用品"]
# 灵活凭证：任何一种都可作为依据；都没有时可填写例外原因（留例外标签）
SIGNOFF_PURPOSES = ["签收单", "收据", "到货照片", "发货单"]
DOC_PURPOSES = {"invoice": ["发票"], "receipt": ["收据", "发票"], "none": []}
PAY_PURPOSES = ["付款证明", "收据"]
DOC_LABEL = {"invoice": "发票", "receipt": "收据", "none": "无票据"}
from ..categories import FOOD as LOT_REQUIRED_CATEGORIES  # 食品（干货/冻货/原料肉/原料菜）必须有 Lot（追溯）；用品、机器可不填
OPEN_STATUSES = ["draft", "pending_approval", "approved", "ordered", "partial"]
STATUS_LABEL = {"draft": "草稿", "pending_approval": "待审批", "approved": "已批准", "rejected": "已驳回", "ordered": "已下单",
                "partial": "部分到货", "received": "已到齐", "closed": "已关闭", "cancelled": "已取消"}
PAY_LABEL = {"none": "未开票", "invoiced": "已收发票", "paid_pending": "已付款待核对", "reconciled": "已核对"}
MATCH_TOLERANCE = 1.0  # USD


def _now():
    return datetime.now(timezone.utc)


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


def _code(db, model, prefix):
    n = db.query(func.count(model.id)).scalar() + 1
    return f"{prefix}-{datetime.now(SITE_TZ).strftime('%y%m%d')}-{n:03d}"


# ---------- settings ----------
def approval_limit(db: Session) -> float:
    s = db.get(AppSetting, "purchasing")
    return float((s.value or {}).get("approval_limit", 2000)) if s else 2000.0


@router.get("/settings")
def get_settings(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"approval_limit": approval_limit(db), "can_edit": can(u, "settings.purchasing"), "categories": CATEGORIES}


class SettingsIn(BaseModel):
    approval_limit: float


@router.put("/settings")
def put_settings(body: SettingsIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "settings.purchasing")
    if body.approval_limit < 0:
        raise HTTPException(400, "额度不能为负")
    s = db.get(AppSetting, "purchasing") or AppSetting(key="purchasing", value={})
    old = (s.value or {}).get("approval_limit")
    s.value = {**(s.value or {}), "approval_limit": body.approval_limit}
    s.updated_by, s.updated_at = u.id, _now()
    db.merge(s)
    audit(db, u, "settings.purchasing", "setting", "purchasing", approval_limit=[old, body.approval_limit])
    db.commit()
    return {"approval_limit": body.approval_limit}


# ---------- requisitions & quotes ----------
class ReqLine(BaseModel):
    key: str | None = None
    sku_id: str | None = None
    item_name: str
    base_qty: float
    unit: str = "箱"


class ReqIn(BaseModel):
    title: str
    category: str = "原料包材"
    margin_pct: float = 0
    lines: list[ReqLine]
    note: str = ""


def _req_lines(lines: list[ReqLine], margin: float):
    if not lines or any(l.base_qty <= 0 for l in lines):
        raise HTTPException(400, "至少一行，且基础数量大于 0")
    if not 0 <= margin <= 100:
        raise HTTPException(400, "余量需在 0-100% 之间")
    out = []
    for i, l in enumerate(lines):
        out.append({"key": l.key or f"L{i + 1}", "sku_id": l.sku_id, "item_name": l.item_name.strip(), "base_qty": l.base_qty, "unit": l.unit})
    return out


def _version_out(v: RequisitionVersion, names):
    return {"id": v.id, "version_no": v.version_no, "margin_pct": v.margin_pct, "note": v.note, "by": names.get(v.created_by),
            "at": v.created_at, "lines": [{**l, "required_qty": round(l["base_qty"] * (1 + v.margin_pct / 100), 3)} for l in v.lines]}


def _quote_total(q: Quote, v: RequisitionVersion):
    req = {l["key"]: l["base_qty"] * (1 + v.margin_pct / 100) for l in v.lines}
    missing = [k for k in req if k not in q.prices]
    subtotal = sum(req[k] * float(q.prices.get(k, 0)) for k in req)
    return round(subtotal, 2), round(subtotal + q.tax + q.freight + q.install_fee, 2), missing


@router.get("/requisitions")
def list_reqs(u: User = Depends(current_user), db: Session = Depends(get_db)):
    names = _names(db)
    out = []
    for r in db.query(Requisition).order_by(Requisition.created_at.desc()).limit(200):
        v = db.get(RequisitionVersion, r.current_version_id)
        out.append({"id": r.id, "code": r.code, "title": r.title, "category": r.category, "status": r.status,
                    "owner": names.get(r.owner_id), "version_no": v.version_no if v else None, "margin_pct": v.margin_pct if v else None,
                    "quotes": db.query(Quote).filter_by(requisition_id=r.id).count(), "created_at": r.created_at})
    return out


@router.post("/requisitions")
def create_req(body: ReqIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    if body.category not in CATEGORIES:
        raise HTTPException(400, "分类无效")
    r = Requisition(code=_code(db, Requisition, "RQ"), title=body.title, category=body.category, owner_id=u.id, created_by=u.id)
    db.add(r)
    db.flush()
    v = RequisitionVersion(requisition_id=r.id, version_no=1, margin_pct=body.margin_pct, lines=_req_lines(body.lines, body.margin_pct),
                           note=body.note, created_by=u.id)
    db.add(v)
    db.flush()
    r.current_version_id = v.id
    audit(db, u, "requisition.created", "requisition", r.id, code=r.code, margin=body.margin_pct)
    db.commit()
    return {"id": r.id, "code": r.code}


@router.get("/requisitions/{rid}")
def get_req(rid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Requisition, rid)
    names = _names(db)
    versions = {v.id: v for v in db.query(RequisitionVersion).filter_by(requisition_id=rid)}
    quotes = []
    for q in db.query(Quote).filter_by(requisition_id=rid).order_by(Quote.created_at):
        v = versions[q.version_id]
        sub, total, missing = _quote_total(q, v)
        quotes.append({"id": q.id, "supplier": q.supplier, "version_id": q.version_id, "version_no": v.version_no,
                       "stale": q.version_id != r.current_version_id, "prices": q.prices, "subtotal": sub, "tax": q.tax,
                       "freight": q.freight, "install_fee": q.install_fee, "total": total, "missing_lines": missing,
                       "lead_time_days": q.lead_time_days, "valid_until": q.valid_until, "note": q.note, "status": q.status,
                       "by": names.get(q.created_by), "at": q.created_at})
    cur = [q for q in quotes if not q["stale"] and not q["missing_lines"] and q["status"] != "rejected"]
    if cur:
        cheapest = min(cur, key=lambda q: q["total"])
        cheapest["best_price"] = True
        timed = [q for q in cur if q["lead_time_days"] is not None]
        if timed:
            min(timed, key=lambda q: q["lead_time_days"])["fastest"] = True
    pos = db.query(PurchaseOrder).filter_by(requisition_id=rid).all()
    return {"id": r.id, "code": r.code, "title": r.title, "category": r.category, "status": r.status, "owner": names.get(r.owner_id),
            "current_version_id": r.current_version_id,
            "versions": [_version_out(v, names) for v in sorted(versions.values(), key=lambda v: -v.version_no)],
            "quotes": quotes, "pos": [{"id": p.id, "code": p.code, "status": p.status} for p in pos],
            "can_manage": can(u, "po.manage")}


class VersionIn(BaseModel):
    margin_pct: float
    lines: list[ReqLine]
    note: str = ""


@router.post("/requisitions/{rid}/versions")
def new_version(rid: str, body: VersionIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """新需求版本：旧版本与其报价保留（PUR-02），旧报价标记为“基于旧版本”。"""
    require(u, "po.manage")
    r = get_or_404(db, Requisition, rid)
    if r.status in ("closed", "cancelled"):
        raise HTTPException(409, "需求已关闭")
    n = (db.query(func.max(RequisitionVersion.version_no)).filter_by(requisition_id=rid).scalar() or 0) + 1
    v = RequisitionVersion(requisition_id=rid, version_no=n, margin_pct=body.margin_pct, lines=_req_lines(body.lines, body.margin_pct),
                           note=body.note, created_by=u.id)
    db.add(v)
    db.flush()
    r.current_version_id = v.id
    audit(db, u, "requisition.versioned", "requisition", rid, version=n, margin=body.margin_pct)
    db.commit()
    return {"version_id": v.id, "version_no": n}


class QuoteIn(BaseModel):
    version_id: str | None = None
    supplier: str
    supplier_contact_id: str | None = None
    prices: dict[str, float]
    tax: float = 0
    freight: float = 0
    install_fee: float = 0
    lead_time_days: int | None = None
    valid_until: str | None = None
    note: str = ""


@router.post("/requisitions/{rid}/quotes")
def add_quote(rid: str, body: QuoteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    r = get_or_404(db, Requisition, rid)
    v = get_or_404(db, RequisitionVersion, body.version_id or r.current_version_id)
    if v.requisition_id != rid:
        raise HTTPException(400, "版本不属于此需求")
    keys = {l["key"] for l in v.lines}
    if set(body.prices) - keys:
        raise HTTPException(400, "报价包含不存在的需求行")
    if any(p < 0 for p in body.prices.values()) or min(body.tax, body.freight, body.install_fee) < 0:
        raise HTTPException(400, "金额不能为负")
    q = Quote(requisition_id=rid, version_id=v.id, **body.model_dump(exclude={"version_id"}), created_by=u.id)
    db.add(q)
    db.flush()
    audit(db, u, "quote.added", "requisition", rid, quote=q.id, supplier=body.supplier, version=v.version_no)
    db.commit()
    return {"id": q.id}


class FromQuoteIn(BaseModel):
    warehouse_id: str | None = None
    expected_date: str | None = None


@router.post("/quotes/{qid}/to-po")
def quote_to_po(qid: str, body: FromQuoteIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """选定报价生成采购单草稿：数量取报价所依据版本的需求量（含余量），单价取报价。"""
    require(u, "po.manage")
    q = get_or_404(db, Quote, qid)
    r = db.get(Requisition, q.requisition_id)
    v = db.get(RequisitionVersion, q.version_id)
    if q.version_id != r.current_version_id:
        raise HTTPException(409, f"该报价基于旧版本 v{v.version_no}，请按当前版本重新报价")
    _, _, missing = _quote_total(q, v)
    if missing:
        raise HTTPException(409, f"报价缺少需求行：{', '.join(missing)}")
    po = PurchaseOrder(code=_code(db, PurchaseOrder, "PO"), supplier=q.supplier, supplier_contact_id=q.supplier_contact_id,
                       category=r.category, warehouse_id=body.warehouse_id or _default_wh(db), requisition_id=r.id,
                       requisition_version_id=v.id, quote_id=q.id, expected_date=body.expected_date, tax=q.tax, freight=q.freight,
                       install_fee=q.install_fee, owner_id=u.id, created_by=u.id,
                       note=f"来自 {r.code} v{v.version_no}（余量 {v.margin_pct:g}%）报价：{q.supplier}")
    db.add(po)
    db.flush()
    for i, l in enumerate(v.lines):
        db.add(POLine(po_id=po.id, sku_id=l.get("sku_id"), item_name=l["item_name"], unit=l["unit"], sort=i,
                      qty_ordered=round(l["base_qty"] * (1 + v.margin_pct / 100), 3), unit_price=float(q.prices[l["key"]])))
    q.status = "selected"
    for other in db.query(Quote).filter(Quote.requisition_id == r.id, Quote.id != q.id, Quote.status == "active"):
        other.status = "rejected"
    r.status = "ordered"
    audit(db, u, "po.created", "po", po.id, code=po.code, from_quote=q.id)
    db.commit()
    return {"id": po.id, "code": po.code}


def _default_wh(db):
    w = db.query(Warehouse).filter_by(kind="main").first()
    return w.id if w else None


# ---------- purchase orders ----------
def _totals(po: PurchaseOrder, lines: list[POLine]):
    goods = sum(l.qty_ordered * l.unit_price for l in lines)
    received_goods = sum(l.qty_received * l.unit_price for l in lines)
    fees = po.tax + po.freight + po.install_fee
    total = round(goods + fees, 2)
    # 应付参考额：已收货物金额 + 费用（费用在首次收货后计入）
    payable = round(received_goods + (fees if received_goods > 0 or po.status in ("received", "closed") else 0), 2)
    match = None
    if po.invoice_amount is not None:
        diff = round(po.invoice_amount - payable, 2)
        match = {"invoice": po.invoice_amount, "payable": payable, "ordered": total, "diff": diff,
                 "ok": abs(diff) <= MATCH_TOLERANCE}
    return total, payable, match


def _ev_any(db, target_type, target_id, purposes):
    return db.query(EvidenceLink).filter_by(target_type=target_type, target_id=target_id, active=True).filter(
        EvidenceLink.verify_status != "rejected", EvidenceLink.purpose.in_(purposes)).count()


def _add_exception(db, po, u, step, reason):
    if not (reason or "").strip():
        raise HTTPException(400, "例外需要填写原因")
    po.exceptions = [*(po.exceptions or []), {"step": step, "reason": reason.strip(), "by": u.id, "at": _now().isoformat()}]
    audit(db, u, "po.exception", "po", po.id, step=step, reason=reason)


def _awaiting_approval(po):
    a = po.approval or {}
    return po.status == "pending_approval" or (po.quick and a.get("required") and not a.get("decision"))


def _ev_count(db, target_type, target_id, purpose=None):
    q = db.query(EvidenceLink).filter_by(target_type=target_type, target_id=target_id, active=True).filter(EvidenceLink.verify_status != "rejected")
    if purpose:
        q = q.filter_by(purpose=purpose)
    return q.count()


def po_out(db: Session, po: PurchaseOrder, u: User, names=None, detail=True):
    names = names or _names(db)
    lines = db.query(POLine).filter_by(po_id=po.id).order_by(POLine.sort).all()
    total, payable, match = _totals(po, lines)
    today = datetime.now(SITE_TZ).date().isoformat()
    overdue = bool(po.expected_date and po.expected_date < today and po.status in ("ordered", "partial"))
    wh = db.get(Warehouse, po.warehouse_id) if po.warehouse_id else None
    d = {"id": po.id, "code": po.code, "title": po.title, "supplier": po.supplier, "category": po.category, "status": po.status,
         "status_label": STATUS_LABEL[po.status], "expected_date": po.expected_date, "overdue": overdue, "total": total,
         "warehouse_id": po.warehouse_id, "warehouse": wh.name if wh else None, "owner": names.get(po.owner_id),
         "payment_status": po.payment_status, "payment_label": PAY_LABEL[po.payment_status], "version": po.version,
         "pending_receipts": db.query(Receipt).filter_by(po_id=po.id, status="submitted").count(),
         "quick": po.quick, "purchase_date": po.purchase_date, "payment_method": po.payment_method,
         "payment_method_label": PAYMETHOD_LABEL.get(po.payment_method or "", po.payment_method), "paid_by": po.paid_by,
         "awaiting_approval": _awaiting_approval(po), "exception_count": len(po.exceptions or []),
         "doc_type": po.doc_type, "doc_label": DOC_LABEL.get(po.doc_type, po.doc_type),
         "missing_doc": po.doc_type != "none" and po.status in ("partial", "received", "closed") and po.payment_status != "reconciled"
         and not _ev_any(db, "po", po.id, DOC_PURPOSES.get(po.doc_type, ["发票"])),
         "received_pct": round(100 * sum(min(l.qty_received, l.qty_ordered) for l in lines) / max(sum(l.qty_ordered for l in lines), 1e-9)),
         "created_at": po.created_at, "updated_at": po.updated_at}
    if not detail:
        return d
    skus = {s.id: s for s in db.query(Sku).all()}
    receipts = db.query(Receipt).filter_by(po_id=po.id).order_by(Receipt.submitted_at.desc()).all()
    approval = dict(po.approval or {})
    if approval.get("by"):
        approval["by_name"] = names.get(approval["by"])
    d.update({
        "tax": po.tax, "freight": po.freight, "install_fee": po.install_fee, "note": po.note, "close_reason": po.close_reason,
        "requisition_id": po.requisition_id, "quote_id": po.quote_id, "approval": approval, "approval_limit": approval_limit(db),
        "invoice_no": po.invoice_no, "invoice_amount": po.invoice_amount, "payable": payable, "match": match,
        "reconcile": {**(po.reconcile or {}), "by_name": names.get((po.reconcile or {}).get("by"))},
        "lines": [{"id": l.id, "sku_id": l.sku_id, "sku_code": skus[l.sku_id].code if l.sku_id in skus else None,
                   "item_name": l.item_name, "unit": l.unit, "qty_ordered": l.qty_ordered, "unit_price": l.unit_price,
                   "amount": round(l.qty_ordered * l.unit_price, 2), "qty_received": l.qty_received, "qty_rejected": l.qty_rejected,
                   "open_qty": round(max(l.qty_ordered - l.qty_received, 0), 3), "inventory": bool(l.sku_id)} for l in lines],
        "receipts": [{"id": r.id, "code": r.code, "status": r.status, "lines": r.lines, "note": r.note,
                      "submitted_by": names.get(r.submitted_by), "submitted_at": r.submitted_at, "posted_by": names.get(r.posted_by),
                      "posted_at": r.posted_at, "self_posted": r.posted_by == r.submitted_by and r.status == "posted",
                      "signoff_evidence": _ev_any(db, "receipt", r.id, SIGNOFF_PURPOSES)} for r in receipts],
        "evidence": {"doc": _ev_any(db, "po", po.id, DOC_PURPOSES.get(po.doc_type, ["发票"])) if po.doc_type != "none" else 0,
                     "invoice": _ev_count(db, "po", po.id, "发票"), "payment_proof": _ev_any(db, "po", po.id, PAY_PURPOSES)},
        "exceptions": [{**e, "by_name": names.get(e.get("by"))} for e in (po.exceptions or [])],
        "can": {"manage": can(u, "po.manage"), "approve": can(u, "po.approve"), "receive": can(u, "po.receive"),
                "post": can(u, "inventory.post"), "reconcile": can(u, "finance.reconcile")},
    })
    return d


@router.get("/pos")
def list_pos(status: str | None = None, open_only: bool = False, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(PurchaseOrder)
    if status:
        q = q.filter(PurchaseOrder.status.in_(status.split(",")))
    if open_only:
        q = q.filter(PurchaseOrder.status.in_(OPEN_STATUSES))
    names = _names(db)
    return [po_out(db, p, u, names, detail=False) for p in q.order_by(PurchaseOrder.created_at.desc()).limit(300)]


class POLineIn(BaseModel):
    sku_id: str | None = None
    item_name: str | None = None
    unit: str | None = None
    qty_ordered: float
    unit_price: float = 0


class POIn(BaseModel):
    supplier: str
    supplier_contact_id: str | None = None
    category: str = "原料包材"
    warehouse_id: str | None = None
    expected_date: str | None = None
    tax: float = 0
    freight: float = 0
    install_fee: float = 0
    note: str = ""
    lines: list[POLineIn]


def _set_lines(db, po, lines: list[POLineIn]):
    if not lines or any(l.qty_ordered <= 0 or l.unit_price < 0 for l in lines):
        raise HTTPException(400, "至少一行；数量大于 0，单价不为负")
    db.query(POLine).filter_by(po_id=po.id).delete()
    for i, l in enumerate(lines):
        sku = db.get(Sku, l.sku_id) if l.sku_id else None
        if l.sku_id and not sku:
            raise HTTPException(400, "SKU 不存在")
        name = (l.item_name or (sku.name if sku else "")).strip()
        if not name:
            raise HTTPException(400, "非库存行需要填写品名")
        db.add(POLine(po_id=po.id, sku_id=l.sku_id, item_name=name, unit=l.unit or (sku.base_unit if sku else "件"),
                      qty_ordered=l.qty_ordered, unit_price=l.unit_price, sort=i))


@router.post("/pos")
def create_po(body: POIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    if body.category not in CATEGORIES:
        raise HTTPException(400, "分类无效")
    if min(body.tax, body.freight, body.install_fee) < 0:
        raise HTTPException(400, "费用不能为负")
    po = PurchaseOrder(code=_code(db, PurchaseOrder, "PO"), **body.model_dump(exclude={"lines"}), owner_id=u.id, created_by=u.id)
    po.warehouse_id = po.warehouse_id or _default_wh(db)
    db.add(po)
    db.flush()
    _set_lines(db, po, body.lines)
    audit(db, u, "po.created", "po", po.id, code=po.code)
    db.commit()
    return {"id": po.id, "code": po.code}


@router.get("/pos/{pid}")
def get_po(pid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return po_out(db, get_or_404(db, PurchaseOrder, pid, "采购单不存在"), u)


class POPatch(BaseModel):
    version: int
    supplier: str | None = None
    expected_date: str | None = None
    note: str | None = None
    warehouse_id: str | None = None
    tax: float | None = None
    freight: float | None = None
    install_fee: float | None = None
    lines: list[POLineIn] | None = None


def apply_po_patch(db: Session, u: User, po: PurchaseOrder, body: POPatch, via: str = "manual") -> dict:
    require(u, "po.manage")
    if body.version != po.version:
        raise HTTPException(409, {"msg": "采购单已被他人修改，请刷新后再保存", "current_version": po.version})
    money_change = any(v is not None for v in (body.tax, body.freight, body.install_fee, body.lines, body.supplier))
    if money_change and po.status not in ("draft", "rejected"):
        raise HTTPException(409, "已提交审批或下单的采购单不能改供应商、金额或行；如需变更请取消后重建")
    diff = {}
    for k in ("supplier", "expected_date", "note", "warehouse_id", "tax", "freight", "install_fee"):
        v = getattr(body, k)
        if v is not None and v != getattr(po, k):
            diff[k] = [getattr(po, k), v]
            setattr(po, k, v)
    if body.lines is not None:
        _set_lines(db, po, body.lines)
        diff["lines"] = "replaced"
    if diff:
        po.version += 1
        po.updated_at = _now()
        db.add(RecordHistory(target_type="po", target_id=po.id, version=po.version, changed_by=u.id, diff=diff, via=via))
        audit(db, u, "po.updated", "po", po.id, fields=list(diff), via=via)
    return diff


@router.patch("/pos/{pid}")
def update_po(pid: str, body: POPatch, u: User = Depends(current_user), db: Session = Depends(get_db)):
    po = get_or_404(db, PurchaseOrder, pid)
    apply_po_patch(db, u, po, body)
    db.commit()
    return po_out(db, po, u)


def _bump(po):
    po.version += 1
    po.updated_at = _now()


@router.post("/pos/{pid}/submit")
def submit_po(pid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    po = get_or_404(db, PurchaseOrder, pid)
    if po.status not in ("draft", "rejected"):
        raise HTTPException(409, "只有草稿或已驳回的采购单可以提交")
    total, _, _ = _totals(po, db.query(POLine).filter_by(po_id=pid).all())
    limit = approval_limit(db)
    if total > limit:
        po.status = "pending_approval"
        po.approval = {"required": True, "limit": limit, "total": total, "submitted_by": u.id, "submitted_at": _now().isoformat()}
        for b in db.query(User).filter_by(role="boss", active=True):
            notify(db, b.id, f"采购待审批：{po.code} {po.supplier} ${total:,.2f}", "/purchasing", dedup_key=f"poappr:{po.id}")
    else:
        po.status = "approved"
        po.approval = {"required": False, "limit": limit, "total": total}
    _bump(po)
    audit(db, u, "po.submitted", "po", po.id, total=total, limit=limit, needs_approval=total > limit)
    db.commit()
    return po_out(db, po, u)


class DecisionIn(BaseModel):
    decision: str  # approve | reject
    reason: str = ""


@router.post("/pos/{pid}/approve")
def approve_po(pid: str, body: DecisionIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.approve")
    po = get_or_404(db, PurchaseOrder, pid)
    if not _awaiting_approval(po):
        raise HTTPException(409, "采购单不在待审批状态")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 需为 approve 或 reject")
    if body.decision == "reject" and not body.reason.strip():
        raise HTTPException(400, "驳回需填写原因")
    meaning = "我批准按此金额向该供应商采购" if body.decision == "approve" else "我驳回此采购单"
    po.approval = {**(po.approval or {}), "decision": body.decision, "by": u.id, "at": _now().isoformat(), "meaning": meaning,
                   "reason": body.reason, "self": po.created_by == u.id}
    if po.status == "pending_approval":
        po.status = "approved" if body.decision == "approve" else "rejected"
    elif body.decision == "reject":  # 补录采购已发生：驳回不能撤销实物，转为待跟进例外
        _add_exception(db, po, u, "事后审批被驳回", body.reason)
        t = Task(title=f"事后审批被驳回：{po.code} {po.supplier}", kind="exception", source="po", owner_id=po.owner_id,
                 target_type="po", target_id=po.id, description=body.reason, created_by=u.id)
        db.add(t)
    _bump(po)
    notify(db, po.owner_id, f"采购单 {po.code} {'已批准' if body.decision == 'approve' else '被驳回'}", "/purchasing")
    audit(db, u, f"po.{body.decision}d", "po", po.id, reason=body.reason, signature_meaning=meaning)
    db.commit()
    return po_out(db, po, u)


@router.post("/pos/{pid}/order")
def mark_ordered(pid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    po = get_or_404(db, PurchaseOrder, pid)
    if po.status != "approved":
        raise HTTPException(409, "只有已批准的采购单可以下单")
    po.status = "ordered"
    _bump(po)
    audit(db, u, "po.ordered", "po", po.id)
    db.commit()
    return po_out(db, po, u)


class ReasonIn(BaseModel):
    reason: str


@router.post("/pos/{pid}/close")
def close_short(pid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """按短收关闭：剩余未到数量不再等待。"""
    require(u, "po.manage")
    po = get_or_404(db, PurchaseOrder, pid)
    if po.status not in ("ordered", "partial"):
        raise HTTPException(409, "只有已下单或部分到货的采购单可以关闭")
    if not body.reason.strip():
        raise HTTPException(400, "关闭需填写原因")
    if db.query(Receipt).filter_by(po_id=pid, status="submitted").count():
        raise HTTPException(409, "还有待入账的收货单，请先入账或作废")
    po.status, po.close_reason = "closed", body.reason
    _bump(po)
    audit(db, u, "po.closed_short", "po", po.id, reason=body.reason)
    db.commit()
    return po_out(db, po, u)


@router.post("/pos/{pid}/cancel")
def cancel_po(pid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "po.manage")
    po = get_or_404(db, PurchaseOrder, pid)
    if db.query(Receipt).filter_by(po_id=pid, status="posted").count():
        raise HTTPException(409, "已有入账收货，不能取消；请按短收关闭")
    if po.status in ("closed", "cancelled", "received"):
        raise HTTPException(409, "采购单已结束")
    if not body.reason.strip():
        raise HTTPException(400, "取消需填写原因")
    for r in db.query(Receipt).filter_by(po_id=pid, status="submitted"):
        r.status = "voided"
    po.status, po.close_reason = "cancelled", body.reason
    _bump(po)
    audit(db, u, "po.cancelled", "po", po.id, reason=body.reason)
    db.commit()
    return po_out(db, po, u)


# ---------- receiving ----------
class RLine(BaseModel):
    po_line_id: str
    qty: float = 0
    rejected: float = 0
    reject_reason: str = ""
    lot_code: str | None = None
    expiry: str | None = None
    counted: bool = True


class ReceiptIn(BaseModel):
    lines: list[RLine]
    note: str = ""
    over_reason: str = ""


@router.post("/pos/{pid}/receipts")
def submit_receipt(pid: str, body: ReceiptIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """现场提交收货（不改库存）。入账需签收单 + 仓管签名。"""
    require(u, "po.receive")
    po = get_or_404(db, PurchaseOrder, pid)
    if po.status not in ("ordered", "partial"):
        raise HTTPException(409, "采购单尚未下单或已结束，不能收货")
    lines = {l.id: l for l in db.query(POLine).filter_by(po_id=pid)}
    pending = {}
    for r in db.query(Receipt).filter_by(po_id=pid, status="submitted"):
        for rl in r.lines:
            pending[rl["po_line_id"]] = pending.get(rl["po_line_id"], 0) + rl["qty"]
    clean = []
    for rl in body.lines:
        l = lines.get(rl.po_line_id)
        if not l:
            raise HTTPException(400, "收货行不属于此采购单")
        if rl.qty < 0 or rl.rejected < 0:
            raise HTTPException(400, "数量不能为负")
        if rl.qty == 0 and rl.rejected == 0:
            continue
        if rl.rejected > 0 and not rl.reject_reason.strip():
            raise HTTPException(400, f"「{l.item_name}」拒收需填写原因")
        if l.sku_id and rl.qty > 0 and not (rl.lot_code or "").strip():
            sku = db.get(Sku, l.sku_id)
            if sku and sku.category in LOT_REQUIRED_CATEGORIES:
                raise HTTPException(400, f"「{l.item_name}」是{sku.category}，需填写 Lot（追溯用）")
        open_qty = l.qty_ordered - l.qty_received - pending.get(l.id, 0)
        if rl.qty > open_qty + 1e-9 and not body.over_reason.strip():
            raise HTTPException(409, f"「{l.item_name}」实收 {rl.qty} 超过未到数量 {round(open_qty, 3)}；如确需超收请填写原因")
        clean.append(rl.model_dump())
    if not clean:
        raise HTTPException(400, "没有填写任何收货数量")
    r = Receipt(code=_code(db, Receipt, "RC"), po_id=pid, warehouse_id=po.warehouse_id, lines=clean,
                note=(body.note + (f"\n超收原因：{body.over_reason}" if body.over_reason else "")).strip(), submitted_by=u.id)
    db.add(r)
    db.flush()
    for w in db.query(User).filter(User.role.in_(["warehouse", "ops_manager"]), User.active, User.id != u.id):
        notify(db, w.id, f"收货待入账：{po.code} {r.code}", "/purchasing", dedup_key=f"rcpost:{r.id}")
    audit(db, u, "receipt.submitted", "receipt", r.id, po=po.code)
    db.commit()
    return {"id": r.id, "code": r.code}


class PostIn(BaseModel):
    exception_reason: str = ""


@router.post("/receipts/{rid}/post")
def post_receipt(rid: str, body: PostIn | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """仓管签名入账：需要签收单证据；按行写库存流水（幂等），拒收生成差异任务。"""
    require(u, "inventory.post")
    r = get_or_404(db, Receipt, rid, "收货单不存在")
    po = db.get(PurchaseOrder, r.po_id)
    if r.status == "posted":
        return {"ok": True, "duplicate": True, "po": po_out(db, po, u)}
    if r.status != "submitted":
        raise HTTPException(409, "收货单已作废")
    if not _ev_any(db, "receipt", r.id, SIGNOFF_PURPOSES):
        if not (body and body.exception_reason.strip()):
            raise HTTPException(409, "入账前需要签收依据（签收单、收据、到货照片任一）；确实没有时可填写例外原因后入账")
        _add_exception(db, po, u, f"收货 {r.code} 无签收依据", body.exception_reason)
    try:
        posting = begin_posting(db, u, f"receipt:{r.id}", "receive", "receipt", r.id, f"{po.code} {r.code}")
    except Dup:
        return {"ok": True, "duplicate": True, "po": po_out(db, po, u)}
    lines = {l.id: l for l in db.query(POLine).filter_by(po_id=po.id)}
    rejected = []
    for rl in r.lines:
        l = lines[rl["po_line_id"]]
        if l.sku_id and rl["qty"] > 0:
            if not r.warehouse_id:
                raise HTTPException(409, "采购单未指定收货仓库")
            lot = get_or_create_lot(db, l.sku_id, rl.get("lot_code"), rl.get("expiry"))
            txn(db, posting, l.sku_id, r.warehouse_id, "on_hand" if rl.get("counted", True) else "unverified", rl["qty"], lot.id if lot else None)
        l.qty_received += rl["qty"]
        l.qty_rejected += rl.get("rejected", 0)
        if rl.get("rejected", 0) > 0:
            rejected.append(f"{l.item_name} 拒收 {rl['rejected']}（{rl.get('reject_reason', '')}）")
    r.status, r.posted_by, r.posted_at, r.posting_id = "posted", u.id, _now(), posting.id
    r.signature_meaning = "我已核对实物与签收单，确认入账"
    all_in = all(l.qty_received >= l.qty_ordered - 1e-9 for l in lines.values())
    po.status = "received" if all_in else "partial"
    _bump(po)
    if rejected:
        t = Task(title=f"采购收货差异：{po.code} {po.supplier}", kind="exception", source="receipt", requires_evidence=True,
                 description="；".join(rejected), owner_id=po.owner_id, target_type="po", target_id=po.id,
                 done_criteria="与供应商确认退换/补发/扣款，并上传沟通或单据", created_by=u.id)
        db.add(t)
        db.flush()
        notify(db, po.owner_id, t.title, f"/t/{t.id}")
    audit(db, u, "receipt.posted", "receipt", r.id, po=po.code, posting=posting.id, self_posted=r.submitted_by == u.id,
          signature_meaning=r.signature_meaning)
    db.commit()
    return {"ok": True, "duplicate": False, "po": po_out(db, po, u)}


@router.post("/receipts/{rid}/void")
def void_receipt(rid: str, body: ReasonIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Receipt, rid)
    if r.status != "submitted":
        raise HTTPException(409, "只有未入账的收货单可以作废；已入账请走库存冲销")
    if not (can(u, "inventory.post") or r.submitted_by == u.id):
        raise HTTPException(403, "无权作废")
    if not body.reason.strip():
        raise HTTPException(400, "作废需填写原因")
    r.status, r.note = "voided", (r.note + f"\n作废：{body.reason}").strip()
    audit(db, u, "receipt.voided", "receipt", r.id, reason=body.reason)
    db.commit()
    return {"ok": True}


# ---------- invoice & payment ----------
class InvoiceIn(BaseModel):
    doc_type: str = "invoice"  # invoice | receipt | none
    invoice_no: str = ""
    invoice_amount: float
    reason: str = ""  # 无票据时必填


@router.post("/pos/{pid}/invoice")
def set_invoice(pid: str, body: InvoiceIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not (can(u, "po.manage") or can(u, "finance.reconcile")):
        raise HTTPException(403, "无权登记发票")
    po = get_or_404(db, PurchaseOrder, pid)
    if po.payment_status == "reconciled":
        raise HTTPException(409, "已核对完成，不能修改票据")
    if body.doc_type not in DOC_LABEL:
        raise HTTPException(400, "票据类型需为 发票 / 收据 / 无票据")
    if body.invoice_amount < 0:
        raise HTTPException(400, "金额不能为负")
    if body.doc_type == "invoice" and not body.invoice_no.strip():
        raise HTTPException(400, "发票需要发票号（收据可不填）")
    if body.doc_type == "none":
        _add_exception(db, po, u, "无票据", body.reason)
    po.doc_type, po.invoice_no, po.invoice_amount = body.doc_type, body.invoice_no.strip(), body.invoice_amount
    if po.payment_status == "none":
        po.payment_status = "invoiced"
    _bump(po)
    audit(db, u, "po.invoice", "po", po.id, doc_type=body.doc_type, invoice_no=po.invoice_no, amount=body.invoice_amount)
    db.commit()
    return po_out(db, po, u)


class PaymentIn(BaseModel):
    status: str  # paid_pending | reconciled
    note: str = ""
    exception_reason: str = ""  # 没有付款凭证（如现金）时填写


@router.post("/pos/{pid}/payment")
def set_payment(pid: str, body: PaymentIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    po = get_or_404(db, PurchaseOrder, pid)
    _, payable, match = _totals(po, db.query(POLine).filter_by(po_id=pid).all())
    if body.status == "paid_pending":
        if not (can(u, "po.manage") or can(u, "finance.reconcile")):
            raise HTTPException(403, "无权登记付款")
        if not _ev_any(db, "po", po.id, PAY_PURPOSES):
            if not body.exception_reason.strip():
                raise HTTPException(409, "请上传付款证明或收据；确实没有凭证（如现金）时填写例外原因")
            _add_exception(db, po, u, "付款无凭证", body.exception_reason)
        po.payment_status = "paid_pending"
    elif body.status == "reconciled":
        require(u, "finance.reconcile")
        if po.invoice_amount is None:
            raise HTTPException(409, "核对前需要先登记票据金额（发票、收据或无票据）")
        if po.doc_type != "none" and not _ev_any(db, "po", po.id, DOC_PURPOSES[po.doc_type]):
            raise HTTPException(409, f"核对前需要上传{DOC_LABEL[po.doc_type]}；如确实没有，请把票据类型改为“无票据”并写明原因")
        if po.payment_status != "paid_pending":
            raise HTTPException(409, "需先登记付款（上传付款证明）")
        if match and not match["ok"] and not body.note.strip():
            raise HTTPException(409, f"发票金额与应付参考额相差 ${match['diff']:,.2f}，需填写差异说明才能核对")
        po.payment_status = "reconciled"
        po.reconcile = {"by": u.id, "at": _now().isoformat(), "meaning": "我已核对发票、收货与付款", "note": body.note,
                        "diff": match["diff"] if match else None}
    else:
        raise HTTPException(400, "status 需为 paid_pending 或 reconciled")
    _bump(po)
    audit(db, u, f"po.payment_{body.status}", "po", po.id, note=body.note, payable=payable)
    db.commit()
    return po_out(db, po, u)


# ---------- quick purchase (补录已完成的采购：日常用品、门店现买、紧急采购) ----------
class QuickLine(BaseModel):
    sku_id: str | None = None
    item_name: str | None = None
    unit: str | None = None
    qty: float
    unit_price: float = 0
    lot_code: str | None = None


class QuickIn(BaseModel):
    supplier: str
    category: str = "日常用品"
    warehouse_id: str | None = None
    lines: list[QuickLine]
    tax: float = 0
    freight: float = 0
    paid: bool = True
    doc_type: str = "receipt"
    doc_no: str = ""
    doc_amount: float | None = None
    reason: str = ""  # 无票据时必填
    note: str = ""
    purchase_date: str | None = None
    payment_method: str = ""
    paid_by: str = ""


PAYMETHOD_LABEL = {"company_card": "公司卡", "cash": "现金", "personal": "个人垫付", "check": "支票", "transfer": "转账", "other": "其他", "": ""}


@router.post("/pos/quick")
def quick_purchase(body: QuickIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """一步补录：货已到手、钱多半已付。库存物料直接入账（实物为准）；超额度的进入“事后审批”。票据照片随后上传。"""
    r = do_quick_purchase(db, u, body)
    db.commit()
    return r


def do_quick_purchase(db: Session, u: User, body: QuickIn) -> dict:
    require(u, "po.manage")
    if body.payment_method not in PAYMETHOD_LABEL:
        raise HTTPException(400, f"付款方式需为 {' / '.join(k for k in PAYMETHOD_LABEL if k)}")
    if body.payment_method == "personal" and not body.paid_by.strip():
        raise HTTPException(400, "个人垫付请写是谁付的（以后报销用）")
    if body.category not in CATEGORIES or body.doc_type not in DOC_LABEL:
        raise HTTPException(400, "分类或票据类型无效")
    po = PurchaseOrder(code=_code(db, PurchaseOrder, "PO"), supplier=body.supplier, category=body.category,
                       warehouse_id=body.warehouse_id or _default_wh(db), tax=body.tax, freight=body.freight, quick=True,
                       note=("补录已完成采购。" + body.note).strip(), owner_id=u.id, created_by=u.id, status="received",
                       doc_type=body.doc_type, invoice_no=body.doc_no, purchase_date=(body.purchase_date or "")[:10] or None,
                       payment_method=body.payment_method, paid_by=body.paid_by.strip())
    db.add(po)
    db.flush()
    _set_lines(db, po, [POLineIn(sku_id=l.sku_id, item_name=l.item_name, unit=l.unit, qty_ordered=l.qty, unit_price=l.unit_price)
                        for l in body.lines])
    db.flush()
    lines = db.query(POLine).filter_by(po_id=po.id).order_by(POLine.sort).all()
    total, _, _ = _totals(po, lines)
    po.invoice_amount = body.doc_amount if body.doc_amount is not None else total
    if body.doc_type == "none":
        _add_exception(db, po, u, "无票据", body.reason)
    limit = approval_limit(db)
    po.approval = {"required": total > limit, "limit": limit, "total": total, "after_the_fact": total > limit}
    r = Receipt(code=_code(db, Receipt, "RC"), po_id=po.id, warehouse_id=po.warehouse_id, status="posted", submitted_by=u.id,
                posted_by=u.id, posted_at=_now(), note="补录采购自动收货", signature_meaning="补录：货已到手",
                lines=[{"po_line_id": l.id, "qty": l.qty_ordered, "rejected": 0, "lot_code": bl.lot_code, "counted": True}
                       for l, bl in zip(lines, body.lines)])
    db.add(r)
    db.flush()
    if any(l.sku_id for l in lines):
        posting = begin_posting(db, u, f"receipt:{r.id}", "receive", "receipt", r.id, f"{po.code} 补录")
        for l, bl in zip(lines, body.lines):
            if l.sku_id:
                lot = get_or_create_lot(db, l.sku_id, bl.lot_code)
                txn(db, posting, l.sku_id, po.warehouse_id, "on_hand", l.qty_ordered, lot.id if lot else None)
        r.posting_id = posting.id
    for l in lines:
        l.qty_received = l.qty_ordered
    po.payment_status = "paid_pending" if body.paid else "invoiced"
    if total > limit:
        for b in db.query(User).filter_by(role="boss", active=True):
            notify(db, b.id, f"补录采购需事后审批：{po.code} {po.supplier} ${total:,.2f}", "/purchasing", dedup_key=f"poappr:{po.id}")
    audit(db, u, "po.quick", "po", po.id, total=total, after_the_fact_approval=total > limit, doc_type=body.doc_type,
          payment=body.payment_method or None, paid_by=body.paid_by or None)
    return {"id": po.id, "code": po.code, "total": total, "needs_approval": total > limit}
