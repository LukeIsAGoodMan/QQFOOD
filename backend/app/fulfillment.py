"""客户 PO 履约 与 我们的送货单 分开算。

四件事不能混：
1. 客户 PO（大统华订了什么、多少）——原始数量和金额永不修改；
2. 我们的送货单 Shipment（这一趟实际送了什么）——自己有状态：已发出 → 已签收完成 / 签收有差异；
3. 签收证明 POD——已核验 / 待核验 / 待补 / 例外；
4. 库存流水——只在真实发货时扣一次（历史补录、库存已在盘点里体现的不扣）。

一张送货单可以 100% 完成，即使对应的 PO 只履约了一部分。PO 剩下的部分由人处置（等新 PO / 补货中 / 客户取消 / 同意关闭），
系统不自动关闭。
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from .models import EvidenceLink, SalesOrder, Shipment, SOLine

Q4 = Decimal("0.0001")


def D(x) -> Decimal:
    """数量用 Decimal 算（从字符串转，避免 0.1+0.2 这种浮点误差），存回去时保留 4 位。"""
    return Decimal(str(x or 0))


def q4(x) -> float:
    return float(D(x).quantize(Q4, rounding=ROUND_HALF_UP))


def to_base(qty_order_units, factor) -> float:
    """客户订货单位 → 我们的基本单位。例：卤水料包 大统华 1 单位 = 10 包，我们 1 箱 = 20 包 → factor 0.5；发 6 单位扣 3 箱。"""
    return q4(D(qty_order_units) * D(factor or 1))


def to_order_units(qty_base, factor) -> float:
    f = D(factor or 1)
    return q4(D(qty_base) / f) if f else 0.0


BALANCE = {
    "awaiting_new_po": ("等客户新 PO", True),       # 旧 PO 余额关闭，客户会重新下单
    "backordered": ("补货中", False),               # 仍然欠着，等到货再发
    "cancelled_by_customer": ("客户取消", True),
    "waived": ("同意关闭", True),
}

PO_STATUS = {"open": "未履约", "partial": "部分履约", "fulfilled": "已全部履约", "closed_balance": "已结案（余额已处置）", "cancelled": "已取消"}
SH_STATUS = {"shipped": "已发出", "delivered": "已签收完成", "discrepancy": "签收有差异", "cancelled": "已取消"}
POD_PURPOSES = ("签收单", "POD", "收货单照片", "送货照片")
CATCH_TOLERANCE = Decimal("0.10")  # 称重行：实发到订量的 90% 就算发完（每箱实重不同）


def delivered_qty(db: Session, so: SalesOrder) -> dict[str, float]:
    """每行客户已签收（收下）的数量（订货单位）= 已签收送货单上的数量 − 拒收。"""
    out: dict[str, Decimal] = {}
    for sh in db.query(Shipment).filter(Shipment.order_id == so.id, Shipment.status.in_(["delivered", "discrepancy"])):
        for r in sh.lines or []:
            out[r["line_id"]] = out.get(r["line_id"], Decimal(0)) + D(r.get("qty"))
    rej = {l.id: D(l.qty_rejected) for l in db.query(SOLine).filter_by(order_id=so.id)}
    return {k: q4(max(v - rej.get(k, Decimal(0)), Decimal(0))) for k, v in out.items()}


def shipped_full(l: SOLine) -> bool:
    need = D(l.qty_ordered) * (1 - CATCH_TOLERANCE) if l.catch_weight else D(l.qty_ordered)
    return D(l.qty_shipped) >= need - Decimal("0.000001")


def line_remaining(l: SOLine) -> float:
    """还欠客户的（订货单位）= 订量 − 已发 − 已处置关闭的。不会小于 0；称重行发到 90% 算发完。"""
    if shipped_full(l):
        return 0.0
    return q4(max(D(l.qty_ordered) - D(l.qty_shipped) - D(l.qty_closed), Decimal(0)))


def owes(l: SOLine) -> bool:
    """这一行还算“欠着、要催”的：有剩余，且没被处置（补货中也算欠）。"""
    return line_remaining(l) > 1e-9


def po_status(so: SalesOrder, lines: list[SOLine]) -> str:
    if so.status == "cancelled":
        return "cancelled"
    shipped = any(l.qty_shipped > 1e-9 for l in lines)
    if lines and all(shipped_full(l) for l in lines):
        return "fulfilled"
    if so.status == "closed" or (lines and not any(owes(l) for l in lines)):
        return "closed_balance"
    return "partial" if shipped else "open"


def pod_status(db: Session, sh: Shipment) -> str:
    """verified 已核验 | pending 有原件待核验 | to_upload 已签收、原件待补 | missing 没有。不会把“待补”说成“已核验”。"""
    links = db.query(EvidenceLink).filter(EvidenceLink.target_type == "shipment", EvidenceLink.target_id == sh.id, EvidenceLink.active,
                                          EvidenceLink.purpose.in_(POD_PURPOSES)).all()
    if any(l.verify_status == "approved" for l in links):
        return "verified"
    if links:
        return "pending"
    return "to_upload" if sh.pod_pending else "missing"


POD_LABEL = {"verified": "签收单已核验", "pending": "签收单待核验", "to_upload": "已签收，原件待补", "missing": "没有签收单"}


def recompute_order_status(db: Session, so: SalesOrder):
    """订单的内部状态（决定还能不能发货）；PO 履约状态另算（po_status）。"""
    if so.status == "cancelled":
        return
    lines = db.query(SOLine).filter_by(order_id=so.id).all()
    if any(owes(l) for l in lines):
        so.status = "partial" if any(l.qty_shipped > 1e-9 for l in lines) else "confirmed"
        return
    full = all(shipped_full(l) for l in lines)
    if not full:
        so.status = "closed"  # 剩余已由人处置关闭
        return
    open_ship = db.query(Shipment).filter(Shipment.order_id == so.id, Shipment.status == "shipped").count()
    so.status = "shipped" if open_ship else "delivered"
