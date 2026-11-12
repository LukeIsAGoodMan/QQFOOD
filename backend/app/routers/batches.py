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
from ..models import BatchProposal, CustomerItem, InventoryTxn, Lot, SafetyStock, Sku, User, Warehouse
from .inventory import Dup, TLineIn, TransferIn, balance, begin_posting, create_transfer, get_or_create_lot, txn

router = APIRouter(prefix="/api/batches", tags=["batches"])

SITE_TZ = ZoneInfo("America/Los_Angeles")
from ..categories import SKU_CATEGORIES, UNCATEGORIZED, FOOD, normalize as norm_category
KIND_LABEL = {"sku_upsert": "商品主档", "stock_count": "期初 / 盘点数量", "inbound": "调入（外库 / 公司）", "order": "客户订单（PO）",
              "alias_upsert": "商品对照（一物多码）", "sku_merge": "合并商品"}
KIND_PERM = {"sku_upsert": "inventory.master", "stock_count": "inventory.post", "inbound": "inventory.transfer", "order": "order.manage",
             "alias_upsert": "inventory.master", "sku_merge": "inventory.master"}
MAX_ROWS = 300


def _now():
    return datetime.now(timezone.utc)


def _norm(s) -> str:
    return "".join(str(s or "").lower().split())


# ---------- analysis (pure: never writes) ----------
def _tnt_no(db, tnt, sku_id) -> str:
    ci = db.query(CustomerItem).filter(CustomerItem.customer_id == tnt.id, CustomerItem.sku_id == sku_id, CustomerItem.item_no != "").first()
    return ci.item_no if ci else ""


def next_sku_code(db) -> str:
    import re as _re
    n = max([int(m.group(1)) for (c,) in db.query(Sku.code) if (m := _re.fullmatch(r"QQ-(\d+)", c))] or [0]) + 1
    return f"QQ-{n:04d}"


def _analyze_sku(db: Session, rows: list[dict]):
    from ..models import Customer
    tnt = db.query(Customer).filter_by(code="TNT").first()
    skus = {s.code: s for s in db.query(Sku)}
    by_name = {_norm(s.name): s for s in skus.values()}
    by_barcode = {s.barcode: s for s in skus.values() if s.barcode}
    whs = {w.code: w for w in db.query(Warehouse)}
    seen, out = set(), []
    for r in rows:
        code = str(r.get("code") or "").strip().upper()
        name = str(r.get("name") or "").strip()
        if not code and name and _norm(name) in by_name:  # 以品名为主：没给编码就按品名找已有商品
            code = by_name[_norm(name)].code
        res = {"key": name or (skus[code].name if code in skus else code), "code": code, "action": "error", "messages": [], "changes": {}}
        ident = code or _norm(name)
        if not ident:
            res["messages"].append("缺少品名")
        elif ident in seen:
            res["messages"].append("本批重复")
        seen.add(ident)
        given = r.get("category")
        cat = norm_category(given, name or (skus[code].name if code in skus else "")) if given else (skus[code].category if code in skus else UNCATEGORIZED)
        if cat is None:
            res["messages"].append(f"分类需为 {'/'.join(SKU_CATEGORIES)}")
        conv = r.get("conversions") or {}
        if not isinstance(conv, dict) or any(not isinstance(v, (int, float)) or v <= 0 for v in conv.values()):
            res["messages"].append("单位换算需为 {单位: 每个基本单位等于多少} 且大于 0")
            conv = {}
        for wc in (r.get("safety_stock") or {}):
            w = whs.get(str(wc).upper())
            if not w or not w.tracked:
                res["messages"].append(f"安全库存的仓库 {wc} 不存在或不管账")
        tnt_no = str(r.get("tnt_item_no") or r.get("item_no") or "").strip()
        if tnt_no and tnt:
            other = db.query(CustomerItem).filter_by(customer_id=tnt.id, item_no=tnt_no).first()
            if other and (code not in skus or other.sku_id != skus[code].id):
                res["messages"].append(f"大统华 Item# {tnt_no} 已对到「{db.get(Sku, other.sku_id).name}」")
        if res["messages"]:
            out.append(res)
            continue
        cur = skus.get(code)
        cur_tnt = _tnt_no(db, tnt, cur.id) if (cur and tnt) else ""
        if tnt_no and tnt_no != cur_tnt:
            res["changes"]["大统华 Item#"] = [cur_tnt or None, tnt_no]
        if cur:
            want = {"name": r.get("name") or cur.name, "category": cat, "base_unit": r.get("base_unit") or cur.base_unit,
                    "conversions": {**(cur.conversions or {}), **conv}, "barcode": r.get("barcode") if r.get("barcode") is not None else cur.barcode,
                    "purchase_pack": r.get("purchase_pack") if r.get("purchase_pack") is not None else cur.purchase_pack,
                    "ship_pack": r.get("ship_pack") if r.get("ship_pack") is not None else cur.ship_pack}
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
            if cat == UNCATEGORIZED:
                res["messages"].append("还没分类（确认后可在库存页补）")
        else:
            if not r.get("name"):
                res["messages"].append("新 SKU 需要名称")
                out.append(res)
                continue
            twin = by_name.get(_norm(r.get("name"))) or (by_barcode.get(r.get("barcode")) if r.get("barcode") else None)
            res["action"] = "duplicate" if twin else "new"
            if twin:
                res["messages"].append(f"疑似与已有 {twin.code} {twin.name} 重复（默认不勾选）")
            res["changes"] = {**res["changes"], **{k: [None, v] for k, v in {"name": r.get("name"), "category": cat, "base_unit": r.get("base_unit") or "箱",
                                                         "conversions": conv, "barcode": r.get("barcode"), "purchase_pack": r.get("purchase_pack"),
                                                         "ship_pack": r.get("ship_pack")}.items() if v not in (None, {}, "")}}
            if not code:
                res["messages"].append("没给编码：系统自动编号，平时按品名和大统华 Item# 认")
            if cat == UNCATEGORIZED:
                res["messages"].append("还没分类（确认后可在库存页补）")
            for wc, q in (r.get("safety_stock") or {}).items():
                res["changes"][f"安全库存@{str(wc).upper()}"] = [None, q]
        out.append(res)
    return out


