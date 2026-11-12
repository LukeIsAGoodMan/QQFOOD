"""切配生产：配方/出成率、包装规格、备料计划、生产记录（含损耗）。

备料：订单剩余需求 − 成品可用 = 要生产；× 每单位用料（1/出成率）= 原料需要；− 原料可用 = 缺口；
      按供应商箱规向上取整成箱数（例：300 lb 鸡丁，出成率 95%，鸡腿肉 40 lb/箱 → 8 箱 = 320 lb）。
生产：投入原料批次（剩余可当损耗或留在库存）→ 产出逐箱称重（标签）→ 损耗 = 投入 − 产出、实际出成率；
      成品批次默认待放行，有放行权限的人可勾选“完工即放行”（电子签名）。
箱规、出成率都是可随时修改的参数；历史采购单、生产记录保留当时的数字。
"""
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core import audit, can, current_user, get_or_404, require
from ..db import get_db
from ..models import (Box, InventoryTxn, Lot, ProductionBatch, QualityEvent, Recipe, SalesOrder, SOLine, Sku, User,
                      Warehouse)
from .inventory import Dup, assert_usable, balance, begin_posting, get_or_create_lot, txn

router = APIRouter(prefix="/api/production", tags=["production"])
SITE_TZ = ZoneInfo("America/Los_Angeles")
LOSS_ALERT_PP = 5.0  # 实际损耗比预计多 5 个百分点以上 → 标红


def _now():
    return datetime.now(timezone.utc)


def _main_wh(db):
    return db.query(Warehouse).filter_by(kind="main").first()


def _sku_out(s: Sku):
    return {"id": s.id, "code": s.code, "name": s.name, "category": s.category, "base_unit": s.base_unit,
            "catch_weight": s.catch_weight or s.base_unit in ("磅", "lb"), "purchase_pack": s.purchase_pack, "ship_pack": s.ship_pack}


def recipe_out(db, r: Recipe):
    skus = {s.id: s for s in db.query(Sku)}
    out = skus.get(r.output_sku_id)
    lines = [{"sku_id": l["sku_id"], "sku_code": skus[l["sku_id"]].code, "sku_name": skus[l["sku_id"]].name,
              "base_unit": skus[l["sku_id"]].base_unit, "per_output": l["per_output"],
              "yield_pct": round(100 / l["per_output"], 1) if l["per_output"] else None,
              "purchase_pack": skus[l["sku_id"]].purchase_pack} for l in r.lines if l["sku_id"] in skus]
    main = lines[0] if lines else None
    return {"id": r.id, "output_sku_id": r.output_sku_id, "output_code": out.code if out else None, "output_name": out.name if out else None,
            "output_unit": out.base_unit if out else None, "ship_pack": out.ship_pack if out else None, "lines": lines, "note": r.note,
            "expected_yield_pct": main["yield_pct"] if main else None, "updated_at": r.updated_at}


# ---------- master data: packs & recipes ----------
@router.get("/setup")
def setup(u: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"skus": [_sku_out(s) for s in db.query(Sku).filter(Sku.active.isnot(False)).order_by(Sku.name)],
            "recipes": [recipe_out(db, r) for r in db.query(Recipe)]}


class PackIn(BaseModel):
    purchase_pack: float | None = None
    ship_pack: float | None = None
    base_unit: str | None = None
    catch_weight: bool | None = None


