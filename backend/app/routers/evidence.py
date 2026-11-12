"""Unified evidence: upload, dedupe, versions, links with purpose, per-link verification (PRD §4)."""
import codecs
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..core import audit, can, can_read_evidence, current_user, get_or_404, require, self_review_allowed, sign_file, verify_file_sig
from ..db import UPLOAD_DIR, get_db
from ..models import Evidence, EvidenceLink, EvidenceVersion, User

router = APIRouter(prefix="/api", tags=["evidence"])

MAX_BYTES = 50 * 1024 * 1024
PREVIEWABLE = {"image/jpeg", "image/png", "image/heic", "application/pdf", "image/webp", "text/plain"}


def sniff(head: bytes, filename: str) -> str | None:
    """Content-based type check; extension alone is not trusted."""
    fn = filename.lower()
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"%PDF":
        return "application/pdf"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heis", b"hevc"):
        return "image/heic"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"PK\x03\x04" and fn.endswith((".docx", ".xlsx", ".pptx")):
        return {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}[fn.rsplit(".", 1)[1]]
    if fn.endswith((".csv", ".txt")):
        try:  # head may end mid-character; an incremental decoder tolerates that
            codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
            return "text/csv" if fn.endswith(".csv") else "text/plain"
        except UnicodeDecodeError:
            return None
    return None


def _store(data: bytes) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    d = UPLOAD_DIR / sha[:2]
    d.mkdir(exist_ok=True)
    path = d / sha
    if not path.exists():
        path.write_bytes(data)
    return sha, f"{sha[:2]}/{sha}"


def _names(db):
    return {x.id: x.display_name for x in db.query(User).all()}


def link_out(db: Session, l: EvidenceLink, u: User, names: dict):
    ev = db.get(Evidence, l.evidence_id)
    ver = db.get(EvidenceVersion, l.evidence_version_id)
    readable = can_read_evidence(u, ev.sensitivity)
    base = {"link_id": l.id, "evidence_id": ev.id, "purpose": l.purpose, "item_key": l.item_key,
            "verify_status": l.verify_status, "verified_by": names.get(l.verified_by), "verified_at": l.verified_at,
            "verify_note": l.verify_note, "sensitivity": ev.sensitivity, "readable": readable,
            "linked_by": names.get(l.created_by), "linked_at": l.created_at,
            "is_latest_version": ev.current_version_id == ver.id, "version_no": ver.version_no}
    if not readable:
        return {**base, "title": "受限证据", "filename": None, "url": None}
    return {**base, "title": ev.title, "filename": ver.filename, "mime": ver.mime, "size": ver.size,
            "sha256": ver.sha256[:12], "uploaded_by": names.get(ver.uploaded_by), "uploaded_at": ver.uploaded_at,
            "occurred_at": ver.occurred_at, "version_id": ver.id, "previewable": ver.mime in PREVIEWABLE,
            "url": sign_file(ver.id, u.id)}


def create_link(db: Session, u: User, ver: EvidenceVersion, target_type: str, target_id: str,
                purpose: str = "附件", item_key: str | None = None) -> EvidenceLink:
    exists = db.query(EvidenceLink).filter_by(evidence_version_id=ver.id, target_type=target_type, target_id=target_id,
                                              purpose=purpose, item_key=item_key, active=True).first()
    if exists:
        return exists
    l = EvidenceLink(evidence_id=ver.evidence_id, evidence_version_id=ver.id, target_type=target_type,
                     target_id=target_id, purpose=purpose, item_key=item_key, created_by=u.id)
    db.add(l)
    db.flush()
    audit(db, u, "evidence.linked", target_type, target_id, evidence=ver.evidence_id, version=ver.version_no,
          purpose=purpose)
    if target_type == "batch":  # 批次确认后再补传的原件，同时挂到它生成的订单 / 调入单上
        from ..models import BatchProposal
        b = db.get(BatchProposal, target_id)
        r = (b.result or {}) if b else {}
        for t, k in (("so", "order_id"), ("transfer", "transfer_id"), ("po", "po_id")):
            if r.get(k):
                create_link(db, u, ver, t, r[k], purpose, item_key)
    return l