def _find_wh(whs: dict, ref) -> Warehouse | None:
    r = str(ref or "").strip()
    if not r:
        return None
    from .orders import norm_text
    return whs.get(r.upper()) or next((w for w in whs.values() if norm_text(r) in (norm_text(w.code), norm_text(w.name))), None)


def _row_key(r: dict) -> str:
    code = r.get("party_code") or r.get("item_code") or r.get("sku_code") or ""
    name = r.get("party_name") or r.get("description") or ""
    return f"{code} {name}".strip()


def _resolve(db, r, w, skus, res):
    """一行 → 我们的 SKU（含对照）；没对上时标“未配对”，确认页里选一次就记住。返回 (sku, alias, how)。"""
    from . import aliases as al
    m = al.resolve_row(db, r, "warehouse", w.code if w else "", skus)
    if m["how"] == "alias_other":
        res["messages"].append(f"按 {m['alias'].party} 的对照「{m['alias'].code}」认作「{m['sku'].name}」，确认后也记到 {w.code if w else ''}")
    elif m["how"] == "alias_other_ci":
        res["messages"].append(f"按大统华 Item# 认作「{m['sku'].name}」，确认后也记到 {w.code if w else ''}")
    elif m["how"] == "name_contains":
        res["messages"].append(f"按品名认作「{m['sku'].name}」，请核对；确认后记住")
    elif m["how"] == "sku" and (r.get("party_code") or r.get("party_name")):
        res["messages"].append(f"确认后记住：{w.code if w else ''} 的「{r.get('party_code') or r.get('party_name')}」= {m['sku'].code}")
    return m["sku"], m["alias"], m["how"]


def _should_remember(r, how) -> bool:
    if how in ("alias_other", "alias_other_ci", "name_contains"):
        return True
    return how in ("sku", "name_exact") and bool(r.get("party_code") or (r.get("party_name") and how == "sku"))


def _unmapped(res, w, r, qty, unit, sku=None, why=None):
    res["action"] = "unmapped"
    res["messages"].append(why or f"{w.code if w else ''} 的「{_row_key(r) or '（无编码）'}」还没对上我们的 SKU：在这里选一次，确认后自动记住")
    res["changes"] = {"数量": [None, f"{qty:g} {unit or ''}".strip()]}
    if sku:
        res["sku_id"] = sku.id


