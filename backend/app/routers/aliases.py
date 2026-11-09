"""商品对照（一物多码）。

库存、生产、订单只认我们自己的 SKU。外库（Supreme）、供应商（国内/本地）、QuickBooks、旧称各有自己的编码和品名，
都记在 sku_aliases；客户（大统华）的 Item# 对照沿用 customer_items（订单模块已在用）。本模块把两张表合在一起查、改、匹配。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import audit, current_user, get_or_404, require
from ..db import get_db
from ..models import Customer, CustomerItem, PurchaseOrder, Sku, SkuAlias, User, Warehouse
from .orders import ItemMapIn, find_customer, norm_text, norm_unit, upsert_mapping

router = APIRouter(prefix="/api/aliases", tags=["aliases"])

KINDS = {"customer": "客户", "warehouse": "外库 / 调货来源", "supplier": "供应商", "accounting": "QuickBooks", "other": "旧称 / 其他"}
QB = "QuickBooks"


def _now():
    return datetime.now(timezone.utc)


def _code(s) -> str:
    return str(s or "").strip().upper()


# ---------- parties ----------
def party_names(db: Session, kind: str, party: str) -> set[str]:
    """一方的所有叫法（规范化后），用于匹配：仓库按编码或名称都算同一方。"""
    p = norm_text(party)
    out = {p} if p else set()
    if kind == "warehouse":
        for w in db.query(Warehouse):
            if p in (norm_text(w.code), norm_text(w.name)):
                out |= {norm_text(w.code), norm_text(w.name)}
    if kind == "accounting":
        out |= {norm_text(QB), "qb", "qbo"}
    return out


def canonical_party(db: Session, kind: str, party: str) -> str:
    party = str(party or "").strip()
    if kind == "accounting":
        return QB
    if kind == "warehouse":
        p = norm_text(party)
        w = next((w for w in db.query(Warehouse) if p in (norm_text(w.code), norm_text(w.name))), None)
        if not w:
            raise HTTPException(400, f"仓库 / 来源地「{party}」不存在，请先在库存页「仓库与来源地」里添加")
        return w.code
    if not party:
        raise HTTPException(400, "请填写对方名称（供应商名等）")
    return party


def guess_kind(db: Session, party: str) -> str:
    p = norm_text(party)
    if p in ("quickbooks", "qb", "qbo"):
        return "accounting"
    if any(p in (norm_text(w.code), norm_text(w.name)) for w in db.query(Warehouse)):
        return "warehouse"
    if len(p) >= 2 and find_customer(db, party):
        return "customer"
    return "supplier"


# ---------- matching ----------
def find_alias(db: Session, kind: str, party: str | None, code=None, name=None, any_party=False) -> SkuAlias | None:
    q = db.query(SkuAlias).filter_by(party_kind=kind) if kind else db.query(SkuAlias)
    rows = q.all()
    if not any_party:
        names = party_names(db, kind, party or "")
        rows = [a for a in rows if norm_text(a.party) in names]
    c, n = _code(code), norm_text(name)
    if c:
        hit = next((a for a in rows if a.code and _code(a.code) == c), None) or next(
            (a for a in rows if a.code and norm_text(a.code) == norm_text(c)), None)
        if hit:
            return hit
    if n:
        return next((a for a in rows if a.name and norm_text(a.name) == n), None)
    return None


def match_sku_by_name(db: Session, text) -> tuple[Sku | None, str]:
    """以品名为主：先找品名完全一致（我们的品名或任何一方记过的品名），再找“单据品名里含我们的品名”且唯一的。
    返回 (SKU, 方式)：exact / contains。"""
    n = norm_text(text)
    if len(n) < 2:
        return None, ""
    skus = db.query(Sku).all()
    hit = next((s for s in skus if norm_text(s.name) == n), None)
    if hit:
        return hit, "exact"
    names = [(a.sku_id, a.name) for a in db.query(SkuAlias) if a.name] + [(c.sku_id, c.description) for c in db.query(CustomerItem) if c.description]
    ids = {sid for sid, nm in names if norm_text(nm) == n}
    if len(ids) == 1:
        return db.get(Sku, ids.pop()), "exact"
    inside = [s for s in skus if len(norm_text(s.name)) >= 2 and norm_text(s.name) in n]
    if inside:
        best = max(inside, key=lambda s: len(norm_text(s.name)))
        if sum(1 for s in inside if len(norm_text(s.name)) == len(norm_text(best.name))) == 1:
            return best, "contains"
    return None, ""


def resolve_row(db: Session, r: dict, kind: str, party: str, skus: dict[str, Sku]) -> dict:
    """一行单据 → 我们的 SKU。顺序：我们的编码 → 该来源的对照（编码、品名）→ 其他来源的唯一同码对照。"""
    sc = _code(r.get("sku_code"))
    if sc in skus:
        return {"sku": skus[sc], "how": "sku", "alias": None}
    pcode = r.get("party_code") or r.get("item_code") or (r.get("sku_code") if sc else None)
    pname = r.get("party_name") or r.get("description") or r.get("name")
    a = find_alias(db, kind, party, pcode, pname)
    if a:
        return {"sku": db.get(Sku, a.sku_id), "how": "alias", "alias": a}
    if pcode:
        hits = [x for x in db.query(SkuAlias) if x.code and _code(x.code) == _code(pcode)]
        if len({x.sku_id for x in hits}) == 1:
            return {"sku": db.get(Sku, hits[0].sku_id), "how": "alias_other", "alias": hits[0]}
        ci = {c.sku_id for c in db.query(CustomerItem) if c.item_no and _code(c.item_no) == _code(pcode)}
        if len(ci) == 1:  # 单据上写的是大统华 Item#
            return {"sku": db.get(Sku, ci.pop()), "how": "alias_other_ci", "alias": None}
    for t in (pname, r.get("name")):
        sku, how = match_sku_by_name(db, t)
        if sku:
            return {"sku": sku, "how": "name_" + how, "alias": None}
    return {"sku": None, "how": None, "alias": None}


def to_base_qty(sku: Sku, qty: float, unit: str | None, alias: SkuAlias | None):
    """对方单位 → 我们的基本单位。返回 (数量, 错误)。没写单位时按该对照的单位（对照上记的换算）。"""
    raw = str(unit or "").strip()
    if alias:
        au, f = alias.unit, alias.factor or 1
        same_as_alias = bool(au) and norm_unit(raw) == norm_unit(au)
        alias_unit_implied = not au and f != 1 and norm_unit(raw) != norm_unit(sku.base_unit)
        if not raw or same_as_alias or alias_unit_implied:
            return round(qty * f, 4), None
    if not raw or norm_unit(raw) == norm_unit(sku.base_unit):
        return qty, None
    per = (sku.conversions or {}).get(raw) or (sku.conversions or {}).get(norm_unit(raw))
    if not per:
        return None, f"{sku.code} 没有「{raw}」与「{sku.base_unit}」的换算：确认时填「换算×」（每{raw}等于多少{sku.base_unit}）"
    return round(qty / float(per), 4), None


def upsert_alias(db: Session, sku_id: str, kind: str, party: str, code="", name="", pack_size="", unit="", factor=None,
                 price=None, note="") -> SkuAlias:
    if kind not in KINDS or kind == "customer":
        raise HTTPException(400, "对照类型不对")
    if not (str(code or "").strip() or str(name or "").strip()):
        raise HTTPException(400, "对方编码和品名至少填一个")
    party = canonical_party(db, kind, party)
    a = find_alias(db, kind, party, code, None if code else name)
    if not a:
        a = SkuAlias(sku_id=sku_id, party_kind=kind, party=party)
        db.add(a)
    a.sku_id, a.party = sku_id, party
    a.code, a.name = str(code or a.code or "").strip(), str(name or a.name or "").strip()
    a.pack_size, a.unit = str(pack_size or a.pack_size or ""), str(unit or a.unit or "").strip()
    if factor:
        a.factor = float(factor)
    if price is not None:
        a.last_price = float(price)
    if note:
        a.note = note
    a.updated_at = _now()
    db.flush()
    return a


def remember_from_row(db: Session, u: User, r: dict, kind: str, party: str, sku: Sku, factor=None):
    """确认页人工选了 SKU（或单据给的是对方编码）→ 记住，下次自动对上。"""
    pcode = r.get("party_code") or r.get("item_code") or (r.get("sku_code") if _code(r.get("sku_code")) != sku.code else None)
    pname = r.get("party_name") or r.get("description")
    if not (pcode or pname):
        return None
    a = upsert_alias(db, sku.id, kind, party, pcode or "", pname or "", r.get("pack_size") or "", r.get("unit") or "", factor)
    audit(db, u, "alias.remembered", "sku", sku.id, party=a.party, code=a.code, name=a.name, factor=a.factor)
    return a


def accounting_name(db: Session, sku_id: str) -> str | None:
    a = db.query(SkuAlias).filter_by(sku_id=sku_id, party_kind="accounting").order_by(SkuAlias.updated_at.desc()).first()
    return a.name or a.code if a else None


# ---------- read ----------
def _sku_brief(s: Sku | None):
    return {"id": s.id, "code": s.code, "name": s.name, "base_unit": s.base_unit} if s else None


def all_aliases(db: Session, sku_id: str | None = None, q: str | None = None, kind: str | None = None) -> list[dict]:
    skus = {s.id: s for s in db.query(Sku)}
    custs = {c.id: c for c in db.query(Customer)}
    out = []
    if kind in (None, "", "customer"):
        cq = db.query(CustomerItem)
        if sku_id:
            cq = cq.filter_by(sku_id=sku_id)
        for i in cq:
            c = custs.get(i.customer_id)
            out.append({"id": i.id, "source": "customer_item", "party_kind": "customer", "kind_label": KINDS["customer"],
                        "party": c.name if c else "", "party_ref": c.code if c else "", "code": i.item_no, "name": i.description,
                        "pack_size": i.pack_size, "unit": i.order_unit, "factor": i.factor, "last_price": i.last_price, "note": "",
                        "sku": _sku_brief(skus.get(i.sku_id)), "updated_at": i.updated_at})
    aq = db.query(SkuAlias)
    if sku_id:
        aq = aq.filter_by(sku_id=sku_id)
    if kind and kind != "customer":
        aq = aq.filter_by(party_kind=kind)
    if kind != "customer":
        for a in aq:
            out.append({"id": a.id, "source": "alias", "party_kind": a.party_kind, "kind_label": KINDS.get(a.party_kind, a.party_kind),
                        "party": a.party, "party_ref": a.party, "code": a.code, "name": a.name, "pack_size": a.pack_size, "unit": a.unit,
                        "factor": a.factor, "last_price": a.last_price, "note": a.note, "sku": _sku_brief(skus.get(a.sku_id)),
                        "updated_at": a.updated_at})
    if q:
        n, c = norm_text(q), _code(q)
        out = [x for x in out if (c and c in _code(x["code"])) or (n and (n in norm_text(x["name"]) or n in norm_text(x["party"])))
               or (x["sku"] and (c in x["sku"]["code"] or n in norm_text(x["sku"]["name"])))]
    order = list(KINDS)
    out.sort(key=lambda x: ((x["sku"] or {}).get("code", ""), order.index(x["party_kind"]) if x["party_kind"] in order else 9, x["party"]))
    return out


def lookup(db: Session, q: str) -> list[dict]:
    """按任意一方的编码/品名找我们的 SKU（搜索、扫码用）。"""
    q = (q or "").strip()
    if not q:
        return []
    n, c = norm_text(q), _code(q)
    hits: dict[str, dict] = {}
    for s in db.query(Sku):
        if c == s.code or (s.barcode and q == s.barcode) or (len(n) >= 2 and (n in norm_text(s.code) or n in norm_text(s.name))):
            hits.setdefault(s.id, {"sku": _sku_brief(s), "via": []})
    for x in all_aliases(db):
        if not x["sku"]:
            continue
        exact = c and x["code"] and _code(x["code"]) == c
        if exact or (len(n) >= 2 and ((x["code"] and n in norm_text(x["code"])) or (x["name"] and n in norm_text(x["name"])))):
            hits.setdefault(x["sku"]["id"], {"sku": x["sku"], "via": []})["via"].append(
                {"party": x["party"], "kind_label": x["kind_label"], "code": x["code"], "name": x["name"], "exact": bool(exact)})
    return list(hits.values())[:30]


@router.get("")
def list_aliases(sku_id: str | None = None, q: str | None = None, kind: str | None = None, u: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    return all_aliases(db, sku_id, q, kind)


@router.get("/lookup")
def lookup_ep(q: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    return lookup(db, q)


@router.get("/parties")
def parties(u: User = Depends(current_user), db: Session = Depends(get_db)):
    sup = {p.supplier.strip() for p in db.query(PurchaseOrder) if p.supplier and p.supplier.strip()}
    sup |= {a.party for a in db.query(SkuAlias).filter_by(party_kind="supplier")}
    other = {a.party for a in db.query(SkuAlias).filter_by(party_kind="other")}
    return {"kinds": [{"key": k, "label": v} for k, v in KINDS.items()],
            "customer": [{"value": c.code, "label": c.name} for c in db.query(Customer).filter_by(active=True)],
            "warehouse": [{"value": w.code, "label": w.name} for w in db.query(Warehouse).filter(Warehouse.kind != "main")],
            "supplier": [{"value": s, "label": s} for s in sorted(sup)],
            "accounting": [{"value": QB, "label": QB}],
            "other": [{"value": s, "label": s} for s in sorted(other)] or [{"value": "旧称", "label": "旧称"}]}


class AliasIn(BaseModel):
    sku_id: str
    party_kind: str
    party: str = ""
    code: str = ""
    name: str = ""
    pack_size: str = ""
    unit: str = ""
    factor: float | None = None
    note: str = ""


def save_alias(db: Session, u: User, body: AliasIn, aid: str | None = None) -> dict:
    sku = get_or_404(db, Sku, body.sku_id, "SKU 不存在")
    if body.factor is not None and body.factor <= 0:
        raise HTTPException(400, "换算需大于 0")
    if body.party_kind == "customer":
        require(u, "order.manage")
        c = find_customer(db, body.party)
        if not c:
            raise HTTPException(400, f"客户「{body.party}」未登记")
        if aid:
            old = db.get(CustomerItem, aid)
            if old and (old.item_no != body.code.strip() or norm_text(old.description) != norm_text(body.name)):
                db.delete(old)
                db.flush()
        ci = upsert_mapping(db, c.id, ItemMapIn(item_no=body.code, description=body.name or body.code, pack_size=body.pack_size,
                                                order_unit=body.unit or "箱", sku_id=sku.id, factor=body.factor))
        audit(db, u, "alias.saved", "sku", sku.id, party=c.code, code=ci.item_no, name=ci.description, factor=ci.factor)
        return {"id": ci.id, "source": "customer_item"}
    require(u, "inventory.master")
    if aid:
        a = get_or_404(db, SkuAlias, aid, "对照不存在")
        a.sku_id, a.party_kind, a.party = sku.id, body.party_kind, canonical_party(db, body.party_kind, body.party)
        if not (body.code.strip() or body.name.strip()):
            raise HTTPException(400, "对方编码和品名至少填一个")
        a.code, a.name, a.pack_size, a.unit = body.code.strip(), body.name.strip(), body.pack_size, body.unit.strip()
        a.factor, a.note, a.updated_at = body.factor or a.factor or 1.0, body.note, _now()
    else:
        a = upsert_alias(db, sku.id, body.party_kind, body.party, body.code, body.name, body.pack_size, body.unit, body.factor, None, body.note)
    clash = [x for x in db.query(SkuAlias).filter_by(party_kind=a.party_kind, party=a.party)
             if x.id != a.id and ((a.code and _code(x.code) == _code(a.code)) or (not a.code and a.name and norm_text(x.name) == norm_text(a.name)))]
    if clash:
        raise HTTPException(409, f"{a.party} 的「{a.code or a.name}」已对到别的 SKU，请先改那一条")
    audit(db, u, "alias.saved", "sku", sku.id, party=a.party, code=a.code, name=a.name, factor=a.factor)
    return {"id": a.id, "source": "alias"}


@router.post("")
def create_alias(body: AliasIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = save_alias(db, u, body)
    db.commit()
    return r


@router.put("/{aid}")
def update_alias(aid: str, body: AliasIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = save_alias(db, u, body, aid)
    db.commit()
    return r


@router.delete("/{aid}")
def delete_alias(aid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    a = db.get(SkuAlias, aid)
    if a:
        require(u, "inventory.master")
        audit(db, u, "alias.deleted", "sku", a.sku_id, party=a.party, code=a.code, name=a.name)
        db.delete(a)
    else:
        ci = get_or_404(db, CustomerItem, aid, "对照不存在")
        require(u, "order.manage")
        audit(db, u, "alias.deleted", "sku", ci.sku_id, customer=ci.customer_id, code=ci.item_no, name=ci.description)
        db.delete(ci)
    db.commit()
    return {"ok": True}