def ingest_file(db: Session, u: User, data: bytes, filename: str, title: str | None = None,
                sensitivity: str = "normal", occurred_at: datetime | None = None, source: str = "upload"):
    if len(data) == 0:
        raise HTTPException(400, "空文件")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "文件超过 50MB")
    mime = sniff(data[:64], filename)
    if not mime:
        raise HTTPException(415, "不支持的文件类型（支持 JPG/PNG/HEIC/WEBP/PDF，DOCX/XLSX/CSV 作为附件）")
    if sensitivity not in ("normal", "finance", "hr"):
        raise HTTPException(400, "无效敏感级别")
    sha, key = _store(data)
    # 文件去重：同一校验值且当前用户可读 → 复用原证据，不重复上传
    for ver in db.query(EvidenceVersion).filter_by(sha256=sha).all():
        ev = db.get(Evidence, ver.evidence_id)
        if can_read_evidence(u, ev.sensitivity):
            return ev, ver, True
    ev = Evidence(title=title or filename, sensitivity=sensitivity, created_by=u.id)
    db.add(ev)
    db.flush()
    ver = EvidenceVersion(evidence_id=ev.id, version_no=1, filename=filename, mime=mime, size=len(data),
                          sha256=sha, storage_key=key, source=source, occurred_at=occurred_at, uploaded_by=u.id)
    db.add(ver)
    db.flush()
    ev.current_version_id = ver.id
    audit(db, u, "evidence.uploaded", "evidence", ev.id, filename=filename, size=len(data), sha256=sha[:12])
    return ev, ver, False