def _analyze_stock(db: Session, rows: list[dict]):
    from . import aliases as al
    skus = {s.code: s for s in db.query(Sku)}
    whs = {w.code: w for w in db.query(Warehouse)}
    out, seen = [], set()
    for r in rows:
        wc = str(r.get("warehouse_code") or "WH597").strip()
        w = _find_wh(whs, wc)
        lot_code = (str(r.get("lot_code")).strip() if r.get("lot_code") else None)
        res = {"key": f"{_row_key(r)} @ {w.code if w else wc.upper()}" + (f" · {lot_code}" if lot_code else ""), "action": "error", "messages": [], "changes": {}}
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
        if res["messages"]:
            out.append(res)
            continue
        sku, alias, how = _resolve(db, r, w, skus, res)
        unit = r.get("unit")
        if not sku:
            _unmapped(res, w, r, qty, unit)
            out.append(res)
            continue
        qty_b, err = al.to_base_qty(sku, qty, unit, alias)
        if err:
            _unmapped(res, w, r, qty, unit, sku, err)
            out.append(res)
            continue
        if (sku.id, w.code, lot_code) in seen:
            res["action"] = "error"
            res["messages"].append("本批重复（同 SKU、仓库、Lot）")
            out.append(res)
            continue
        seen.add((sku.id, w.code, lot_code))
        res["sku_id"], res["remember"] = sku.id, _should_remember(r, how)
        _stock_changes(db, res, sku, w, lot_code, qty_b, r.get("counted", True) is not False)
        out.append(res)
    return out


def _stock_changes(db, res, sku, w, lot_code, qty, counted):
    lot = db.query(Lot).filter_by(sku_id=sku.id, lot_code=lot_code).first() if lot_code else None
    lot_id = lot.id if lot else None
    fresh = bool(lot_code and not lot)
    cur_on = 0.0 if fresh else balance(db, sku.id, w.id, "on_hand", lot_id)
    cur_unv = 0.0 if fresh else balance(db, sku.id, w.id, "unverified", lot_id)
    res["changes"] = {"SKU": [None, f"{sku.code} {sku.name}"]} if res.get("key", "").split(" @ ")[0] != sku.code else {}
    if counted:
        if cur_on != qty:
            res["changes"]["实物"] = [cur_on, qty]
        if cur_unv:
            res["changes"]["未核数"] = [cur_unv, 0]
    elif cur_unv != qty:
        res["changes"]["未核数（估计）"] = [cur_unv, qty]
    moved = any(k in res["changes"] for k in ("实物", "未核数", "未核数（估计）"))
    res.update({"action": "set" if moved else "unchanged", "unit": sku.base_unit, "qty_base": qty, "counted": counted})
    if not lot_code and sku.category in FOOD:
        res["messages"].append("无 Lot（可确认，追溯时会缺批次）")


def _analyze_inbound(db: Session, meta: dict, rows: list[dict]):
    from . import aliases as al
    skus = {s.code: s for s in db.query(Sku)}
    whs = {w.code: w for w in db.query(Warehouse)}
    src, dst = _find_wh(whs, meta.get("from_code")), _find_wh(whs, meta.get("to_code") or "WH597")
    header = []
    if not src:
        header.append(f"来源地 {meta.get('from_code')} 不存在")
    if not dst or not dst.tracked:
        header.append("目的仓不存在或不管账")
    if src and dst and src.id == dst.id:
        header.append("来源与目的仓相同")
    out, need = [], {}
    for r in rows:
        lot_code = (str(r.get("lot_code")).strip() if r.get("lot_code") else None)
        res = {"key": _row_key(r) + (f" · {lot_code}" if lot_code else ""), "action": "error", "messages": list(header), "changes": {}}
        try:
            qty = float(r.get("qty"))
            if qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            res["messages"].append("数量需大于 0")
            qty = None
        if res["messages"]:
            out.append(res)
            continue
        sku, alias, how = _resolve(db, r, src, skus, res)
        if not sku:
            _unmapped(res, src, r, qty, r.get("unit"))
            out.append(res)
            continue
        qty_b, err = al.to_base_qty(sku, qty, r.get("unit"), alias)
        if err:
            _unmapped(res, src, r, qty, r.get("unit"), sku, err)
            out.append(res)
            continue
        res.update({"action": "mapped" if how == "alias" else "new", "qty_base": qty_b, "unit": sku.base_unit, "sku_id": sku.id,
                    "remember": _should_remember(r, how),
                    "changes": {"SKU": [None, f"{sku.code} {sku.name}"], "在途": [None, qty_b]}})
        if src and src.tracked:
            k = (sku.id, lot_code)
            need[k] = need.get(k, 0) + qty_b
            short = _source_short(db, src, sku, lot_code, need[k])
            if short > 1e-9:
                res["short"] = short
                res["messages"].append(f"{src.code} 账面不够：差 {short:g} {sku.base_unit}（确认时填原因可按单据补记）")
        out.append(res)
    return out, src, dst


