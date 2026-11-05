"""批量导入（PRD §9.11 AGT-09 / ING）：ChatGPT 或人工一次提交多行 → 系统逐行比对（新增/修改/无变化/疑似重复/错误）
→ 人勾选确认后应用。库存数字只有在人确认后才写入流水。

kinds
- sku_upsert   商品主档：编码、名称、分类、基本单位、单位换算、条码、各仓安全库存
- stock_count  期初 / 盘点：SKU × 仓库 × Lot 的数量；实盘写入实物、估计写入“未核数”
- inbound      公司调入（多伦多/温哥华等）：一张调入单，确认后记为在途
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, require
from ..db import get_db
from ..models import BatchProposal, InventoryTxn, Lot, SafetyStock, Sku, User, Warehouse
from .inventory import Dup, TLineIn, TransferIn, balance, begin_posting, create_transfer, get_or_create_lot, txn

router = APIRouter(prefix="/api/batches", tags=["batches"])

SITE_TZ = ZoneInfo("America/Los_Angeles")
SKU_CATEGORIES = ["原料", "包材", "成品", "耗材"]
KIND_LABEL = {"sku_upsert": "商品主档", "stock_count": "期初 / 盘点数量", "inbound": "公司调入"}
KIND_PERM = {"sku_upsert": "inventory.master", "stock_count": "inventory.post", "inbound": "inventory.transfer"}
MAX_ROWS = 300


def _now():
    return datetime.now(timezone.utc)


def _norm(s) -> str:
    return "".join(str(s or "").lower().split())


# ---------- analysis (pure: never writes) ----------
def _analyze_sku(db: Session, rows: list[dict]):
    skus = {s.code: s for s in db.query(Sku)}
    by_name = {_norm(s.name): s for s in skus.values()}
    by_barcode = {s.barcode: s for s in skus.values() if s.barcode}
    whs = {w.code: w for w in db.query(Warehouse)}
    seen, out = set(), []
    for r in rows:
        code = str(r.get("code") or "").strip().upper()
        res = {"key": code, "action": "error", "messages": [], "changes": {}}
        if not code:
            res["messages"].append("缺少编码")
        elif code in seen:
            res["messages"].append("本批重复的编码")
        seen.add(code)
        cat = r.get("category") or ("原料" if code not in skus else skus[code].category)
        if cat not in SKU_CATEGORIES:
            res["messages"].append(f"分类需为 {'/'.join(SKU_CATEGORIES)}")
        conv = r.get("conversions") or {}
        if not isinstance(conv, dict) or any(not isinstance(v, (int, float)) or v <= 0 for v in conv.values()):
            res["messages"].append("单位换算需为 {单位: 每个基本单位等于多少} 且大于 0")
            conv = {}
        for wc in (r.get("safety_stock") or {}):
            w = whs.get(str(wc).upper())
            if not w or not w.tracked:
                res["messages"].append(f"安全库存的仓库 {wc} 不存在或不管账")
        if res["messages"]:
            out.append(res)
            continue
        cur = skus.get(code)
        if cur:
            want = {"name": r.get("name") or cur.name, "category": cat, "base_unit": r.get("base_unit") or cur.base_unit,
                    "conversions": {**(cur.conversions or {}), **conv}, "barcode": r.get("barcode") if r.get("barcode") is not None else cur.barcode}
            for k, v in want.items():
                if getattr(cur, k) != v:
                    res["changes"][k] = [getattr(cur, k), v]
            if "base_unit" in res["changes"] and db.query(func.count(InventoryTxn.id)).filter_by(sku_id=cur.id).scalar():
                res["action"], res["messages"] = "error", ["已有库存流水，不能改基本单位（请新建 SKU）"]
                out.append(res)
                continue
            for wc, q in (r.get("safety_stock") or {}).items():
                w = whs[str(wc).upper()]
                ss = db.query(SafetyStock).filter_by(sku_id=cur.id, warehouse_id=w.id).first()
                if not ss or ss.qty != q:
                    res["changes"][f"安全库存@{w.code}"] = [ss.qty if ss else None, q]
            res["action"] = "update" if res["changes"] else "unchanged"
        else:
            if not r.get("name"):
                res["messages"].append("新 SKU 需要名称")
                out.append(res)
                continue
            twin = by_name.get(_norm(r.get("name"))) or (by_barcode.get(r.get("barcode")) if r.get("barcode") else None)
            res["action"] = "duplicate" if twin else "new"
            if twin:
                res["messages"].append(f"疑似与已有 {twin.code} {twin.name} 重复（默认不勾选）")
            res["changes"] = {k: [None, v] for k, v in {"name": r.get("name"), "category": cat, "base_unit": r.get("base_unit") or "箱",
                                                         "conversions": conv, "barcode": r.get("barcode")}.items() if v not in (None, {}, "")}
            for wc, q in (r.get("safety_stock") or {}).items():
                res["changes"][f"安全库存@{str(wc).upper()}"] = [None, q]
        out.append(res)
    return out


def _analyze_stock(db: Session, rows: list[dict]):
    skus = {s.code: s for s in db.query(Sku)}
    whs = {w.code: w for w in db.query(Warehouse)}
    out, seen = [], set()
    for r in rows:
        sc = str(r.get("sku_code") or "").strip().upper()
        wc = str(r.get("warehouse_code") or "WH597").strip().upper()
        lot_code = (str(r.get("lot_code")).strip() if r.get("lot_code") else None)
        res = {"key": f"{sc} @ {wc}" + (f" · {lot_code}" if lot_code else ""), "action": "error", "messages": [], "changes": {}}
        sku, w = skus.get(sc), whs.get(wc)
        if not sku:
            res["messages"].append(f"SKU {sc} 不存在（请先导入商品主档）")
        if not w:
            res["messages"].append(f"仓库 {wc} 不存在")
        elif not w.tracked:
            res["messages"].append(f"{w.name} 不在本系统管账，不能录库存")
        try:
            qty = float(r.get("qty"))
            if qty < 0:
                raise ValueError
        except (TypeError, ValueError):
            res["messages"].append("数量需为不小于 0 的数字")
            qty = None
        unit = r.get("unit")
        if sku and qty is not None and unit and unit != sku.base_unit:
            per = (sku.conversions or {}).get(unit)
            if not per:
                res["messages"].append(f"{sku.code} 未定义 {unit} 与 {sku.base_unit} 的换算")
            else:
                qty = round(qty / float(per), 4)
        if (sc, wc, lot_code) in seen:
            res["messages"].append("本批重复（同 SKU、仓库、Lot）")
        seen.add((sc, wc, lot_code))
        if res["messages"]:
            out.append(res)
            continue
        lot = db.query(Lot).filter_by(sku_id=sku.id, lot_code=lot_code).first() if lot_code else None
        lot_id = lot.id if lot else (None if not lot_code else "__new__")
        counted = r.get("counted", True) is not False
        cur_on = balance(db, sku.id, w.id, "on_hand", None if lot_id in (None, "__new__") else lot_id) if lot_id != "__new__" else 0.0
        cur_unv = balance(db, sku.id, w.id, "unverified", None if lot_id in (None, "__new__") else lot_id) if lot_id != "__new__" else 0.0
        if counted:
            if cur_on != qty:
                res["changes"]["实物"] = [cur_on, qty]
            if cur_unv:
                res["changes"]["未核数"] = [cur_unv, 0]
        elif cur_unv != qty:
            res["changes"]["未核数（估计）"] = [cur_unv, qty]
        res.update({"action": "set" if res["changes"] else "unchanged", "unit": sku.base_unit, "qty_base": qty, "counted": counted})
        if not lot_code and sku.category in ("原料", "包材", "成品"):
            res["messages"].append("无 Lot（可确认，追溯时会缺批次）")
        out.append(res)
    return out


def _analyze_inbound(db: Session, meta: dict, rows: list[dict]):
    skus = {s.code: s for s in db.query(Sku)}
    whs = {w.code: w for w in db.query(Warehouse)}
    src, dst = whs.get(str(meta.get("from_code", "")).upper()), whs.get(str(meta.get("to_code") or "WH597").upper())
    header = []
    if not src:
        header.append(f"来源地 {meta.get('from_code')} 不存在")
    if not dst or not dst.tracked:
        header.append("目的仓不存在或不管账")
    out = []
    for r in rows:
        sc = str(r.get("sku_code") or "").strip().upper()
        res = {"key": f"{sc}" + (f" · {r.get('lot_code')}" if r.get("lot_code") else ""), "action": "error", "messages": list(header), "changes": {}}
        sku = skus.get(sc)
        if not sku:
            res["messages"].append(f"SKU {sc} 不存在（请先导入商品主档）")
        try:
            qty = float(r.get("qty"))
            if qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            res["messages"].append("数量需大于 0")
            qty = None
        if sku and qty and r.get("unit") and r["unit"] != sku.base_unit:
            per = (sku.conversions or {}).get(r["unit"])
            if not per:
                res["messages"].append(f"未定义 {r['unit']} 换算")
            else:
                qty = round(qty / float(per), 4)
        if res["messages"]:
            out.append(res)
            continue
        res.update({"action": "new", "qty_base": qty, "unit": sku.base_unit, "changes": {"在途": [None, qty]}})
        out.append(res)
    return out, src, dst


def analyze(db: Session, b: BatchProposal):
    if b.kind == "sku_upsert":
        return _analyze_sku(db, b.rows)
    if b.kind == "stock_count":
        return _analyze_stock(db, b.rows)
    return _analyze_inbound(db, b.meta, b.rows)[0]


def summary(an: list[dict]) -> dict:
    s = {}
    for a in an:
        s[a["action"]] = s.get(a["action"], 0) + 1
    return s


def batch_out(db: Session, b: BatchProposal, names=None, detail=True):
    names = names or {x.id: x.display_name for x in db.query(User).all()}
    d = {"id": b.id, "code": b.code, "kind": b.kind, "kind_label": KIND_LABEL[b.kind], "status": b.status, "source": b.source,
         "source_note": b.source_note, "meta": b.meta, "rows_count": len(b.rows), "created_by": names.get(b.created_by),
         "created_at": b.created_at, "decided_by": names.get(b.decided_by), "decided_at": b.decided_at, "result": b.result}
    if detail or b.status == "proposed":
        an = analyze(db, b) if b.status == "proposed" else b.result.get("analysis", [])
        d["summary"] = summary(an)
        if detail:
            d["rows"] = [{"index": i, "input": r, **a} for i, (r, a) in enumerate(zip(b.rows, an))]
    return d


# ---------- create ----------
class BatchIn(BaseModel):
    kind: str
    rows: list[dict]
    meta: dict = {}
    source_note: str = ""


def create_batch(db: Session, u: User, body: BatchIn, source: str = "manual") -> BatchProposal:
    if body.kind not in KIND_LABEL:
        raise HTTPException(400, "kind 需为 sku_upsert / stock_count / inbound")
    require(u, KIND_PERM[body.kind])
    if not body.rows:
        raise HTTPException(400, "没有任何行")
    if len(body.rows) > MAX_ROWS:
        raise HTTPException(400, f"每批最多 {MAX_ROWS} 行，请分批提交")
    n = db.query(func.count(BatchProposal.id)).scalar() + 1
    b = BatchProposal(code=f"BT-{datetime.now(SITE_TZ).strftime('%y%m%d')}-{n:03d}", kind=body.kind, meta=body.meta, rows=body.rows,
                      source=source, source_note=body.source_note, created_by=u.id)
    db.add(b)
    db.flush()
    audit(db, u, "batch.proposed", "batch", b.id, kind=body.kind, rows=len(body.rows), source=source)
    return b


@router.post("")
def create(body: BatchIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    b = create_batch(db, u, body)
    db.commit()
    return batch_out(db, b)


@router.get("")
def list_batches(status: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(BatchProposal)
    if status:
        q = q.filter(BatchProposal.status.in_(status.split(",")))
    names = {x.id: x.display_name for x in db.query(User).all()}
    return [batch_out(db, b, names, detail=False) for b in q.order_by(BatchProposal.created_at.desc()).limit(100)]


@router.get("/{bid}")
def get_batch(bid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return batch_out(db, get_or_404(db, BatchProposal, bid, "批量提议不存在"))


# ---------- apply ----------
class ApplyIn(BaseModel):
    selected: list[int]
    note: str = ""


@router.post("/{bid}/apply")
def apply(bid: str, body: ApplyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    b = get_or_404(db, BatchProposal, bid)
    require(u, KIND_PERM[b.kind])
    if b.status != "proposed":
        raise HTTPException(409, "该批次已处理")
    an = analyze(db, b)  # re-validate against current data
    chosen = [i for i in sorted(set(body.selected)) if 0 <= i < len(b.rows)]
    applied, skipped = [], []
    if b.kind == "sku_upsert":
        whs = {w.code: w for w in db.query(Warehouse)}
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] not in ("new", "update", "duplicate"):
                skipped.append(i)
                continue
            code = a["key"]
            sku = db.query(Sku).filter_by(code=code).first()
            if not sku:
                sku = Sku(code=code, name=r["name"], category=r.get("category") or "原料", base_unit=r.get("base_unit") or "箱",
                          conversions=r.get("conversions") or {}, barcode=r.get("barcode"))
                db.add(sku)
                db.flush()
            else:
                ch = a["changes"]
                for k in ("name", "category", "base_unit", "conversions", "barcode"):
                    if k in ch:
                        setattr(sku, k, ch[k][1])
            for wc, q in (r.get("safety_stock") or {}).items():
                w = whs[str(wc).upper()]
                ss = db.query(SafetyStock).filter_by(sku_id=sku.id, warehouse_id=w.id).first()
                if ss:
                    ss.qty = q
                else:
                    db.add(SafetyStock(sku_id=sku.id, warehouse_id=w.id, qty=q))
            applied.append(i)
    elif b.kind == "stock_count":
        skus = {s.code: s for s in db.query(Sku)}
        whs = {w.code: w for w in db.query(Warehouse)}
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] != "set":
                skipped.append(i)
                continue
            sku = skus[str(r["sku_code"]).strip().upper()]
            w = whs[str(r.get("warehouse_code") or "WH597").strip().upper()]
            lot = get_or_create_lot(db, sku.id, r.get("lot_code"))
            lot_id = lot.id if lot else None
            try:
                p = begin_posting(db, u, f"batch:{b.id}:{i}", "count", "batch", b.id, f"{b.code} {KIND_LABEL[b.kind]} {r.get('note') or ''}".strip())
            except Dup:
                skipped.append(i)
                continue
            on, unv = balance(db, sku.id, w.id, "on_hand", lot_id), balance(db, sku.id, w.id, "unverified", lot_id)
            if a["counted"]:
                txn(db, p, sku.id, w.id, "unverified", -unv, lot_id)
                txn(db, p, sku.id, w.id, "on_hand", a["qty_base"] - on, lot_id)
            else:
                txn(db, p, sku.id, w.id, "unverified", a["qty_base"] - unv, lot_id)
            applied.append(i)
    else:  # inbound → one transfer with the chosen lines
        lines = []
        skus = {s.code: s for s in db.query(Sku)}
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] != "new":
                skipped.append(i)
                continue
            lines.append(TLineIn(sku_id=skus[str(r["sku_code"]).strip().upper()].id, lot_code=r.get("lot_code"), expiry=r.get("expiry"), qty=a["qty_base"]))
            applied.append(i)
        _, src, dst = _analyze_inbound(db, b.meta, b.rows)
        if lines:
            tr = create_transfer(TransferIn(from_wh=src.id, to_wh=dst.id, expected_date=b.meta.get("expected_date"),
                                            note=f"{b.code} {b.meta.get('note') or ''}".strip(), lines=lines, shipped_now=True), u, db)
            b.result = {**(b.result or {}), "transfer_id": tr["id"], "transfer_code": tr["code"]}
    b.status = "applied" if len(applied) == len(b.rows) else ("partial" if applied else "rejected")
    b.decided_by, b.decided_at = u.id, _now()
    b.result = {**(b.result or {}), "applied": applied, "skipped": skipped, "note": body.note,
                "analysis": an}
    audit(db, u, "batch.applied", "batch", b.id, kind=b.kind, applied=len(applied), skipped=len(skipped))
    db.commit()
    return batch_out(db, b)


class RejectIn(BaseModel):
    note: str = ""


@router.post("/{bid}/reject")
def reject(bid: str, body: RejectIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    b = get_or_404(db, BatchProposal, bid)
    if b.status != "proposed":
        raise HTTPException(409, "该批次已处理")
    if not (can(u, KIND_PERM[b.kind]) or b.created_by == u.id):
        raise HTTPException(403, "无权驳回")
    b.status, b.decided_by, b.decided_at = "rejected", u.id, _now()
    b.result = {"note": body.note, "analysis": analyze(db, b)}
    audit(db, u, "batch.rejected", "batch", b.id, note=body.note)
    db.commit()
    return {"ok": True}
