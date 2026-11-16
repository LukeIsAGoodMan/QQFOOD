"""历史业务补录 API（只有老板 / 系统管理员）。逻辑在 app/backfill.py。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import backfill as bf
from ..core import current_user, get_or_404
from ..db import get_db
from ..models import HistoricalBackfill, User

router = APIRouter(prefix="/api/backfill", tags=["backfill"])


class BackfillIn(BaseModel):
    kind: str
    business_date: str
    supplier: str = ""
    doc_no: str = ""
    container_no: str = ""
    goods_status: str = "arrived"
    warehouse_id: str | None = None
    from_warehouse_id: str | None = None
    payment_method: str = ""
    paid_by: str = ""
    total_amount: float | None = None
    reason: str = ""
    note: str = ""
    links: list[dict] = []
    lines: list[dict]


class ConfirmIn(BaseModel):
    version: int
    acknowledged_lines: list[str] = []  # 疑似已记过、人逐行核对后确认“确实没记过”的行
    ack_duplicates: bool = False
    negative_reason: str = ""            # 例外：账面不够扣时按负数记的原因


class ReviseIn(ConfirmIn):
    changes: dict
    reason: str = ""
    preview_only: bool = False


class VoidIn(BaseModel):
    version: int
    reason: str = ""
    preview_only: bool = False


@router.get("/meta")
def meta(u: User = Depends(current_user)):
    bf.require_admin(u)
    return {"kinds": bf.KINDS, "item_types": {k: v for k, v in bf.ITEM_TYPES.items()}, "goods_status": bf.GOODS_STATUS,
            "pay": bf.PAY, "link_types": bf.LINK_TYPES, "direction": bf.DIRECTION}


@router.get("")
def list_(status: str | None = None, q: str = "", u: User = Depends(current_user), db: Session = Depends(get_db)):
    bf.require_admin(u)
    qs = db.query(HistoricalBackfill).order_by(HistoricalBackfill.created_at.desc())
    if status:
        qs = qs.filter_by(status=status)
    n = q.strip().lower()
    rows = []
    for r in qs.limit(300):
        if n and n not in f"{r.code} {r.supplier} {r.doc_no} {r.container_no} {r.reason} {' '.join(l.get('name', '') for l in r.lines)}".lower():
            continue
        rows.append({"id": r.id, "code": r.code, "kind": r.kind, "kind_label": bf.KINDS[r.kind], "status": r.status,
                     "business_date": r.business_date, "supplier": r.supplier, "doc_no": r.doc_no, "container_no": r.container_no,
                     "lines": len(r.lines), "posted_lines": len({e["line_id"] for e in r.ledger or []}), "total_amount": r.total_amount,
                     "pending": bool(r.pending), "source": r.source, "created_at": r.created_at})
    return rows


@router.post("/preview")
def preview_new(body: BackfillIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """还没保存的补录单：先看每行库存影响。"""
    bf.require_admin(u)
    h = bf.clean_header(db, body.model_dump())
    h["links"] = [bf.resolve_link(db, x) for x in body.links]
    lines = bf.clean_lines(db, h["kind"], body.lines)
    return {"plan": bf.plan(db, None, h, lines), "lines": lines}


@router.post("")
def create(body: BackfillIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = bf.create(db, u, body.model_dump())
    db.commit()
    return bf.out(db, rec)


@router.get("/{bid}")
def get(bid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    bf.require_admin(u)
    return bf.out(db, _get(db, bid))


def _get(db, bid) -> HistoricalBackfill:
    r = db.get(HistoricalBackfill, bid) or db.query(HistoricalBackfill).filter_by(code=bid.strip().upper()).first()
    return r or get_or_404(db, HistoricalBackfill, bid, "补录单不存在")


@router.post("/{bid}/confirm")
def confirm(bid: str, body: ConfirmIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = _get(db, bid)
    r = bf.confirm(db, u, rec, body.version, body.acknowledged_lines, body.ack_duplicates, body.negative_reason)
    db.commit()
    return {**r, "record": bf.out(db, rec)}


@router.post("/{bid}/revise")
def revise(bid: str, body: ReviseIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = _get(db, bid)
    r = bf.revise(db, u, rec, body.changes, body.reason, body.version, body.acknowledged_lines, body.ack_duplicates,
                  body.negative_reason, body.preview_only)
    if body.preview_only:
        db.rollback()
        return r
    db.commit()
    return {**r, "record": bf.out(db, rec)}


@router.post("/{bid}/void")
def void(bid: str, body: VoidIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = _get(db, bid)
    r = bf.void(db, u, rec, body.reason, body.version, body.preview_only)
    if body.preview_only:
        db.rollback()
        return r
    db.commit()
    return {**r, "record": bf.out(db, rec)}


@router.post("/{bid}/pending/apply")
def apply_pending(bid: str, body: ConfirmIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    rec = _get(db, bid)
    bf.require_admin(u)
    r = bf.apply_pending(db, u, rec, body.version, body.acknowledged_lines, body.ack_duplicates, body.negative_reason)
    db.commit()
    return {**r, "record": bf.out(db, rec)}


@router.post("/{bid}/pending/preview")
def preview_pending(bid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    bf.require_admin(u)
    rec = _get(db, bid)
    pd = rec.pending or {}
    if not pd:
        return {"plan": None}
    if pd["void"]:
        r = bf.void(db, u, rec, pd["reason"], rec.version, preview_only=True)
    else:
        r = bf.revise(db, u, rec, pd["changes"], pd["reason"], rec.version, preview_only=True)
    db.rollback()
    return r


@router.post("/{bid}/pending/discard")
def discard_pending(bid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    bf.require_admin(u)
    rec = _get(db, bid)
    if rec.pending:
        bf._log(rec, u, "change_discarded", rec.pending.get("reason", ""))
        rec.pending = {}
        db.commit()
    return bf.out(db, rec)