def _source_short(db, w, sku, lot_code, qty) -> float:
    """来源仓账面差多少（未分批次的库存可以认作这个批次）。"""
    lot = db.query(Lot).filter_by(sku_id=sku.id, lot_code=lot_code).first() if lot_code else None
    have = balance(db, sku.id, w.id, "on_hand", lot.id) if lot else 0.0
    if lot_code:
        have += max(balance(db, sku.id, w.id, "on_hand", None), 0)
    else:
        have = balance(db, sku.id, w.id, "on_hand", None)
    return round(max(qty - have, 0), 4)


def _analyze_alias(db: Session, rows: list[dict]):
    from . import aliases as al
    from .orders import find_customer, match_item, norm_text
    skus = {s.code: s for s in db.query(Sku)}
    out, seen = [], set()
    for r in rows:
        sc = str(r.get("sku_code") or "").strip().upper()
        party = str(r.get("party") or "").strip()
        kind = r.get("party_kind") or (al.guess_kind(db, party) if party else "")
        code, name = str(r.get("code") or "").strip(), str(r.get("name") or "").strip()
        res = {"key": f"{party} · {code or name}", "action": "error", "messages": [], "changes": {}}
        sku = skus.get(sc)
        if not sku:
            res["messages"].append(f"我们的 SKU {sc or '（未填）'} 不存在：对照只能挂在我们自己的 SKU 上")
        if kind not in al.KINDS:
            res["messages"].append("party_kind 需为 customer / warehouse / supplier / accounting / other")
        if not party and kind != "accounting":
            res["messages"].append("缺少对方（party），如 SUPREME、T&T、供应商名、QuickBooks")
        if not (code or name):
            res["messages"].append("对方编码和品名至少填一个")
        f = r.get("factor")
        if f is not None and (not isinstance(f, (int, float)) or f <= 0):
            res["messages"].append("factor 需大于 0（对方 1 个单位 = 多少个我们的基本单位）")
        if kind == "warehouse" and party and not _find_wh({w.code: w for w in db.query(Warehouse)}, party):
            res["messages"].append(f"仓库 {party} 不存在")
        cust = find_customer(db, party) if kind == "customer" else None
        if kind == "customer" and not cust:
            res["messages"].append(f"客户「{party}」未登记")
        dkey = (kind, norm_text(party), (code or name).upper())
        if dkey in seen:
            res["messages"].append("本批重复")
        seen.add(dkey)
        if res["messages"]:
            out.append(res)
            continue
        if kind == "customer":
            cur = match_item(db, cust.id, code, name)
            cur_sku = db.get(Sku, cur.sku_id) if cur else None
            res["key"] = f"{cust.code} · {code or name}"
        else:
            canon = party if kind == "other" or kind == "supplier" else al.canonical_party(db, kind, party)
            cur = al.find_alias(db, kind, canon, code, None if code else name)
            cur_sku = db.get(Sku, cur.sku_id) if cur else None
            res["key"] = f"{canon} · {code or name}"
        res["party_kind"], res["kind_label"] = kind, al.KINDS[kind]
        want = {"SKU": sku.code, "编码": code, "品名": name, "单位": r.get("unit") or "", "换算": f}
        if not cur:
            res["action"] = "new"
            res["changes"] = {k: [None, v] for k, v in want.items() if v not in (None, "")}
        else:
            have = {"SKU": cur_sku.code if cur_sku else None, "编码": getattr(cur, "item_no", None) if kind == "customer" else cur.code,
                    "品名": cur.description if kind == "customer" else cur.name,
                    "单位": cur.order_unit if kind == "customer" else cur.unit, "换算": cur.factor}
            res["changes"] = {k: [have[k], v] for k, v in want.items() if v not in (None, "") and have[k] != v}
            res["action"] = "update" if res["changes"] else "unchanged"
            if cur_sku and cur_sku.id != sku.id:
                res["messages"].append(f"原来对到 {cur_sku.code} {cur_sku.name}，确认后改对到 {sku.code}")
        out.append(res)
    return out


def _name_match(db, text):
    from .aliases import match_sku_by_name
    return match_sku_by_name(db, text)


def fill_candidates(db: Session, meta: dict, an: list[dict]) -> list[dict]:
    """PO 到了，但货可能已经先送了：同一客户、还没填 PO 号、品项有重合的订单，作为“补到这张”的候选。"""
    from ..models import SalesOrder, SOLine
    from . import orders as o
    if not str(meta.get("po_no") or "").strip():
        return []
    cust = o.find_customer(db, meta.get("customer"))
    if not cust:
        return []
    want = {a.get("sku_id") for a in an if a.get("sku_id")}
    out = []
    for so in db.query(SalesOrder).filter(SalesOrder.customer_id == cust.id, SalesOrder.po_no == "", SalesOrder.status != "cancelled") \
            .order_by(SalesOrder.created_at.desc()).limit(20):
        lines = db.query(SOLine).filter_by(order_id=so.id).all()
        overlap = want & {l.sku_id for l in lines}
        if want and not overlap:
            continue
        out.append({"id": so.id, "code": so.code, "required_date": so.required_date, "status_label": o.STATUS_LABEL[so.status],
                    "lines": "；".join(f"{l.description} {l.qty_ordered:g}{l.order_unit}" for l in lines[:4]), "overlap": len(overlap)})
    out.sort(key=lambda x: -x["overlap"])
    return out


