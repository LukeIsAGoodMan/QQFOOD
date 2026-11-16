"""历史业务补录（只有老板 / 系统管理员）。

补一张以前发生、系统里没记全的单据。每一行单独选库存处理：
- doc_only 只补单据：库存已经入账（期初 / 盘点 / 调入 / 发货时已扣），或者是费用、不管库存的东西；默认就是这个；
- post 补记库存变动：库存确实还没记，按业务类型加 / 减对应仓库。

不同环节分开判断：销售已交付 ≠ 原料采购、收货、生产耗用已补齐。采购单（只是订货）不能加库存；收货加库存；生产耗用减原料。
确认前给出每行的库存影响和变动前后数量；没对上商品、单位换算不明确的行不能记库存。
已记库存的补录单修改或作废，按差额 / 冲销记新流水，不改旧流水。
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .categories import FOOD, UNCATEGORIZED, guess as guess_category, normalize as norm_category
from .core import audit
from .fulfillment import D, q4
from .models import (BatchProposal, HistoricalBackfill, InventoryPosting, InventoryTxn, Lot, ProductionBatch,
                     PurchaseOrder, SalesOrder, Shipment, Sku, Transfer, TransferLine, User, Warehouse)

ADMIN_ROLES = ("boss", "sys_admin")

KINDS = {
    "purchase_receipt": "采购小票（买了就拿回来）",
    "purchase_order": "采购单（订货，不加库存）",
    "receiving": "收货",
    "transfer": "调拨",
    "production_use": "生产领料 / 耗用",
    "sales_delivery": "销售交付",
    "expense": "费用",
}
# 这一类单据的行“补记库存”时往哪个方向动；None = 这类单据不能动库存
DIRECTION = {"purchase_receipt": "in", "receiving": "in", "transfer": "transfer", "production_use": "out",
             "sales_delivery": "out", "purchase_order": None, "expense": None}
NO_STOCK_REASON = {"purchase_order": "采购单只是订货，不能增加库存；货到了按「收货」补（或正常收货）",
                   "expense": "费用不管库存"}

# 明细物品类型 → 默认分类 / 用途 / 是否管库存
ITEM_TYPES = {
    "食品原料": {"category": None, "usage": "production", "track": True},
    "调料辅料": {"category": "干货", "usage": "production", "track": True},
    "包材": {"category": "生产用品", "usage": "", "track": True},
    "生产用品": {"category": "生产用品", "usage": "", "track": True},
    "维修材料": {"category": "公司用品", "usage": "", "track": False},
    "设备": {"category": "生产机器", "usage": "", "track": False},
    "成品": {"category": None, "usage": "resale", "track": True},
    "费用": {"category": None, "usage": "", "track": False},
}
ITEM_SYN = {"原料": "食品原料", "食品": "食品原料", "调料": "调料辅料", "辅料": "调料辅料", "调料/辅料": "调料辅料", "spice": "调料辅料",
            "包装": "包材", "packaging": "包材", "维修": "维修材料", "equipment": "设备", "机器": "设备", "expense": "费用"}

GOODS_STATUS = {"arrived": "已到货", "in_transit": "在途 / 未到货"}
PAY = {"company_card": "公司卡", "cash": "现金", "personal": "个人垫付", "check": "支票", "transfer": "转账", "other": "其他", "": ""}
LINK_TYPES = {"order": "客户订单", "shipment": "送货单", "po": "采购单", "transfer": "调拨 / 调入单", "production": "生产记录",
              "batch": "库存导入批次", "posting": "库存流水", "backfill": "补录单"}
WINDOW_DAYS = 21

UNIT_SYN = {"lb": "磅", "lbs": "磅", "pound": "磅", "pounds": "磅", "kg": "公斤", "kgs": "公斤", "千克": "公斤", "oz": "盎司",
            "case": "箱", "cs": "箱", "ctn": "箱", "carton": "箱", "box": "箱", "bag": "袋", "pack": "包", "pk": "包",
            "pc": "个", "pcs": "个", "ea": "个", "each": "个", "件": "个"}
PHYSICAL = {("公斤", "磅"): Decimal("2.20462"), ("盎司", "磅"): Decimal("0.0625"), ("磅", "公斤"): Decimal("0.453592")}


def is_admin(u: User) -> bool:
    return bool(u) and u.role in ADMIN_ROLES


def require_admin(u: User):
    if not is_admin(u):
        raise HTTPException(403, "历史业务补录只有老板或系统管理员可以操作")


def unit_norm(u) -> str:
    s = str(u or "").strip()
    return UNIT_SYN.get(s.lower(), s)


def _norm(s) -> str:
    return "".join(str(s or "").lower().split())


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _day(v) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    return str(v or "")[:10]


def _wh597(db) -> Warehouse | None:
    return db.query(Warehouse).filter_by(code="WH597").first() or db.query(Warehouse).filter_by(kind="main").first()


def find_wh(db, ref) -> Warehouse | None:
    if not ref:
        return None
    r = str(ref).strip()
    return db.get(Warehouse, r) or db.query(Warehouse).filter_by(code=r.upper()).first() or \
        next((w for w in db.query(Warehouse) if _norm(w.name) == _norm(r)), None)


def base_qty(base_unit: str, conversions: dict, qty, unit, factor):
    """数量 → 基本单位（Decimal）。返回 (基本单位数量 or None, 说明, 问题)。换算不明确就不给数，不默认相等。"""
    q, u, b = D(qty), unit_norm(unit) or base_unit, base_unit
    fac = D(factor) if _f(factor) else None
    if u == b:
        if fac is not None and fac != 1:
            return None, "", f"单位就是基本单位「{b}」，换算却填了 {factor}，请核对"
        return q, f"{q4(q):g} {b}", ""
    per = (conversions or {}).get(u)
    if per:
        bq = q / D(per)
        if fac is not None and abs(fac - 1 / D(per)) > Decimal("0.0001"):
            return None, "", f"商品主档是 1 {b} = {per:g} {u}，这一行换算填的是 1 {u} = {factor} {b}，两者不一致"
        return bq, f"{q4(q):g} {u} = {q4(bq):g} {b}（1 {b} = {float(per):g} {u}）", ""
    if fac is not None and fac > 0:
        bq = q * fac
        return bq, f"{q4(q):g} {u} = {q4(bq):g} {b}（1 {u} = {q4(fac):g} {b}，本行填的换算）", ""
    if (u, b) in PHYSICAL:
        bq = q * PHYSICAL[(u, b)]
        return bq, f"{q4(q):g} {u} = {q4(bq):g} {b}", ""
    return None, "", f"单位「{u}」和基本单位「{b}」的换算不明确：填「1 {u} = 几 {b}」，或在商品主档加换算"


# ---------- 清洗输入 ----------
HEADER_FIELDS = ("kind", "business_date", "supplier", "doc_no", "container_no", "goods_status", "warehouse_id",
                 "from_warehouse_id", "payment_method", "paid_by", "total_amount", "reason", "note")
LINE_FIELDS = ("name", "item_type", "category", "sku_id", "new_sku", "qty", "unit", "factor", "unit_price", "amount",
               "lot_code", "expiry", "lot_id", "inventory", "direction", "covered_by", "note")


def find_sku(db, ref) -> Sku | None:
    from .routers.batches import find_sku_any
    if not ref:
        return None
    s = db.get(Sku, str(ref))
    return s if s and s.active is not False else find_sku_any(db, ref)


def clean_header(db, data: dict, old: HistoricalBackfill | None = None) -> dict:
    h = {k: getattr(old, k) for k in HEADER_FIELDS} if old else {}
    for k in HEADER_FIELDS:
        if k in data and data[k] is not None:
            h[k] = data[k]
        h.setdefault(k, None)
    h["kind"] = str(h.get("kind") or "").strip()
    if h["kind"] not in KINDS:
        raise HTTPException(400, f"补录类型需为：{'、'.join(f'{k}（{v}）' for k, v in KINDS.items())}")
    d = _day(h.get("business_date"))
    try:
        if date.fromisoformat(d) > date.today() + timedelta(days=1):
            raise HTTPException(400, "业务日期不能是将来（补录的是已经发生的事）")
    except ValueError:
        raise HTTPException(400, "业务日期格式为 YYYY-MM-DD")
    h["business_date"] = d
    for k in ("supplier", "doc_no", "container_no", "paid_by", "reason", "note"):
        h[k] = str(h.get(k) or "").strip()
    h["goods_status"] = h.get("goods_status") or "arrived"
    if h["goods_status"] not in GOODS_STATUS:
        raise HTTPException(400, "到货状态需为 arrived（已到货）/ in_transit（在途、未到货）")
    from .routers.batches import PAY_NORM
    pm = str(h.get("payment_method") or "").strip()
    h["payment_method"] = pm if pm in PAY else PAY_NORM.get(pm.lower(), "other")
    for k in ("warehouse_id", "from_warehouse_id"):
        if h.get(k):
            w = find_wh(db, h[k])
            if not w:
                raise HTTPException(400, f"找不到仓库 / 来源地「{h[k]}」")
            h[k] = w.id
    if not h.get("warehouse_id"):
        w = _wh597(db)
        h["warehouse_id"] = w.id if w else None
    if h["kind"] == "transfer" and not h.get("from_warehouse_id"):
        raise HTTPException(400, "调拨要写从哪里来（from_warehouse，如多伦多 / SUPREME）")
    h["total_amount"] = _f(h.get("total_amount"))
    return h


def clean_lines(db, kind: str, lines: list, old_lines: list | None = None) -> list:
    if not isinstance(lines, list) or not lines:
        raise HTTPException(400, "至少要有一行明细")
    if len(lines) > 200:
        raise HTTPException(400, "一张补录单最多 200 行")
    # 行号：沿用原来的（修改时按行号对账），新行取一个从没用过的号（作废掉的行号也不复用）
    taken = {l["id"] for l in (old_lines or [])}
    keep = {str(x.get("id")) for x in lines if x.get("id")}
    out, used, n = [], set(), 0

    def fresh():
        nonlocal n
        while True:
            n += 1
            c = f"L{n:03d}"
            if c not in taken and c not in keep and c not in used:
                return c
    for i, raw in enumerate(lines):
        r = {k: raw.get(k) for k in LINE_FIELDS if raw.get(k) is not None}
        lid = str(raw.get("id") or "")
        if not lid or lid in used:
            lid = fresh()
        used.add(lid)
        r["id"] = lid
        it = str(r.get("item_type") or "").strip()
        r["item_type"] = ITEM_SYN.get(it.lower(), ITEM_SYN.get(it, it)) if it else ""
        if r["item_type"] and r["item_type"] not in ITEM_TYPES:
            raise HTTPException(400, f"第 {i + 1} 行物品类型需为：{'、'.join(ITEM_TYPES)}")
        sku = find_sku(db, r.get("sku_id")) if r.get("sku_id") else None
        if r.get("sku_id") and not sku:
            raise HTTPException(400, f"第 {i + 1} 行找不到商品「{r['sku_id']}」（可用品名、内部编号、大统华 Item#；没有就用 new_sku 新建）")
        r["sku_id"] = sku.id if sku else None
        r["name"] = str(r.get("name") or (sku.name if sku else "") or (r.get("new_sku") or {}).get("name") or "").strip()
        if not r["name"]:
            raise HTTPException(400, f"第 {i + 1} 行缺品名")
        cat = r.get("category")
        if cat:
            c = norm_category(cat, r["name"])
            if c is None:
                raise HTTPException(400, f"第 {i + 1} 行分类「{cat}」不认识")
            r["category"] = c
        else:
            preset = ITEM_TYPES.get(r["item_type"], {}).get("category")
            r["category"] = sku.category if sku else (preset or guess_category(r["name"]) or UNCATEGORIZED)
        if r.get("new_sku") and not sku:
            ns = r["new_sku"] if isinstance(r["new_sku"], dict) else {}
            r["new_sku"] = {"name": str(ns.get("name") or r["name"]).strip(), "category": norm_category(ns.get("category") or r["category"], r["name"]) or r["category"],
                            "base_unit": unit_norm(ns.get("base_unit") or r.get("unit") or "箱"),
                            "usage": ns.get("usage") if ns.get("usage") is not None else ITEM_TYPES.get(r["item_type"], {}).get("usage"),
                            "conversions": {unit_norm(k): float(v) for k, v in (ns.get("conversions") or {}).items() if _f(v)}}
        else:
            r.pop("new_sku", None)
        for k in ("qty", "factor", "unit_price", "amount"):
            if k in r:
                v = _f(r[k])
                if v is None or v < 0:
                    raise HTTPException(400, f"第 {i + 1} 行 {k} 需为非负数字")
                r[k] = v
        if r.get("amount") is None and r.get("qty") is not None and r.get("unit_price") is not None:
            r["amount"] = q4(D(r["qty"]) * D(r["unit_price"]))
        r["unit"] = unit_norm(r.get("unit")) or (sku.base_unit if sku else (r.get("new_sku") or {}).get("base_unit", ""))
        inv = str(r.get("inventory") or "doc_only")
        if inv not in ("doc_only", "post"):
            raise HTTPException(400, f"第 {i + 1} 行库存处理需为 doc_only（只补单据）/ post（补记库存变动）")
        r["inventory"] = inv
        d = DIRECTION.get(kind)
        if kind == "production_use":
            r["direction"] = "in" if str(r.get("direction") or "") in ("in", "output", "产出") else "out"
        else:
            r["direction"] = d or ""
        for k in ("lot_code", "expiry", "lot_id", "note"):
            if k in r:
                r[k] = str(r[k]).strip()
        if r.get("covered_by"):
            r["covered_by"] = resolve_link(db, r["covered_by"])
        out.append(r)
    return out


# ---------- 关联已有记录 ----------
_LINK_MODEL = {"order": SalesOrder, "shipment": Shipment, "po": PurchaseOrder, "transfer": Transfer, "production": ProductionBatch,
               "batch": BatchProposal, "posting": InventoryPosting, "backfill": HistoricalBackfill}


def resolve_link(db, ref) -> dict:
    """{type, id} → 校验存在并带上给人看的名字。订单也可以用 PO 号，送货单可以用送货单号。"""
    if not isinstance(ref, dict) or ref.get("type") not in _LINK_MODEL:
        raise HTTPException(400, f"关联类型需为：{'、'.join(f'{k}（{v}）' for k, v in LINK_TYPES.items())}")
    t, rid = ref["type"], str(ref.get("id") or "").strip()
    M = _LINK_MODEL[t]
    obj = db.get(M, rid)
    if not obj and hasattr(M, "code"):
        obj = db.query(M).filter_by(code=rid.upper()).first()
    if not obj and t == "order":
        obj = db.query(SalesOrder).filter_by(po_no=rid).first()
    if not obj and t == "shipment":
        obj = db.query(Shipment).filter_by(delivery_no=rid).first()
    if not obj and t == "posting":
        obj = db.query(InventoryPosting).filter_by(idempotency_key=rid).first()
    if not obj:
        raise HTTPException(400, f"找不到要关联的{LINK_TYPES[t]}「{rid}」")
    label = getattr(obj, "code", None) or obj.id
    if t == "order" and obj.po_no:
        label = f"{obj.code}（PO {obj.po_no}）"
    if t == "shipment" and obj.delivery_no:
        label = f"{obj.code}（{obj.delivery_no}）"
    if t == "posting":
        label = f"{obj.kind} {obj.note or ''}".strip()
    return {"type": t, "id": obj.id, "label": f"{LINK_TYPES[t]} {label}"}


def _posting_txns(db, pid):
    return db.query(InventoryTxn).filter_by(posting_id=pid).all()


def _linked_stock(db, links: list) -> list[dict]:
    """关联记录已经动过的库存：[{sku_id, sign(+1/-1), by}]——这些不能再补记一次。"""
    hits = []

    def add_posting(pid, by):
        p = pid and db.get(InventoryPosting, pid)
        if not p or p.reversed_by_posting_id:
            return
        for t in _posting_txns(db, p.id):
            if t.bucket == "on_hand" and t.qty:
                hits.append({"sku_id": t.sku_id, "sign": 1 if t.qty > 0 else -1, "by": by})
    for l in links or []:
        t, oid, by = l["type"], l["id"], l["label"]
        if t == "shipment":
            sh = db.get(Shipment, oid)
            if sh and sh.inventory_effect == "issued":
                add_posting(sh.posting_id, f"{by}（发货时已扣库存）")
        elif t == "order":
            for sh in db.query(Shipment).filter_by(order_id=oid):
                if sh.inventory_effect == "issued":
                    add_posting(sh.posting_id, f"{by} 的送货单 {sh.code}（发货时已扣库存）")
        elif t == "production":
            pb = db.get(ProductionBatch, oid)
            if pb:
                add_posting(pb.posting_id, f"{by}（生产时已记领料与产出）")
        elif t == "po":
            from .models import Receipt
            for rc in db.query(Receipt).filter_by(po_id=oid, status="posted"):
                add_posting(rc.posting_id, f"{by} 的收货单 {rc.code}（已入库）")
        elif t == "transfer":
            for p in db.query(InventoryPosting).filter(InventoryPosting.source_id == oid,
                                                       InventoryPosting.kind.in_(("transfer_ship", "transfer_receive"))):
                add_posting(p.id, f"{by}（调拨已记）")
        elif t == "posting":
            add_posting(oid, by)
        elif t == "batch":
            for p in db.query(InventoryPosting).filter_by(source_type="batch", source_id=oid):
                add_posting(p.id, f"{by}（导入时已记）")
        elif t == "backfill":
            other = db.get(HistoricalBackfill, oid)
            for e in (other.ledger if other else []):
                if e["qty"]:
                    hits.append({"sku_id": e["sku_id"], "sign": 1 if e["qty"] > 0 else -1, "by": f"{by}（已补记库存）"})
    return hits


def _pdate(p: InventoryPosting) -> str:
    return p.effective_at or _day(p.created_at)


def related(db, rec_id, h: dict, sku_id, wh_id, sign: int) -> tuple[list[str], list[str]]:
    """这个商品在这个仓库可能已经记过的账。返回 (疑似重复的原因, 仅供参考的信息)。"""
    sus, info = [], []
    biz = date.fromisoformat(h["business_date"])
    lo, hi = (biz - timedelta(days=WINDOW_DAYS)).isoformat(), (biz + timedelta(days=WINDOW_DAYS)).isoformat()
    rows = (db.query(InventoryTxn, InventoryPosting).join(InventoryPosting, InventoryTxn.posting_id == InventoryPosting.id)
            .filter(InventoryTxn.sku_id == sku_id, InventoryTxn.warehouse_id == wh_id, InventoryTxn.bucket == "on_hand",
                    InventoryPosting.reversed_by_posting_id.is_(None), InventoryPosting.reverses_posting_id.is_(None)).all())
    seen = set()
    for t, p in rows:
        if p.id in seen or (p.source_type == "backfill" and p.source_id == rec_id):
            continue
        seen.add(p.id)
        d = _pdate(p)
        if p.kind == "count" and d >= h["business_date"]:
            sus.append(f"{d} 有期初 / 盘点（{p.note or '盘点'}），录的是当时的实际结存——{h['business_date']} 的这笔如果发生在那之前，结存里已经包含了它")
        elif p.kind in ("receive", "transfer_receive", "backfill", "production") and sign > 0 and t.qty > 0 and lo <= d <= hi:
            sus.append(f"{d} 已有入库 {q4(t.qty):g}（{p.note or p.kind}）")
        elif p.kind in ("issue", "production", "transfer_ship", "backfill") and sign < 0 and t.qty < 0 and lo <= d <= hi:
            sus.append(f"{d} 已有出库 / 耗用 {q4(-t.qty):g}（{p.note or p.kind}）")
    if h.get("container_no"):
        for lot in db.query(Lot).filter_by(sku_id=sku_id, lot_code=h["container_no"]):
            from .routers.inventory import balance
            have = balance(db, sku_id, wh_id, "on_hand", lot.id)
            if have and sign > 0:
                sus.append(f"已有 Lot {lot.lot_code} 库存 {q4(have):g}（这一柜可能已经导入过）")
    if sign > 0:
        for tl, tr in (db.query(TransferLine, Transfer).join(Transfer, TransferLine.transfer_id == Transfer.id)
                       .filter(TransferLine.sku_id == sku_id, Transfer.to_wh == wh_id, Transfer.status.in_(("requested", "shipped", "partial")))):
            info.append(f"调入单 {tr.code} 还没收完（{tr.status}）：到货收货时会入库，这里不要再记")
    return sus, info


def doc_dups(db, rec_id, h: dict) -> list[str]:
    """疑似重复的单据：别的补录单、采购单、库存导入批次。"""
    out = []
    no, sup, total, day, box = h["doc_no"], _norm(h["supplier"]), h.get("total_amount"), h["business_date"], h["container_no"]
    for o in db.query(HistoricalBackfill).filter(HistoricalBackfill.status != "voided", HistoricalBackfill.id != (rec_id or "")):
        same_no = no and _norm(o.doc_no) == _norm(no) and (not sup or _norm(o.supplier) == sup)
        same_amt = sup and _norm(o.supplier) == sup and o.business_date == day and total is not None and o.total_amount is not None \
            and abs(o.total_amount - total) < 0.01
        same_box = box and o.container_no == box and o.kind == h["kind"]
        if same_no or same_amt or same_box:
            out.append(f"补录单 {o.code}（{KINDS[o.kind]}，{o.business_date}，{o.supplier or o.doc_no or o.container_no}）")
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.status != "cancelled"):
        if (no and _norm(po.invoice_no) == _norm(no)) or (sup and _norm(po.supplier) == sup and (po.purchase_date or _day(po.created_at)) == day
                                                          and total is not None and po.invoice_amount is not None and abs(po.invoice_amount - total) < 0.01):
            out.append(f"采购单 {po.code}（{po.supplier}，票据 {po.invoice_no or '—'}）")
    for b in db.query(BatchProposal).filter(BatchProposal.status.in_(("applied", "partial", "proposed"))):
        meta = b.meta or {}
        txt = f"{meta} {b.source_note or ''}"
        hit = (no and len(no) >= 3 and no in txt) or (box and (box in txt or any(str(r.get("lot_code") or "") == box for r in b.rows or [])))
        if hit:
            out.append(f"库存导入批次 {b.code}（{b.kind}，{'已确认' if b.status != 'proposed' else '待确认'}）")
    return out


# ---------- 预览：每行的库存影响 ----------
class Sim:
    """在当前账面上模拟这次要记的变动（同一商品多行时，前后数量连续算）。"""

    def __init__(self, db):
        self.db, self.d = db, {}

    def lot(self, sku_id, wh_id, lot_id) -> Decimal:
        from .routers.inventory import balance
        k = (sku_id, wh_id, lot_id)
        return D(balance(self.db, sku_id, wh_id, "on_hand", lot_id)) + self.d.get(k, Decimal(0))

    def total(self, sku_id, wh_id) -> Decimal:
        from sqlalchemy import func
        base = self.db.query(func.coalesce(func.sum(InventoryTxn.qty), 0)).filter_by(sku_id=sku_id, warehouse_id=wh_id, bucket="on_hand").scalar()
        return D(base) + sum((v for (s, w, _), v in self.d.items() if s == sku_id and w == wh_id), Decimal(0))

    def add(self, sku_id, wh_id, lot_id, q: Decimal):
        k = (sku_id, wh_id, lot_id)
        self.d[k] = self.d.get(k, Decimal(0)) + q

    def fifo(self, sku_id, wh_id, need: Decimal) -> tuple[list, Decimal]:
        """先进先出从各 Lot 扣（只扣已放行的）；返回 ([(lot_id, -qty)], 还差多少)。"""
        lots = [l for l in self.db.query(Lot).filter_by(sku_id=sku_id).order_by(Lot.created_at) if l.quality_status == "released"]
        out = []
        for lid in [l.id for l in lots] + [None]:
            if need <= 0:
                break
            have = self.lot(sku_id, wh_id, lid)
            take = min(have, need) if have > 0 else Decimal(0)
            if take > 0:
                out.append((lid, -take))
                need -= take
        return out, need


def _lot_id(db, sku_id, code):
    if not code:
        return None
    l = db.query(Lot).filter_by(sku_id=sku_id, lot_code=code).first()
    return l.id if l else f"new:{code}"


def _ledger_for(rec, line_id) -> dict:
    cur = {}
    for e in (rec.ledger if rec else []) or []:
        if e["line_id"] == line_id:
            k = (e["sku_id"], e["warehouse_id"], e["lot_id"])
            cur[k] = cur.get(k, Decimal(0)) + D(e["qty"])
    return {k: v for k, v in cur.items() if v != 0}


def _auto_lot(h, line):
    sup = "".join(ch for ch in (h.get("supplier") or "").upper() if ch.isalnum())[:10] or "HB"
    return f"{h.get('container_no') or sup}-{h['business_date'][2:].replace('-', '')}"


def _resolve_line_sku(db, l):
    """(Sku 或 None, 基本单位, 换算, 说明)。new_sku 有同名在用商品就复用，不重复建档。"""
    if l.get("sku_id"):
        s = db.get(Sku, l["sku_id"])
        return s, s.base_unit, s.conversions or {}, ""
    ns = l.get("new_sku")
    if ns:
        s = find_sku(db, ns["name"])
        if s:
            return s, s.base_unit, s.conversions or {}, f"已有商品「{s.name}」，确认时复用，不新建"
        return None, ns["base_unit"], ns.get("conversions") or {}, f"确认时新建商品「{ns['name']}」（{ns['category']}，基本单位 {ns['base_unit']}）"
    return None, "", {}, ""


def plan(db, rec: HistoricalBackfill | None, h: dict, lines: list, negative_reason: str = "") -> dict:
    sim, whs = Sim(db), {w.id: w for w in db.query(Warehouse)}
    links = h.get("links") or []
    linked = _linked_stock(db, links)
    rid = rec.id if rec else None
    out_lines, line_ids = [], {l["id"] for l in lines}
    # 被删掉的行：它记过的库存要冲回
    gone = sorted({e["line_id"] for e in (rec.ledger if rec else []) or []} - line_ids)
    for l in lines + [{"id": g, "name": "（已删除的行）", "inventory": "doc_only", "direction": "", "_gone": True} for g in gone]:
        sku, bu, conv, sku_note = _resolve_line_sku(db, l) if not l.get("_gone") else (None, "", {}, "")
        res = {"line_id": l["id"], "name": l.get("name"), "item_type": l.get("item_type", ""), "category": l.get("category", ""),
               "mode": l["inventory"], "direction": l.get("direction", ""), "sku": sku.name if sku else None, "sku_id": sku.id if sku else None,
               "sku_note": sku_note, "base_unit": bu, "convert": "", "qty": l.get("qty"), "unit": l.get("unit"), "amount": l.get("amount"),
               "effects": [], "blockers": [], "suspects": [], "info": [], "deltas": []}
        if l.get("covered_by"):
            res["info"].append(f"已由 {l['covered_by']['label']} 记账")
        bq = None
        if l.get("qty") is not None and bu:
            bq, res["convert"], prob = base_qty(bu, conv, l["qty"], l.get("unit"), l.get("factor"))
            if prob:
                (res["blockers"] if l["inventory"] == "post" else res["info"]).append(prob)
        specs = []  # (sku_id, wh_id, lot spec, signed qty)；lot spec：lot_id / "new:code" / "pool"
        if l["inventory"] == "post":
            k, d = h["kind"], l.get("direction")
            if DIRECTION.get(k) is None:
                res["blockers"].append(NO_STOCK_REASON[k])
            if l.get("item_type") == "费用":
                res["blockers"].append("费用不管库存，请选「只补单据」")
            if not sku and not l.get("new_sku"):
                res["blockers"].append("没对上商品：选一个已有商品，或新建商品，才能记库存")
            if not l.get("qty"):
                res["blockers"].append("数量要大于 0")
            if l.get("covered_by"):
                res["blockers"].append(f"这一行标注了已由 {l['covered_by']['label']} 记账，不能再记一次")
            if d in ("in", "transfer") and h.get("goods_status") == "in_transit":
                res["blockers"].append("货还没到（在途），不能增加实物库存；到货时按正常收货记一次")
            sign = 1 if d == "in" else -1
            if sku:
                for hit in linked:
                    if hit["sku_id"] == sku.id and (d == "transfer" or hit["sign"] == sign):
                        res["blockers"].append(f"关联的 {hit['by']} 已经记过「{sku.name}」，不能重复记")
                        break
            to_w, from_w = whs.get(h.get("warehouse_id")), whs.get(h.get("from_warehouse_id"))
            if not res["blockers"] and bq is not None:
                sid = sku.id if sku else "new"
                food = l.get("category") in FOOD
                lot_in = l.get("lot_id") or (f"new:{l['lot_code']}" if l.get("lot_code") else (f"new:{_auto_lot(h, l)}" if food else None))
                if lot_in and lot_in.startswith("new:") and sku:
                    lot_in = _lot_id(db, sku.id, lot_in[4:])
                if d == "in":
                    if not to_w or not to_w.tracked:
                        res["blockers"].append(f"{to_w.name if to_w else '仓库'} 不在本系统管账，不能记库存")
                    else:
                        specs.append((sid, to_w.id, lot_in, bq))
                elif d == "out":
                    if not to_w or not to_w.tracked:
                        res["blockers"].append(f"{to_w.name if to_w else '仓库'} 不在本系统管账，不能扣库存")
                    else:
                        specs.append((sid, to_w.id, l.get("lot_id") or "pool", -bq))
                elif d == "transfer":
                    if from_w and from_w.tracked:
                        specs.append((sid, from_w.id, l.get("lot_id") or "pool", -bq))
                    if to_w and to_w.tracked:
                        specs.append((sid, to_w.id, lot_in or "pool", bq))
                    if not specs:
                        res["blockers"].append("调出、调入两边都不在本系统管账，没有库存要记")
        if l["inventory"] == "post" and res["blockers"]:
            specs = None  # 有问题的行不动库存（也不冲回已记的，避免半截操作）
        if specs is not None:
            _deltas(db, sim, rec, l["id"], specs, res, negative_reason)
        if res["mode"] == "post" and sku and not res["blockers"]:
            for wid in {e[1] for e in specs or []}:
                s, i = related(db, rid, h, sku.id, wid, 1 if any(x[3] > 0 for x in specs if x[1] == wid) else -1)
                res["suspects"] += s
                res["info"] += i
        out_lines.append(res)
    for r in out_lines:  # 给人看的变动前后
        for dlt in r["deltas"]:
            dlt["warehouse"] = whs[dlt["warehouse_id"]].name if dlt["warehouse_id"] in whs else dlt["warehouse_id"]
    lines_sum = sum((D(l.get("amount")) for l in lines if l.get("amount") is not None), Decimal(0))
    warn = []
    if h.get("total_amount") is not None and abs(lines_sum - D(h["total_amount"])) > Decimal("0.05"):
        warn.append(f"明细金额合计 {q4(lines_sum):g}，与单据总额 {h['total_amount']:g} 不一致（可能含税 / 运费 / 折扣，核对一下）")
    if h.get("payment_method") == "personal" and not h.get("paid_by"):
        warn.append("个人垫付请写是谁付的（以后报销用）")
    posts = [r for r in out_lines if r["deltas"]]
    blockers = [] if (h.get("reason") or "").strip() else ["请写补录原因"]
    return {"lines": out_lines, "dups": doc_dups(db, rid, h), "warnings": warn, "blockers": blockers,
            "posting_lines": len(posts), "lines_sum": q4(lines_sum),
            "line_blockers": sum(1 for r in out_lines if r["mode"] == "post" and r["blockers"]),
            "suspect_lines": [r["line_id"] for r in posts if r["suspects"]]}


def _deltas(db, sim: Sim, rec, line_id, specs, res, negative_reason=""):
    """这一行“应该记的库存”与“已经记了的”之间的差额（首次确认时已记为 0；修改时只记差额；作废时全部冲回）。"""
    cur = _ledger_for(rec, line_id)
    raw = []  # (sku_id, wh_id, lot, qty)

    def short(sid, wid, need, have):
        name = res["sku"] or res["name"]
        if negative_reason:
            res["info"].append(f"「{name}」账面只有 {q4(have):g}，按例外记成负数（{negative_reason}）")
            return True
        res["blockers"].append(f"库存不足：「{name}」账面只有 {q4(have):g} {res['base_unit']}，要扣 {q4(need):g}。"
                               "先核对是否漏记了收货（可在同一张补录里补），或写例外原因后按负数记")
        return False
    for sid, wid, lot, q in specs:
        same = sorted([(k, v) for k, v in cur.items() if k[0] == sid and k[1] == wid and (v > 0) == (q > 0)], key=lambda kv: str(kv[0][2]))
        if lot == "pool":
            for k, _ in same:
                cur.pop(k)
            diff = q - sum((v for _, v in same), Decimal(0))
            if diff < 0 and q < 0:
                alloc, miss = sim.fifo(sid, wid, -diff) if sid != "new" else ([], -diff)
                raw += [(sid, wid, lid, x) for lid, x in alloc]
                if miss > 0 and short(sid, wid, -diff, -diff - miss):
                    raw.append((sid, wid, None, -miss))
            elif diff > 0 and q > 0:  # 调入：跟着调出那边扣到的 Lot 走（能追溯）
                src = [(lt, -x) for s2, _, lt, x in raw if s2 == sid and x < 0]
                left = diff
                for i, (lid, amt) in enumerate(src):
                    take = left if i == len(src) - 1 else min(left, amt)
                    raw.append((sid, wid, lid, take))
                    left -= take
                if not src:
                    raw.append((sid, wid, None, diff))
            else:  # 往回退：从最近记的那几笔退
                left = -diff if q < 0 else diff
                for k, v in reversed(same):
                    if left == 0:
                        break
                    back = min(abs(v), abs(left))
                    raw.append((sid, wid, k[2], back if q < 0 else -back))
                    left = left - back if left > 0 else left + back
        else:
            have = cur.pop((sid, wid, lot), Decimal(0))
            diff = q - have
            if diff < 0 and lot and not str(lot).startswith("new:"):
                lt = db.get(Lot, lot)
                if lt and lt.quality_status != "released":
                    res["blockers"].append(f"Lot {lt.lot_code} 未放行（{lt.quality_status}），不能扣")
                avail = sim.lot(sid, wid, lot)
                if avail < -diff and not short(sid, wid, -diff, avail):
                    continue
            if diff:
                raw.append((sid, wid, lot, diff))
    for (sid, wid, lot), v in cur.items():  # 不再需要的旧记账：冲回
        raw.append((sid, wid, lot, -v))
    if res["blockers"]:
        return
    for sid, wid, lot, q in raw:
        if q == 0:
            continue
        before = sim.total(sid, wid) if sid != "new" else Decimal(0)
        code = lot[4:] if isinstance(lot, str) and lot.startswith("new:") else (db.get(Lot, lot).lot_code if lot else "")
        sim.add(sid, wid, lot, q)
        res["deltas"].append({"sku_id": sid, "warehouse_id": wid, "lot": lot, "lot_code": code, "new_lot": isinstance(lot, str) and lot.startswith("new:"),
                              "qty": q4(q), "before": q4(before), "after": q4(before + q)})
        res["effects"].append(f"{'+' if q > 0 else ''}{q4(q):g} {res['base_unit']}" + (f"（Lot {code}）" if code else ""))


# ---------- 执行 ----------
CONFIRM_ACK = "我已核对"


def _now():
    return datetime.now(timezone.utc)


def _log(rec, u: User, action: str, reason: str = "", changes=None, source: str = "web"):
    rec.log = (rec.log or []) + [{"at": _now().isoformat(), "by": u.id, "by_name": u.display_name, "action": action,
                                  "reason": reason, "changes": changes or {}, "source": source}]


def _diff(old_h: dict, new_h: dict, old_lines: list, new_lines: list) -> dict:
    ch = {k: [old_h.get(k), new_h.get(k)] for k in HEADER_FIELDS if old_h.get(k) != new_h.get(k)}
    if (old_h.get("links") or []) != (new_h.get("links") or []):
        ch["links"] = [[x["label"] for x in old_h.get("links") or []], [x["label"] for x in new_h.get("links") or []]]
    o, n = {l["id"]: l for l in old_lines}, {l["id"]: l for l in new_lines}
    lc = {}
    for i in o.keys() | n.keys():
        if i not in n:
            lc[i] = {"removed": o[i].get("name")}
        elif i not in o:
            lc[i] = {"added": n[i].get("name")}
        else:
            d = {k: [o[i].get(k), n[i].get(k)] for k in LINE_FIELDS if o[i].get(k) != n[i].get(k)}
            if d:
                lc[i] = d
    if lc:
        ch["lines"] = lc
    return ch


def _gate(db, p: dict, acknowledged: list, ack_duplicates: bool):
    """确认前的拦截：有问题的行不能记库存；疑似重复要人逐行确认“确实没记过”。"""
    if p["blockers"]:
        raise HTTPException(400, "；".join(p["blockers"]))
    bad = [f"{r['name']}：{'；'.join(r['blockers'])}" for r in p["lines"] if r["mode"] == "post" and r["blockers"]]
    if bad:
        raise HTTPException(409, {"msg": "这些行不能记库存（改成「只补单据」，或先把问题解决）：" + " | ".join(bad), "plan": p})
    miss = [r["name"] for r in p["lines"] if r["line_id"] in p["suspect_lines"] and r["line_id"] not in (acknowledged or [])]
    if miss:
        raise HTTPException(409, {"msg": f"这些行疑似已经记过库存，请逐行核对后勾选「确实没记过」：{'、'.join(miss)}", "plan": p})
    if p["dups"] and p["posting_lines"] and not ack_duplicates:
        raise HTTPException(409, {"msg": "疑似重复单据：" + "；".join(p["dups"]) + "。确认不是同一张后再勾选", "plan": p})


def _create_skus(db, u, rec, lines):
    from .routers.inventory import SkuIn, create_sku
    from . import usage as us
    from .routers.batches import next_sku_code
    for l in lines:
        if l.get("sku_id") or not l.get("new_sku"):
            continue
        ns = l["new_sku"]
        s = find_sku(db, ns["name"])
        if not s:
            use = us.normalize(ns.get("usage")) if ns.get("usage") is not None else us.default_usage(ns["category"])
            s = Sku(code=next_sku_code(db), name=ns["name"], category=ns["category"], base_unit=ns["base_unit"],
                    conversions=ns.get("conversions") or {}, usage=use or "", catch_weight=ns["base_unit"] in ("磅", "lb"))
            db.add(s)
            db.flush()
            audit(db, u, "sku.created", "sku", s.id, code=s.code, name=s.name, via=f"历史补录 {rec.code}")
        l["sku_id"] = s.id
        l.pop("new_sku", None)


def _post(db, u, rec, p: dict, rev: int, label: str):
    """把预览里的差额记成一笔库存流水（幂等键 backfill:{id}:r{rev}；重复提交不会再记）。"""
    from .routers.inventory import Dup, begin_posting, get_or_create_lot, txn
    rows = [(r, d) for r in p["lines"] for d in r["deltas"]]
    if not rows:
        return None
    try:
        post = begin_posting(db, u, f"backfill:{rec.id}:r{rev}", "backfill", "backfill", rec.id, f"{rec.code} {label} {KINDS[rec.kind]}")
    except Dup:
        raise HTTPException(409, "这次变动已经记过（重复提交），刷新看最新状态")
    post.effective_at = rec.business_date
    ledger = list(rec.ledger or [])
    for r, d in rows:
        sid = d["sku_id"] if d["sku_id"] != "new" else next(l["sku_id"] for l in rec.lines if l["id"] == r["line_id"])
        lot = d["lot"]
        if isinstance(lot, str) and lot.startswith("new:"):
            lot = get_or_create_lot(db, sid, lot[4:]).id
        txn(db, post, sid, d["warehouse_id"], "on_hand", d["qty"], lot)
        ledger.append({"rev": rev, "posting_id": post.id, "line_id": r["line_id"], "sku_id": sid, "warehouse_id": d["warehouse_id"],
                       "lot_id": lot, "qty": d["qty"]})
    rec.ledger = ledger
    return post.id


def state(rec: HistoricalBackfill) -> dict:
    h = {k: getattr(rec, k) for k in HEADER_FIELDS}
    h["links"] = rec.links or []
    return h


def create(db, u, data: dict, source="web") -> HistoricalBackfill:
    require_admin(u)
    h = clean_header(db, data)
    lines = clean_lines(db, h["kind"], data.get("lines") or [])
    n = db.query(HistoricalBackfill).filter(HistoricalBackfill.code.like(f"HB-{datetime.now().strftime('%y%m%d')}-%")).count() + 1
    rec = HistoricalBackfill(code=f"HB-{datetime.now().strftime('%y%m%d')}-{n:03d}", **h, lines=lines,
                             links=[resolve_link(db, x) for x in data.get("links") or []], created_by=u.id, source=source)
    db.add(rec)
    db.flush()
    _log(rec, u, "created", h["reason"], source=source)
    audit(db, u, "backfill.created", "backfill", rec.id, kind=h["kind"], source=source)
    return rec


def _check_version(rec, version):
    if version is not None and int(version) != rec.version:
        raise HTTPException(409, f"这张补录单已被修改过（现在是第 {rec.version} 版），请刷新后再操作")


def confirm(db, u, rec, version, acknowledged=None, ack_duplicates=False, negative_reason="") -> dict:
    require_admin(u)
    if rec.status == "confirmed":
        return {"already": True}
    if rec.status != "draft":
        raise HTTPException(409, "已作废的补录单不能确认")
    _check_version(rec, version)
    p = plan(db, rec, state(rec), rec.lines, negative_reason)
    _gate(db, p, acknowledged, ack_duplicates)
    lines = [dict(l) for l in rec.lines]
    _create_skus(db, u, rec, lines)
    rec.lines = lines
    pid = _post(db, u, rec, p, 1, "补录")
    rec.status, rec.confirmed_by, rec.confirmed_at, rec.version, rec.updated_at = "confirmed", u.id, _now(), rec.version + 1, _now()
    _log(rec, u, "confirmed", rec.reason, {"posting": pid, "acknowledged": acknowledged or [], "negative_reason": negative_reason,
                                           "stock": [{"line": r["name"], "effects": r["effects"]} for r in p["lines"] if r["effects"]]})
    audit(db, u, "backfill.confirmed", "backfill", rec.id, posting=pid, lines=len(rec.lines), posted_lines=p["posting_lines"])
    return {"posting_id": pid, "plan": p}


def proposed_state(db, rec, changes: dict) -> tuple[dict, list]:
    h = clean_header(db, {**state(rec), **{k: v for k, v in (changes or {}).items() if k in HEADER_FIELDS}})
    h["links"] = [resolve_link(db, x) for x in changes["links"]] if "links" in (changes or {}) else (rec.links or [])
    lines = clean_lines(db, h["kind"], changes["lines"], rec.lines) if "lines" in (changes or {}) else rec.lines
    if rec.status == "confirmed" and h["kind"] != rec.kind:
        raise HTTPException(400, "已确认的补录单不能改补录类型；作废后重新补录")
    return h, lines


def revise(db, u, rec, changes: dict, reason: str, version, acknowledged=None, ack_duplicates=False, negative_reason="",
           preview_only=False, source="web") -> dict:
    """草稿：直接改。已确认：写修改原因，库存只按差额记新流水（旧流水不动）。"""
    require_admin(u)
    if rec.status == "voided":
        raise HTTPException(409, "已作废的补录单不能修改")
    _check_version(rec, version)
    h, lines = proposed_state(db, rec, changes)
    p = plan(db, rec, h, lines, negative_reason)
    ch = _diff(state(rec), h, rec.lines, lines)
    if preview_only:
        return {"plan": p, "changes": ch}
    if rec.status == "confirmed":
        if not (reason or "").strip():
            raise HTTPException(400, "修改已确认的补录单要写修改原因")
        _gate(db, {**p, "blockers": []}, acknowledged, ack_duplicates)
    if not ch:
        return {"plan": p, "changes": {}, "unchanged": True}
    lines = [dict(l) for l in lines]
    pid = None
    for k in HEADER_FIELDS:
        setattr(rec, k, h[k])
    rec.links = h["links"]
    if rec.status == "confirmed":
        _create_skus(db, u, rec, lines)
        rec.lines = lines
        pid = _post(db, u, rec, p, rec.version + 1, "修改差额")
    else:
        rec.lines = lines
    rec.version, rec.updated_at = rec.version + 1, _now()
    _log(rec, u, "revised", reason, {**ch, "posting": pid, "stock": [{"line": r["name"], "effects": r["effects"]} for r in p["lines"] if r["effects"]]}, source)
    audit(db, u, "backfill.revised", "backfill", rec.id, posting=pid, reason=reason, changes=list(ch))
    return {"plan": p, "changes": ch, "posting_id": pid}


def void(db, u, rec, reason: str, version, preview_only=False, source="web") -> dict:
    """作废：草稿直接作废；已记库存的按原数冲回（新记一笔冲销流水），单据和日志都保留。"""
    require_admin(u)
    if rec.status == "voided":
        return {"already": True}
    _check_version(rec, version)
    p = None
    if rec.status == "confirmed":
        empty = [{**l, "inventory": "doc_only"} for l in rec.lines]
        p = plan(db, rec, state(rec), empty, negative_reason="作废冲回")
        if preview_only:
            return {"plan": p}
    if preview_only:
        return {"plan": None}
    if not (reason or "").strip():
        raise HTTPException(400, "作废要写原因")
    pid = _post(db, u, rec, p, rec.version + 1, "作废冲回") if p else None
    rec.status, rec.voided_by, rec.voided_at, rec.void_reason = "voided", u.id, _now(), reason.strip()
    rec.version, rec.updated_at = rec.version + 1, _now()
    _log(rec, u, "voided", reason, {"posting": pid, "stock": [{"line": r["name"], "effects": r["effects"]} for r in (p or {}).get("lines", []) if r["effects"]]}, source)
    audit(db, u, "backfill.voided", "backfill", rec.id, posting=pid, reason=reason)
    return {"plan": p, "posting_id": pid}


# ---------- ChatGPT 提出的修改 / 作废：存着，等人在系统里确认 ----------
def propose_pending(db, u, rec, changes: dict | None, reason: str, void_it=False, source="agent") -> dict:
    require_admin(u)
    if rec.status == "voided":
        raise HTTPException(409, "已作废的补录单不能再改")
    if not (reason or "").strip():
        raise HTTPException(400, "请写修改 / 作废原因")
    if not void_it:
        proposed_state(db, rec, changes or {})  # 先校验一遍格式
    rec.pending = {"void": bool(void_it), "changes": changes or {}, "reason": reason, "by": u.id, "by_name": u.display_name,
                   "at": _now().isoformat(), "source": source, "base_version": rec.version}
    _log(rec, u, "change_proposed", reason, {"void": bool(void_it), "fields": list((changes or {}).keys())}, source)
    return rec.pending


def apply_pending(db, u, rec, version, acknowledged=None, ack_duplicates=False, negative_reason="") -> dict:
    pd = rec.pending or {}
    if not pd:
        raise HTTPException(409, "没有待确认的修改")
    if pd.get("base_version") != rec.version:
        raise HTTPException(409, "提出修改之后这张单又被改过，请让 ChatGPT 按最新版本重新提交，或放弃这条提议")
    reason = f"{pd['reason']}（{pd['by_name']} 经 {pd['source']} 提出）"
    if pd["void"]:
        r = void(db, u, rec, reason, version)
    elif rec.status == "draft":
        r = revise(db, u, rec, pd["changes"], reason, version)
    else:
        r = revise(db, u, rec, pd["changes"], reason, version, acknowledged, ack_duplicates, negative_reason)
    rec.pending = {}
    return r


def out(db, rec: HistoricalBackfill, with_plan=True) -> dict:
    whs = {w.id: w for w in db.query(Warehouse)}
    names = {x.id: x.display_name for x in db.query(User)}
    skus = {s.id: s for s in db.query(Sku).filter(Sku.id.in_({l.get("sku_id") for l in rec.lines if l.get("sku_id")} | {e["sku_id"] for e in rec.ledger or []}))}
    lots = {l.id: l.lot_code for l in db.query(Lot).filter(Lot.id.in_({e["lot_id"] for e in rec.ledger or [] if e["lot_id"]}))}
    posted = {}
    for e in rec.ledger or []:
        k = (e["line_id"], e["sku_id"], e["warehouse_id"], e["lot_id"])
        posted[k] = posted.get(k, Decimal(0)) + D(e["qty"])
    d = {k: getattr(rec, k) for k in HEADER_FIELDS}
    d.update({"id": rec.id, "code": rec.code, "status": rec.status, "kind_label": KINDS[rec.kind],
              "goods_status_label": GOODS_STATUS.get(rec.goods_status, ""), "payment_label": PAY.get(rec.payment_method, rec.payment_method),
              "warehouse": whs[rec.warehouse_id].name if rec.warehouse_id in whs else "",
              "from_warehouse": whs[rec.from_warehouse_id].name if rec.from_warehouse_id in whs else "",
              "links": rec.links or [], "lines": [{**l, "sku": skus[l["sku_id"]].name if l.get("sku_id") in skus else None,
                                                  "base_unit": skus[l["sku_id"]].base_unit if l.get("sku_id") in skus else (l.get("new_sku") or {}).get("base_unit")}
                                                 for l in rec.lines],
              "posted": [{"line_id": k[0], "sku": skus[k[1]].name if k[1] in skus else k[1], "warehouse": whs[k[2]].name if k[2] in whs else k[2],
                          "lot": lots.get(k[3], ""), "qty": q4(v)} for k, v in posted.items() if v != 0],
              "pending": rec.pending or {}, "log": rec.log or [], "source": rec.source, "version": rec.version,
              "created_by": names.get(rec.created_by, ""), "created_at": rec.created_at,
              "confirmed_by": names.get(rec.confirmed_by, ""), "confirmed_at": rec.confirmed_at,
              "voided_by": names.get(rec.voided_by, ""), "voided_at": rec.voided_at, "void_reason": rec.void_reason})
    if with_plan and rec.status == "draft":
        d["plan"] = plan(db, rec, state(rec), rec.lines)  # 草稿没有记过库存，传 rec 是为了查重时排除自己
    return d