@router.patch("/skus/{sid}")
def set_packs(sid: str, body: PackIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """改包装规格（换供应商/换箱子随时改）；没有库存流水时也可以把基本单位改成磅（称重品）。"""
    require(u, "inventory.master")
    s = get_or_404(db, Sku, sid)
    for k in ("purchase_pack", "ship_pack"):
        v = getattr(body, k)
        if v is not None:
            if v < 0:
                raise HTTPException(400, "规格不能为负")
            setattr(s, k, v or None)
    if body.base_unit and body.base_unit != s.base_unit:
        if db.query(func.count(InventoryTxn.id)).filter_by(sku_id=s.id).scalar():
            raise HTTPException(409, f"{s.code} 已有库存流水，不能改基本单位。请新建一个以磅为单位的 SKU，并把现有库存盘点过去")
        s.base_unit = body.base_unit
    if body.catch_weight is not None:
        s.catch_weight = body.catch_weight
    if s.base_unit in ("磅", "lb"):
        s.catch_weight = True
    audit(db, u, "sku.packs", "sku", s.id, purchase_pack=s.purchase_pack, ship_pack=s.ship_pack, base_unit=s.base_unit)
    db.commit()
    return _sku_out(s)


class RecipeLineIn(BaseModel):
    sku_id: str
    yield_pct: float | None = None   # 出成率 %，例如 95
    per_output: float | None = None  # 或直接给每单位用料


class RecipeIn(BaseModel):
    lines: list[RecipeLineIn]
    note: str = ""


@router.put("/recipes/{output_sku_id}")
def put_recipe(output_sku_id: str, body: RecipeIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "production.run")
    get_or_404(db, Sku, output_sku_id, "成品 SKU 不存在")
    lines = []
    for l in body.lines:
        get_or_404(db, Sku, l.sku_id, "原料 SKU 不存在")
        per = l.per_output or (100 / l.yield_pct if l.yield_pct else None)
        if not per or per <= 0 or (l.yield_pct is not None and not 1 <= l.yield_pct <= 100):
            raise HTTPException(400, "出成率需在 1–100% 之间")
        lines.append({"sku_id": l.sku_id, "per_output": round(per, 6)})
    if not lines:
        raise HTTPException(400, "至少一种原料")
    r = db.query(Recipe).filter_by(output_sku_id=output_sku_id).first() or Recipe(output_sku_id=output_sku_id, lines=[])
    old = r.lines
    r.lines, r.note, r.updated_by, r.updated_at = lines, body.note, u.id, _now()
    db.add(r)
    audit(db, u, "recipe.set", "sku", output_sku_id, before=old, after=lines)
    db.commit()
    return recipe_out(db, r)


# ---------- material planning ----------
def _available(db, sku_id, wh_id) -> float:
    from .inventory import stock
    rows = [r for r in stock(wh_id, None, db)["rows"] if r["sku_id"] == sku_id]
    return rows[0]["available"] if rows else 0.0


@router.get("/plan")
def plan(order_id: str | None = None, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """按未发完的客户订单算要生产多少、原料够不够、建议向供应商买几箱。"""
    wh = _main_wh(db)
    recipes = {r.output_sku_id: r for r in db.query(Recipe)}
    skus = {s.id: s for s in db.query(Sku)}
    q = db.query(SOLine).join(SalesOrder, SalesOrder.id == SOLine.order_id).filter(SalesOrder.status.in_(["confirmed", "partial"]))
    if order_id:
        q = q.filter(SOLine.order_id == order_id)
    demand: dict[str, dict] = {}
    for l in q:
        if not l.sku_id:
            continue
        rem = max(l.qty_ordered - l.qty_shipped, 0) * l.factor
        if rem <= 1e-9:
            continue
        so = db.get(SalesOrder, l.order_id)
        d = demand.setdefault(l.sku_id, {"qty": 0.0, "orders": []})
        d["qty"] += rem
        d["orders"].append({"order_id": so.id, "po_no": so.po_no, "required_date": so.required_date, "qty": round(rem, 3)})
    outputs, raw_need = [], {}
    for sku_id, d in demand.items():
        s = skus[sku_id]
        fin = _available(db, sku_id, wh.id) if wh else 0.0
        to_make = max(d["qty"] - fin, 0)
        r = recipes.get(sku_id)
        o = {"sku": _sku_out(s), "demand": round(d["qty"], 3), "finished_available": round(fin, 3), "to_make": round(to_make, 3),
             "orders": sorted(d["orders"], key=lambda x: x["required_date"] or "9999"), "has_recipe": bool(r),
             "boxes_suggested": math.ceil(to_make / s.ship_pack) if s.ship_pack and to_make > 0 else None, "inputs": []}
        if r and to_make > 0:
            for rl in r.lines:
                need = to_make * rl["per_output"]
                raw_need.setdefault(rl["sku_id"], {"need": 0.0, "for": []})
                raw_need[rl["sku_id"]]["need"] += need
                raw_need[rl["sku_id"]]["for"].append(s.code)
                o["inputs"].append({"sku_code": skus[rl["sku_id"]].code, "need": round(need, 2), "yield_pct": round(100 / rl["per_output"], 1)})
        outputs.append(o)
    materials = []
    for sku_id, d in raw_need.items():
        s = skus[sku_id]
        have = _available(db, sku_id, wh.id) if wh else 0.0
        short = max(d["need"] - have, 0)
        cases = math.ceil(short / s.purchase_pack - 1e-9) if s.purchase_pack and short > 0 else None
        buy = cases * s.purchase_pack if cases else (round(short, 2) if short > 0 else 0)
        materials.append({"sku": _sku_out(s), "need": round(d["need"], 2), "available": round(have, 2), "short": round(short, 2),
                          "suggest_cases": cases, "suggest_qty": round(buy, 2), "for": sorted(set(d["for"])),
                          "expected_leftover": round(buy + have - d["need"], 2) if short > 0 else round(have - d["need"], 2),
                          "note": None if s.purchase_pack or not short else "未设供应商箱规，按需要量建议"})
    return {"outputs": sorted(outputs, key=lambda o: o["sku"]["code"]), "materials": sorted(materials, key=lambda m: m["sku"]["code"]),
            "computed_at": _now()}


class PlanBuyIn(BaseModel):
    supplier: str = "待定"
    items: list[dict]  # [{sku_id, qty, cases?, unit_price?}]
    expected_date: str | None = None


@router.post("/plan/purchase")
def plan_to_po(body: PlanBuyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """把备料建议生成采购单草稿（按磅下单，备注里写箱数）；之后在采购页补价格、提交。"""
    from .purchasing import POIn, POLineIn, create_po
    lines = []
    for it in body.items:
        s = get_or_404(db, Sku, it.get("sku_id"), "SKU 不存在")
        q = float(it.get("qty") or 0)
        if q <= 0:
            continue
        name = s.name + (f"（{int(it['cases'])} 箱 × {s.purchase_pack:g} {s.base_unit}）" if it.get("cases") and s.purchase_pack else "")
        lines.append(POLineIn(sku_id=s.id, item_name=name, unit=s.base_unit, qty_ordered=q, unit_price=float(it.get("unit_price") or 0)))
    if not lines:
        raise HTTPException(400, "没有要采购的行")
    return create_po(POIn(supplier=body.supplier, category="原料包材", expected_date=body.expected_date, lines=lines,
                          note="由备料计划生成：按订单需求、出成率与供应商箱规计算"), u, db)


# ---------- production runs ----------
class RunInput(BaseModel):
    lot_id: str | None = None
    sku_id: str
    qty: float | None = None  # 留在库存模式下的实际用量


class RunIn(BaseModel):
    idempotency_key: str
    output_sku_id: str
    lot_code: str
    expiry: str | None = None
    order_id: str | None = None
    inputs: list[RunInput]
    leftover: str = "loss"            # loss = 所选原料批次剩余全部当损耗；keep = 只扣实际用量，剩余留在库存
    box_weights: list[float] = []     # 称重品：每箱实重
    output_qty: float | None = None   # 非称重品：产出数量
    loss_reason: str = "切配修整损耗"
    release: bool = False             # 完工即放行（需质量放行权限，电子签名）
    sop_ref: str = ""


def run_out(db, b: ProductionBatch, skus=None, lots=None):
    skus = skus or {s.id: s for s in db.query(Sku)}
    lot = db.get(Lot, b.output_lot_id)
    over = (b.yield_pct is not None and b.expected_yield_pct is not None and b.expected_yield_pct - b.yield_pct >= LOSS_ALERT_PP)
    return {"id": b.id, "code": b.code, "product": skus[b.product_sku_id].name, "product_code": skus[b.product_sku_id].code,
            "unit": skus[b.product_sku_id].base_unit, "output_lot": lot.lot_code, "quality_status": lot.quality_status,
            "output_qty": b.output_qty, "boxes": b.boxes, "consumed_qty": b.consumed_qty, "loss_qty": b.loss_qty, "yield_pct": b.yield_pct,
            "expected_yield_pct": b.expected_yield_pct, "loss_alert": over, "loss_reason": b.loss_reason, "order_id": b.order_id,
            "inputs": [{**i, "sku_code": skus[i["sku_id"]].code, "lot_code": (db.get(Lot, i["lot_id"]).lot_code if i.get("lot_id") else "(无批次)")} for i in b.inputs],
            "sop_ref": b.sop_ref, "created_at": b.created_at}


@router.get("/runs")
def runs(u: User = Depends(current_user), db: Session = Depends(get_db)):
    skus = {s.id: s for s in db.query(Sku)}
    return [run_out(db, b, skus) for b in db.query(ProductionBatch).order_by(ProductionBatch.created_at.desc()).limit(100)]


@router.get("/inputs/{sku_id}")
def input_lots(sku_id: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """某原料在主仓可用的批次（先到期先出）。"""
    from .orders import fefo_lots
    wh = _main_wh(db)
    return fefo_lots(db, sku_id, wh.id) if wh else []


@router.post("/runs")
def create_run(body: RunIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "production.run")
    if body.release:
        require(u, "quality.release")  # 先检查，避免扣了原料才发现没权限
    wh = _main_wh(db)
    out_sku = get_or_404(db, Sku, body.output_sku_id, "成品 SKU 不存在")
    catch = out_sku.catch_weight or out_sku.base_unit in ("磅", "lb")
    if catch and not body.box_weights:
        raise HTTPException(400, "称重品请逐箱录入重量")
    if not catch and not (body.output_qty and body.output_qty > 0):
        raise HTTPException(400, "请填写产出数量")
    if any(w <= 0 or w > 200 for w in body.box_weights):
        raise HTTPException(400, "每箱重量需在 0–200 之间")
    if not body.inputs:
        raise HTTPException(400, "请选择投入的原料")
    if body.leftover not in ("loss", "keep"):
        raise HTTPException(400, "leftover 需为 loss 或 keep")
    if not body.lot_code.strip():
        raise HTTPException(400, "需要成品批次号")
    if db.query(Lot).filter_by(sku_id=out_sku.id, lot_code=body.lot_code.strip()).first():
        raise HTTPException(409, "成品批次号已存在")
    try:
        p = begin_posting(db, u, body.idempotency_key, "production", "batch", None, f"切配生产 {out_sku.code}")
    except Dup:
        return {"duplicate": True}
    consumed, inputs, units = 0.0, [], set()
    for i in body.inputs:
        assert_usable(db, i.lot_id, "领用")
        have = balance(db, i.sku_id, wh.id, "on_hand", i.lot_id)
        q = have if body.leftover == "loss" else (i.qty or 0)
        if q <= 0:
            continue
        if q > have + 1e-6:
            raise HTTPException(409, f"原料批次账面不足：现有 {have}")
        txn(db, p, i.sku_id, wh.id, "on_hand", -q, i.lot_id)
        consumed += q
        units.add(db.get(Sku, i.sku_id).base_unit)
        inputs.append({"lot_id": i.lot_id, "sku_id": i.sku_id, "qty": round(q, 3)})
    if not inputs:
        raise HTTPException(400, "所选原料没有可用库存")
    output = round(sum(body.box_weights), 3) if catch else body.output_qty
    lot = get_or_create_lot(db, out_sku.id, body.lot_code, body.expiry, status="pending")
    same_unit = units == {out_sku.base_unit} or (units <= {"磅", "lb"} and out_sku.base_unit in ("磅", "lb"))
    loss = round(consumed - output, 3) if same_unit else None
    yld = round(100 * output / consumed, 1) if same_unit and consumed else None
    recipe = db.query(Recipe).filter_by(output_sku_id=out_sku.id).first()
    expected = round(100 / recipe.lines[0]["per_output"], 1) if recipe and recipe.lines else None
    if loss is not None and loss < -1e-6:
        raise HTTPException(409, f"产出 {output} 大于投入 {consumed}，请核对重量或原料")
    n = db.query(func.count(ProductionBatch.id)).scalar() + 1
    b = ProductionBatch(code=f"PB-{datetime.now(SITE_TZ).strftime('%y%m%d')}-{n:03d}", product_sku_id=out_sku.id, output_lot_id=lot.id,
                        warehouse_id=wh.id, output_qty=output, inputs=inputs, sop_ref=body.sop_ref, posting_id=p.id, order_id=body.order_id,
                        consumed_qty=round(consumed, 3), loss_qty=loss, yield_pct=yld, expected_yield_pct=expected,
                        loss_reason=body.loss_reason if loss else "", boxes=len(body.box_weights), created_by=u.id)
    db.add(b)
    db.flush()
    lot.produced_by_batch, lot.status_reason = b.id, "生产完工，待质量放行"
    p.source_id = b.id
    txn(db, p, out_sku.id, wh.id, "on_hand", output, lot.id)
    stamp = datetime.now(SITE_TZ).strftime("%y%m%d")
    n0 = db.query(func.count(Box.id)).scalar()
    boxes = []
    for k, w in enumerate(body.box_weights):
        bx = Box(serial=f"BX{stamp}-{n0 + k + 1:05d}", sku_id=out_sku.id, lot_id=lot.id, warehouse_id=wh.id, weight=round(w, 2), packed_by=u.id)
        db.add(bx)
        boxes.append(bx)
    if body.release:
        lot.quality_status, lot.status_reason = "released", "完工即放行"
        db.add(QualityEvent(lot_id=lot.id, action="release", reason="完工即放行", basis=f"生产记录 {b.code}",
                            signature_meaning="我已检查本批次产品，批准放行", signed_by=u.id))
    db.flush()
    audit(db, u, "production.run", "batch", b.id, output=output, consumed=consumed, loss=loss, yield_pct=yld, boxes=len(boxes), released=body.release)
    db.commit()
    return {"duplicate": False, "run": run_out(db, b),
            "boxes": [{"id": x.id, "serial": x.serial, "weight": x.weight, "lot_code": lot.lot_code, "sku_code": out_sku.code,
                       "sku_name": out_sku.name, "expiry": lot.expiry, "packed_at": x.packed_at} for x in boxes]}