def _analyze_order(db: Session, meta: dict, rows: list[dict]):
    from . import orders as o
    cust = o.find_customer(db, meta.get("customer"))
    header, warn = [], []
    site = None
    if not cust:
        header.append(f"客户「{meta.get('customer')}」未登记")
    else:
        site = o.find_site(db, cust, meta.get("site"))
        if not site:
            header.append(f"送货点「{meta.get('site') or '未填'}」未登记，请先在订单页的客户资料里添加")
    po = str(meta.get("po_no") or "").strip()
    if not po:
        warn.append("没有 PO 号：先按送货单录入，PO 到了再补")
    if cust and po:
        dup = o.duplicate_of(db, cust.id, po, meta.get("po_date"), site.id if site else None)
        if dup:
            header.append(f"这张 PO 已录入：{dup.code}（不会重复建单）")
    if not meta.get("required_date"):
        warn.append("PO 上没有交货日期")
    skus = {s.code: s for s in db.query(Sku)}
    wh = db.query(Warehouse).filter_by(kind="main").first()
    out = []
    for r in rows:
        unit = o.norm_unit(r.get("unit"))
        res = {"key": f"{r.get('item_no') or ''} {r.get('description') or ''}".strip(), "action": "error", "messages": list(header), "changes": {}}
        try:
            qty, price = float(r.get("qty")), float(r.get("price") or 0)
            if qty <= 0 or price < 0:
                raise ValueError
        except (TypeError, ValueError):
            res["messages"].append("数量需大于 0、单价不为负")
            qty = price = None
        if not r.get("description"):
            res["messages"].append("缺少品名")
        if res["messages"]:
            out.append(res)
            continue
        res["messages"] += warn
        ci = o.match_item(db, cust.id, r.get("item_no"), r.get("description"))
        sku, factor = None, None
        if ci:
            sku, factor = db.get(Sku, ci.sku_id), ci.factor
            res["action"] = "mapped"
            if ci.last_price is not None and abs(ci.last_price - price) > 1e-6:
                res["messages"].append(f"单价与上次不同：上次 {ci.last_price}，这次 {price}")
        elif r.get("sku_code") and str(r["sku_code"]).strip().upper() in skus:
            sku = skus[str(r["sku_code"]).strip().upper()]
            factor = r.get("factor") or o.default_factor(sku, unit)
            if not factor:
                res["messages"].append(f"{sku.code} 没有「{unit}」换算：确认时请填每{unit}等于多少{sku.base_unit}")
            res["action"] = "new"
            res["messages"].append("首次配对：确认后记住，下次自动对上")
        elif (nm := _name_match(db, r.get("description")))[0]:
            sku, how = nm
            factor = r.get("factor") or o.default_factor(sku, unit)
            res["action"] = "mapped"
            res["messages"].append(f"按品名对上「{sku.name}」{'（请核对）' if how == 'contains' else ''}"
                                   + (f"；确认后记住大统华 Item# {r.get('item_no')}" if r.get("item_no") else ""))
            if not factor:
                res["messages"].append(f"没有「{unit}」换算：确认时请填每{unit}等于多少{sku.base_unit}")
        else:
            res["action"] = "unmapped"
            res["messages"].append("还没对上我们的 SKU：可在这里选，或确认后在订单里补")
        res["changes"] = {"数量": [None, f"{qty:g} {unit}"], "单价": [None, price], "金额": [None, round(qty * price, 2)]}
        if sku:
            res["changes"]["SKU"] = [None, sku.name + (f"（×{factor:g}）" if factor and factor != 1 else "")]
            res.update({"sku_id": sku.id, "factor": factor})
            if wh and factor:
                from .orders import available_base
                avail = available_base(db, sku.id, wh.id)
                if avail + 1e-9 < qty * factor:
                    res["messages"].append(f"库存不足：可用 {round(avail, 2)} {sku.base_unit}，需要 {round(qty * factor, 2)}")
        out.append(res)
    return out, cust, site


