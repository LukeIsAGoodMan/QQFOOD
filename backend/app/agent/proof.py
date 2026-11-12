"""证据（proof）提醒：ChatGPT 每次提交采购 / 库存 / 出货 / 生产 / 安装结果时，告诉用户该传什么原件。

规则（老板定的）：有原件就顺手传；没有也照常提交，写一句 proof_note（如“签收单明天补”），之后再补。
缺证据从不挡住提交或确认——证据是追溯和核对用的，不是闸门。上传后仍是“未核验”，由人核验。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

# kind → (中文, 建议上传的证据, 上传用途默认值)
GUIDE = {
    "purchase": ("采购", ["小票 / 供应商发票（照片或 PDF）", "送货单 / BOL 照片", "到货照片（外箱、标签、温度）", "付款凭证"], "小票"),
    "receipt": ("采购收货", ["送货单 / 签收单照片", "到货照片（外箱、标签、温度计读数）", "拒收 / 破损照片"], "签收单"),
    "stock_count": ("库存盘点", ["盘点表照片", "外库库存报表 PDF / 截图", "货架 / 冷库照片"], "盘点表"),
    "inbound": ("调入 / 外库", ["BOL / 调货单 PDF 或照片", "到货照片", "差异 / 破损照片"], "BOL"),
    "order": ("客户订单", ["PO 原件（PDF / 邮件截图）"], "PO 原件"),
    "shipment": ("出货", ["签收单 / POD 照片", "称重标签照片", "装车 / 送货照片"], "签收单"),
    "production": ("生产", ["生产记录 / 称重单照片", "成品标签照片", "原料批次标签照片", "损耗照片"], "生产记录"),
    "install": ("安装 / 设备", ["安装完成照片", "调试 / 验收记录", "供应商发票或服务单"], "安装后照片"),
    "task": ("任务", ["完成照片", "整改前后对比照片"], "完成证明"),
}


def kind_for_record(db: Session, record_id: str) -> str:
    from ..models import BusinessRecord, RecordType
    r = db.get(BusinessRecord, record_id)
    rt = db.get(RecordType, r.record_type_id) if r else None
    name = rt.name if rt else ""
    if any(k in name for k in ("设备", "建设", "安装")):
        return "install"
    if "采购" in name:
        return "purchase"
    if "订单" in name:
        return "shipment"
    return "task"


def has_proof(db: Session, target_type: str, target_id: str) -> int:
    from ..models import EvidenceLink
    return db.query(EvidenceLink).filter_by(target_type=target_type, target_id=target_id, active=True).count()


def block(ctx, db: Session, kind: str, target_type: str, target_id: str, proof_note: str = "", purpose: str | None = None) -> dict:
    """附在每个写入工具的返回里：建议传什么、现在就能用的上传链接、没有的话怎么补。"""
    from .tools import sign_upload
    label, suggested, default_purpose = GUIDE[kind]
    n = has_proof(db, target_type, target_id)
    return {
        "what": label, "suggested": suggested, "attached": n, "proof_note": proof_note or None,
        "upload_url": ctx.link(f"/u/{sign_upload(ctx.user.id, target_type, target_id, purpose or default_purpose)}"),
        "expires_in_minutes": 30,
        "backfill": {"target_type": target_type, "target_id": target_id,
                     "how": "现在没有也没关系：已照常提交。之后用 request_upload_link(target_type, target_id) 生成新链接补传；"
                            "list_missing_proof 可查哪些单据还缺原件。"},
        "note_for_ai": ("请提醒用户：有 " + "、".join(suggested[:3]) + " 的话，用 upload_url 直接用手机传（PDF/照片都行）。"
                        "没有就先这样，告诉用户之后可以补；不要因为缺证据就不提交。你对图片的描述不能替代原件。") if not n else
                       "已有原件，提醒用户在系统里核验即可。",
    }


def missing(db: Session, days: int = 30, limit: int = 50) -> list[dict]:
    """最近 N 天还没有任何原件的单据（按时间倒序）。"""
    from ..models import (BatchProposal, BusinessRecord, EvidenceLink, ProductionBatch, PurchaseOrder, RecordType, SalesOrder,
                          Shipment, Transfer)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    linked = {(l.target_type, l.target_id) for l in db.query(EvidenceLink.target_type, EvidenceLink.target_id).filter_by(active=True)}
    out = []

    def add(kind, ttype, tid, ref, at, status="", note="", url=""):
        if (ttype, tid) in linked:
            return
        label, suggested, _ = GUIDE[kind]
        out.append({"what": label, "ref": ref, "status": status, "date": at, "proof_note": note or None, "suggested": suggested,
                    "target_type": ttype, "target_id": tid, "url": url})

    def aware(t):
        return t if not t or t.tzinfo else t.replace(tzinfo=timezone.utc)

    for b in db.query(BatchProposal).filter(BatchProposal.kind.in_(["stock_count", "inbound", "order", "purchase"]), BatchProposal.status != "rejected"):
        if aware(b.created_at) < since:
            continue
        r = b.result or {}
        # 已确认的批次：证据挂在批次或其生成的单据上都算
        if any(r.get(k) and (t, r[k]) in linked for t, k in (("so", "order_id"), ("transfer", "transfer_id"), ("po", "po_id"))):
            continue
        add(b.kind, "batch", b.id, f"{b.code}（{b.source_note[:30]}）", b.created_at, b.status, (b.meta or {}).get("proof_note", ""), f"/ingest?batch={b.id}")
    for s in db.query(Shipment):
        if aware(s.shipped_at) >= since:
            so = db.get(SalesOrder, s.order_id)
            add("shipment", "shipment", s.id, f"{s.code}（{so.code if so else ''} PO {so.po_no if so and so.po_no else '待补'}）", s.shipped_at, s.status,
                url=f"/orders?so={s.order_id}")
    for t in db.query(Transfer).filter(Transfer.status.in_(["shipped", "partial", "received"])):
        if aware(t.created_at) >= since:
            add("inbound", "transfer", t.id, t.code, t.created_at, t.status, url="/inventory")
    for p in db.query(PurchaseOrder).filter(PurchaseOrder.status.in_(["ordered", "partial", "received", "closed"])):
        if aware(p.created_at) >= since:
            add("purchase", "po", p.id, f"{p.code} {p.supplier}", p.created_at, p.status, url=f"/purchasing?po={p.id}")
    for pb in db.query(ProductionBatch):
        if aware(pb.created_at) >= since:
            add("production", "production", pb.id, pb.code, pb.created_at, url="/production")
    install_types = [rt.id for rt in db.query(RecordType) if any(k in rt.name for k in ("设备", "建设", "安装"))]
    for r in db.query(BusinessRecord).filter(BusinessRecord.record_type_id.in_(install_types or ["-"])):
        if aware(r.updated_at) >= since and r.status not in ("草稿",):
            add("install", "record", r.id, r.title, r.updated_at, r.status, url=f"/r/{r.id}")
    out.sort(key=lambda x: aware(x["date"]) or since, reverse=True)
    return out[:limit]