@router.post("/evidence/upload")
async def upload(file: UploadFile = File(...), target_type: str | None = Form(None), target_id: str | None = Form(None),
                 purpose: str = Form("附件"), item_key: str | None = Form(None), sensitivity: str = Form("normal"),
                 title: str | None = Form(None), occurred_at: str | None = Form(None),
                 u: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read()
    occ = datetime.fromisoformat(occurred_at) if occurred_at else None
    ev, ver, dedup = ingest_file(db, u, data, file.filename or "upload", title, sensitivity, occ)
    link = None
    if target_type and target_id:
        link = create_link(db, u, ver, target_type, target_id, purpose, item_key)
    db.commit()
    return {"evidence_id": ev.id, "version_id": ver.id, "link_id": link.id if link else None, "dedup": dedup}


class LinkIn(BaseModel):
    evidence_id: str
    target_type: str
    target_id: str
    purpose: str = "附件"
    item_key: str | None = None


@router.post("/evidence/link")
def link_existing(body: LinkIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Evidence, body.evidence_id)
    if not can_read_evidence(u, ev.sensitivity):
        raise HTTPException(403, "无权引用此证据")
    ver = db.get(EvidenceVersion, ev.current_version_id)
    l = create_link(db, u, ver, body.target_type, body.target_id, body.purpose, body.item_key)
    db.commit()
    return {"link_id": l.id}


@router.get("/evidence")
def list_for_target(target_type: str, target_id: str, include_inactive: bool = False,
                    u: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(EvidenceLink).filter_by(target_type=target_type, target_id=target_id)
    if not include_inactive:
        q = q.filter_by(active=True)
    names = _names(db)
    return [link_out(db, l, u, names) for l in q.order_by(EvidenceLink.created_at)]


TARGET_LABEL = {"task": "任务", "record": "记录", "transfer": "调入单", "po": "采购单", "so": "订单", "shipment": "发货",
                "batch": "批次", "production": "生产", "lot": "Lot", "posting": "入库", "contact": "联系人", "note": "笔记",
                "checklist_run": "检查表", "ingest": "资料中心"}


def target_label(db: Session, ttype: str, tid: str) -> str:
    """证据挂在哪里（给人看的）：任务「2602柜入库凭证」、调入单 TR-…"""
    from ..models import (BatchProposal, BusinessRecord, Lot, ProductionBatch, PurchaseOrder, SalesOrder, Shipment, Task,
                          Transfer)
    model = {"task": Task, "record": BusinessRecord, "transfer": Transfer, "po": PurchaseOrder, "so": SalesOrder, "shipment": Shipment,
             "batch": BatchProposal, "production": ProductionBatch, "lot": Lot}.get(ttype)
    obj = db.get(model, tid) if model else None
    name = (getattr(obj, "title", None) or getattr(obj, "code", None) or getattr(obj, "lot_code", None)) if obj else None
    return f"{TARGET_LABEL.get(ttype, ttype)}「{name}」" if name else TARGET_LABEL.get(ttype, ttype)


@router.get("/evidence/library")
def library(q: str = "", limit: int = 40, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """资料库：最近的原件，带“挂在哪里”；搜索也匹配所挂任务 / 单据的名字（如搜 2602 找到 2602 柜任务上的照片）。"""
    out, n = [], q.strip().lower()
    for ev in db.query(Evidence).order_by(Evidence.created_at.desc()).limit(400):
        if not can_read_evidence(u, ev.sensitivity):
            continue
        ver = db.get(EvidenceVersion, ev.current_version_id)
        links = db.query(EvidenceLink).filter_by(evidence_id=ev.id, active=True).filter(EvidenceLink.target_type != "ingest").all()
        where = [f"{target_label(db, l.target_type, l.target_id)} · {l.purpose}" for l in links][:4]
        if n and n not in (ev.title or "").lower() and n not in (ver.filename or "").lower() and not any(n in w.lower() for w in where):
            continue
        out.append({"evidence_id": ev.id, "title": ev.title, "filename": ver.filename, "mime": ver.mime,
                    "uploaded_at": ver.uploaded_at, "sensitivity": ev.sensitivity, "where": where,
                    "url": sign_file(ver.id, u.id) if ver.mime.startswith("image/") else None})
        if len(out) >= limit:
            break
    return out


@router.get("/evidence/{eid}")
def evidence_detail(eid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Evidence, eid)
    if not can_read_evidence(u, ev.sensitivity):
        raise HTTPException(403, "无权访问此证据")
    names = _names(db)
    vers = db.query(EvidenceVersion).filter_by(evidence_id=eid).order_by(EvidenceVersion.version_no.desc()).all()
    links = db.query(EvidenceLink).filter_by(evidence_id=eid).order_by(EvidenceLink.created_at).all()
    return {"id": ev.id, "title": ev.title, "sensitivity": ev.sensitivity, "tags": ev.tags,
            "current_version_id": ev.current_version_id,
            "versions": [{"id": v.id, "version_no": v.version_no, "filename": v.filename, "mime": v.mime, "size": v.size,
                          "sha256": v.sha256, "uploaded_by": names.get(v.uploaded_by), "uploaded_at": v.uploaded_at,
                          "occurred_at": v.occurred_at, "source": v.source, "url": sign_file(v.id, u.id),
                          "previewable": v.mime in PREVIEWABLE} for v in vers],
            "links": [{**link_out(db, l, u, names), "target_type": l.target_type, "target_id": l.target_id,
                       "active": l.active} for l in links]}


@router.post("/evidence/{eid}/versions")
async def replace_version(eid: str, file: UploadFile = File(...), u: User = Depends(current_user),
                          db: Session = Depends(get_db)):
    """替换文件：新版本待核验；已核验通过的关联固定在旧版本，历史批准不变。"""
    ev = get_or_404(db, Evidence, eid)
    if not can_read_evidence(u, ev.sensitivity):
        raise HTTPException(403, "无权替换此证据")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "文件超过 50MB")
    mime = sniff(data[:64], file.filename or "")
    if not mime:
        raise HTTPException(415, "不支持的文件类型")
    sha, key = _store(data)
    old = db.get(EvidenceVersion, ev.current_version_id)
    ver = EvidenceVersion(evidence_id=ev.id, version_no=old.version_no + 1, filename=file.filename or old.filename,
                          mime=mime, size=len(data), sha256=sha, storage_key=key, uploaded_by=u.id)
    db.add(ver)
    db.flush()
    ev.current_version_id = ver.id
    for l in db.query(EvidenceLink).filter_by(evidence_version_id=old.id, active=True).all():
        if l.verify_status == "approved":
            # keep the approved pin; add a fresh pending link on the new version
            db.add(EvidenceLink(evidence_id=ev.id, evidence_version_id=ver.id, target_type=l.target_type,
                                target_id=l.target_id, purpose=l.purpose, item_key=l.item_key, created_by=u.id))
        else:
            l.evidence_version_id = ver.id
            l.verify_status = "unverified"
            l.verified_by = None
            l.verified_at = None
    audit(db, u, "evidence.replaced", "evidence", ev.id, from_version=old.version_no, to_version=ver.version_no)
    db.commit()
    return {"version_id": ver.id, "version_no": ver.version_no}


class VerifyIn(BaseModel):
    status: str  # approved | rejected
    note: str = ""


@router.post("/evidence/links/{lid}/verify")
def verify(lid: str, body: VerifyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    require(u, "evidence.verify")
    l = get_or_404(db, EvidenceLink, lid)
    ver = db.get(EvidenceVersion, l.evidence_version_id)
    ev = db.get(Evidence, l.evidence_id)
    if not can_read_evidence(u, ev.sensitivity):
        raise HTTPException(403, "无权查看此证据")
    if body.status not in ("approved", "rejected"):
        raise HTTPException(400, "无效核验结果")
    self_check = ver.uploaded_by == u.id
    if body.status == "approved" and self_check and not self_review_allowed(u):
        raise HTTPException(409, "上传人不能核验自己上传的证据（标准模式要求分人复核）")
    if body.status == "rejected" and not body.note.strip():
        raise HTTPException(400, "驳回需填写意见")
    l.verify_status, l.verify_note = body.status, (("（本人核验）" if self_check else "") + body.note).strip()
    l.verified_by, l.verified_at = u.id, datetime.now(timezone.utc)
    audit(db, u, f"evidence.{body.status}", l.target_type, l.target_id, evidence=ev.id, version=ver.version_no,
          purpose=l.purpose, signature_meaning="核验证据与用途相符", note=body.note, self_check=self_check)
    db.commit()
    return {"ok": True}


@router.post("/evidence/links/{lid}/unlink")
def unlink(lid: str, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """解除关联只移除该处引用，不删除原件。"""
    l = get_or_404(db, EvidenceLink, lid)
    if l.verify_status == "approved" and not can(u, "task.verify"):
        raise HTTPException(403, "已核验的证据只能由主管解除关联")
    l.active = False
    audit(db, u, "evidence.unlinked", l.target_type, l.target_id, evidence=l.evidence_id)
    db.commit()
    return {"ok": True}


@router.get("/files/{vid}")
def serve_file(vid: str, u: str, e: int, s: str, download: bool = False, db: Session = Depends(get_db)):
    if not verify_file_sig(vid, u, e, s):
        raise HTTPException(403, "链接无效或已过期")
    user = db.get(User, u)
    ver = db.get(EvidenceVersion, vid)
    if not user or not user.active or not ver:
        raise HTTPException(403, "无权访问")
    ev = db.get(Evidence, ver.evidence_id)
    if not can_read_evidence(user, ev.sensitivity):  # re-check: revocation applies to signed links too
        raise HTTPException(403, "无权访问")
    path = UPLOAD_DIR / ver.storage_key
    disp = "attachment" if download or ver.mime not in PREVIEWABLE else "inline"
    mt = ver.mime + ("; charset=utf-8" if ver.mime.startswith("text/") else "")
    return FileResponse(path, media_type=mt, filename=ver.filename, content_disposition_type=disp,
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/evidence-pending")
def pending(u: User = Depends(current_user), db: Session = Depends(get_db)):
    """待核验证据队列（只列当前用户可读的）。"""
    names = _names(db)
    out = []
    q = db.query(EvidenceLink).filter_by(active=True, verify_status="unverified").filter(
        EvidenceLink.target_type.notin_(["ingest"])).order_by(EvidenceLink.created_at).limit(300)
    for l in q:
        d = link_out(db, l, u, names)
        if d["readable"]:
            out.append({**d, "target_type": l.target_type, "target_id": l.target_id})
    return out