def find_sku_any(db: Session, text) -> Sku | None:
    """按我们的品名 / 内部编号 / 大统华 Item# / 任何一方记过的编码或品名找在用的商品（唯一才算）。"""
    from ..models import CustomerItem, SkuAlias
    t = str(text or "").strip()
    if not t:
        return None
    act = [x for x in db.query(Sku).filter(Sku.active.isnot(False))]
    hit = [x for x in act if x.code == t.upper() or _norm(x.name) == _norm(t)]
    if len(hit) == 1:
        return hit[0]
    ids = {c.sku_id for c in db.query(CustomerItem) if c.item_no and c.item_no.strip() == t}
    ids |= {a.sku_id for a in db.query(SkuAlias) if (a.code and _norm(a.code) == _norm(t)) or (a.name and _norm(a.name) == _norm(t))}
    ids &= {x.id for x in act}
    return db.get(Sku, ids.pop()) if len(ids) == 1 else None


def _analyze_merge(db: Session, rows: list[dict]):
    from .. import sku_merge
    out, seen = [], set()
    for r in rows:
        f, t = str(r.get("from") or "").strip(), str(r.get("into") or "").strip()
        res = {"key": f"{f} → {t}", "action": "error", "messages": [], "changes": {}}
        src, dst = find_sku_any(db, f), find_sku_any(db, t)
        if not src:
            res["messages"].append(f"找不到在用的商品「{f}」（只是国内叫法不同、没建成商品的话，用商品对照记下就行，不用合并）")
        if not dst:
            res["messages"].append(f"找不到在用的商品「{t}」")
        if not str(r.get("reason") or "").strip():
            res["messages"].append("缺少合并原因")
        if src and dst:
            res["messages"] += sku_merge.check(db, src, dst)
            if src.id in seen:
                res["messages"].append("本批重复")
            seen.add(src.id)
        if not res["messages"]:
            us = sku_merge.usage(db, src)
            res["action"] = "merge"
            res["src_id"], res["dst_id"] = src.id, dst.id
            res["changes"] = {"并入": [src.name, dst.name]}
            if us["has_stock"]:
                res["changes"]["库存转过去"] = [None, "、".join(f"{ {'on_hand': '实物', 'in_transit': '在途', 'unverified': '未核数'}.get(k, k)} {v}" for k, v in us["stock"].items())]
            refs = [f"{n} {lab}" for lab, n in (("订单行", us["order_lines"]), ("采购行", us["po_lines"]), ("调拨行", us["transfer_lines"]), ("对照", us["aliases"])) if n]
            if refs:
                res["changes"]["改指向"] = [None, "、".join(refs)]
            res["messages"].append(f"合并后「{src.name}」停用，它的品名 / 编号记成「{dst.name}」的旧称，以后单据上出现照样认得出")
        out.append(res)
    return out


def analyze(db: Session, b: BatchProposal):
    if b.kind == "sku_merge":
        return _analyze_merge(db, b.rows)
    if b.kind == "order":
        return _analyze_order(db, b.meta, b.rows)[0]
    if b.kind == "sku_upsert":
        return _analyze_sku(db, b.rows)
    if b.kind == "stock_count":
        return _analyze_stock(db, b.rows)
    if b.kind == "alias_upsert":
        return _analyze_alias(db, b.rows)
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
        if detail and b.kind == "order" and b.status == "proposed":
            d["fill_candidates"] = fill_candidates(db, b.meta, an)
    return d


# ---------- create ----------
class BatchIn(BaseModel):
    kind: str
    rows: list[dict]
    meta: dict = {}
    source_note: str = ""


def create_batch(db: Session, u: User, body: BatchIn, source: str = "manual") -> BatchProposal:
    if body.kind not in KIND_LABEL:
        raise HTTPException(400, f"kind 需为 {' / '.join(KIND_LABEL)}")
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
def _pick(db, b, an, i, body, ok_actions):
    """确认时这一行用哪个 SKU、多少基本单位：确认页手选的优先（并记住对照），否则用分析结果。"""
    from . import aliases as al
    a, r = an[i], b.rows[i]
    ov = body.overrides.get(str(i)) or body.overrides.get(i) or {}
    if ov.get("sku_id"):
        sku = db.get(Sku, ov["sku_id"])
        if not sku:
            return None
        f = ov.get("factor")
        if f:
            return sku, round(float(r["qty"]) * float(f), 4), float(f), True
        q, err = al.to_base_qty(sku, float(r["qty"]), r.get("unit"), None)
        return (sku, q, None, True) if not err else None
    if a["action"] in ok_actions and a.get("sku_id"):
        return db.get(Sku, a["sku_id"]), a["qty_base"], None, a.get("remember")
    return None


class ApplyIn(BaseModel):
    selected: list[int]
    note: str = ""
    overrides: dict = {}  # {行号: {sku_id, factor}}：在确认页给未配对的行选 SKU（订单 / 调入 / 盘点），确认后记住对照
    shortfall_reason: str = ""  # 调入：来源仓（外库）账面不够时按单据补记的原因
    fill_order_id: str | None = None  # 订单：这张 PO 补到之前没有 PO 的送货单上，而不是新建


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
            code = a["code"] or next_sku_code(db)
            sku = db.query(Sku).filter_by(code=code).first()
            if not sku:
                bu = r.get("base_unit") or "箱"
                sku = Sku(code=code, name=r["name"], category=(a["changes"].get("category") or [None, UNCATEGORIZED])[1], base_unit=bu,
                          conversions=r.get("conversions") or {}, barcode=r.get("barcode"), purchase_pack=r.get("purchase_pack"),
                          ship_pack=r.get("ship_pack"), catch_weight=bu in ("磅", "lb"))
                db.add(sku)
                db.flush()
            else:
                ch = a["changes"]
                for k in ("name", "category", "base_unit", "conversions", "barcode", "purchase_pack", "ship_pack"):
                    if k in ch:
                        setattr(sku, k, ch[k][1])
                if sku.base_unit in ("磅", "lb"):
                    sku.catch_weight = True
            for wc, q in (r.get("safety_stock") or {}).items():
                w = whs[str(wc).upper()]
                ss = db.query(SafetyStock).filter_by(sku_id=sku.id, warehouse_id=w.id).first()
                if ss:
                    ss.qty = q
                else:
                    db.add(SafetyStock(sku_id=sku.id, warehouse_id=w.id, qty=q))
            if "大统华 Item#" in a["changes"]:
                from .orders import set_tnt_item_no
                set_tnt_item_no(db, u, sku, a["changes"]["大统华 Item#"][1], r.get("tnt_name"))
            applied.append(i)
    elif b.kind == "stock_count":
        from . import aliases as al
        whs = {w.code: w for w in db.query(Warehouse)}
        for i in chosen:
            r = b.rows[i]
            pick = _pick(db, b, an, i, body, ("set",))
            w = _find_wh(whs, r.get("warehouse_code") or "WH597")
            if not pick or not w or not w.tracked:
                skipped.append(i)
                continue
            sku, qty_b, factor, remember = pick
            if remember:
                al.remember_from_row(db, u, r, "warehouse", w.code, sku, factor)
            lot = get_or_create_lot(db, sku.id, r.get("lot_code"))
            lot_id = lot.id if lot else None
            try:
                p = begin_posting(db, u, f"batch:{b.id}:{i}", "count", "batch", b.id, f"{b.code} {KIND_LABEL[b.kind]} {r.get('note') or ''}".strip())
            except Dup:
                skipped.append(i)
                continue
            on, unv = balance(db, sku.id, w.id, "on_hand", lot_id), balance(db, sku.id, w.id, "unverified", lot_id)
            if r.get("counted", True) is not False:
                txn(db, p, sku.id, w.id, "unverified", -unv, lot_id)
                txn(db, p, sku.id, w.id, "on_hand", qty_b - on, lot_id)
            else:
                txn(db, p, sku.id, w.id, "unverified", qty_b - unv, lot_id)
            applied.append(i)
    elif b.kind == "sku_merge":
        from .. import sku_merge
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] != "merge":
                skipped.append(i)
                continue
            try:
                res = sku_merge.merge(db, u, db.get(Sku, a["src_id"]), db.get(Sku, a["dst_id"]), str(r.get("reason")), source=b.source)
            except HTTPException as e:
                a["messages"].append(str(e.detail))
                skipped.append(i)
                continue
            b.result = {**(b.result or {}), "merged": (b.result or {}).get("merged", []) + [f"{res['from']['name']} → {res['into']['name']}"]}
            applied.append(i)
    elif b.kind == "alias_upsert":
        from . import aliases as al
        from .orders import ItemMapIn, find_customer, upsert_mapping
        skus = {s.code: s for s in db.query(Sku)}
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] not in ("new", "update"):
                skipped.append(i)
                continue
            sku = skus[str(r["sku_code"]).strip().upper()]
            code, name = str(r.get("code") or "").strip(), str(r.get("name") or "").strip()
            try:
                if a["party_kind"] == "customer":
                    upsert_mapping(db, find_customer(db, r["party"]).id, ItemMapIn(item_no=code, description=name or code, pack_size=str(r.get("pack_size") or ""),
                                                                                   order_unit=r.get("unit") or "箱", sku_id=sku.id, factor=r.get("factor")),
                                   r.get("price"))
                else:
                    al.upsert_alias(db, sku.id, a["party_kind"], r.get("party") or al.QB, code, name, str(r.get("pack_size") or ""),
                                    str(r.get("unit") or ""), r.get("factor"), r.get("price"), str(r.get("note") or ""))
            except HTTPException as e:
                a["messages"].append(str(e.detail))
                skipped.append(i)
                continue
            applied.append(i)
    elif b.kind == "order":
        from .orders import OLineIn, OrderIn, create_order
        from ..models import EvidenceLink, EvidenceVersion
        from .evidence import create_link
        _, cust, site = _analyze_order(db, b.meta, b.rows)
        lines = []
        for i in chosen:
            a, r = an[i], b.rows[i]
            if a["action"] not in ("mapped", "new", "unmapped"):
                skipped.append(i)
                continue
            ov = body.overrides.get(str(i)) or body.overrides.get(i) or {}
            sku_id = ov.get("sku_id") or a.get("sku_id")
            factor = ov.get("factor") or a.get("factor")
            lines.append(OLineIn(item_no=str(r.get("item_no") or ""), description=r["description"], pack_size=str(r.get("pack_size") or ""),
                                 unit=r.get("unit") or "箱", qty=float(r["qty"]), price=float(r.get("price") or 0), sku_id=sku_id,
                                 factor=float(factor) if factor else None))
            applied.append(i)
        if not lines:
            raise HTTPException(400, "没有可确认的行")
        order_in = OrderIn(customer_id=cust.id, site_id=site.id if site else None, po_no=str(b.meta.get("po_no") or "").strip(),
                           po_date=b.meta.get("po_date"), required_date=b.meta.get("required_date"),
                           category=b.meta.get("category") or "", note=b.meta.get("note") or "", lines=lines)
        if body.fill_order_id:  # PO 是给之前先送的货补的
            from ..models import SalesOrder
            from .orders import fill_order_from_po
            so = get_or_404(db, SalesOrder, body.fill_order_id, "要补的订单不存在")
            b.result = {**(b.result or {}), "fill_notes": fill_order_from_po(db, u, so, order_in)}
        else:
            so = create_order(db, u, order_in, source=b.source)
        for l in db.query(EvidenceLink).filter_by(target_type="batch", target_id=b.id, active=True):
            create_link(db, u, db.get(EvidenceVersion, l.evidence_version_id), "so", so.id, l.purpose)
        b.result = {**(b.result or {}), "order_id": so.id, "order_code": so.code}
    else:  # inbound → one transfer with the chosen lines（外库 / 公司调入）
        from . import aliases as al
        _, src, dst = _analyze_inbound(db, b.meta, b.rows)
        if not src or not dst:
            raise HTTPException(400, "来源地或目的仓不存在")
        lines = []
        for i in chosen:
            r = b.rows[i]
            pick = _pick(db, b, an, i, body, ("new", "mapped"))
            if not pick:
                skipped.append(i)
                continue
            sku, qty_b, factor, remember = pick
            if remember:
                al.remember_from_row(db, u, r, "warehouse", src.code, sku, factor)
            lines.append(TLineIn(sku_id=sku.id, lot_code=r.get("lot_code"), expiry=r.get("expiry"), qty=qty_b))
            applied.append(i)
        short = [an[i] for i in applied if an[i].get("short")]
        if short and not body.shortfall_reason.strip():
            db.rollback()
            raise HTTPException(409, {"code": "source_short", "msg": f"{src.code} 账面不够的有 {len(short)} 行（{'、'.join(x['key'] for x in short[:3])}）。"
                                      "如果外库实际有货（账面没记全），填写原因后可按单据补记；否则取消勾选这些行。"})
        if lines:
            tr = create_transfer(TransferIn(from_wh=src.id, to_wh=dst.id, expected_date=b.meta.get("expected_date"),
                                            note=f"{b.code} {b.meta.get('note') or ''}".strip(), lines=lines, shipped_now=True,
                                            shortfall_reason=body.shortfall_reason), u, db)
            b.result = {**(b.result or {}), "transfer_id": tr["id"], "transfer_code": tr["code"]}
            from ..models import EvidenceLink, EvidenceVersion
            from .evidence import create_link
            for l in db.query(EvidenceLink).filter_by(target_type="batch", target_id=b.id, active=True):  # BOL 原件跟到调入单
                create_link(db, u, db.get(EvidenceVersion, l.evidence_version_id), "transfer", tr["id"], l.purpose)
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
