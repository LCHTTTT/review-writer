"""Local user-document upload. Never execute imported instructions or start models."""
import hashlib
import os
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, Header, Request, HTTPException
from review_writer_api.conversation_feature import conversation_enabled
from review_writer_api.domain_services.conversation_attachments import MAX_TEXT_BYTES, TEXT_EXTENSIONS
from review_writer_api.errors import WorkflowConflict, WorkflowValidationError
from review_writer_api.job_service import job_payload
from review_writer_api.security import Permission


def build_attachments_router(principal_dependency, service, jobs):
    router = APIRouter(prefix="/api/v1/conversation/attachments", tags=["conversation"])
    def enabled():
        if not conversation_enabled():
            raise HTTPException(503, "Conversation is disabled.")

    @router.post("", status_code=202)
    async def upload(request: Request, filename: str, idempotency_key: str = Header(default="", alias="Idempotency-Key"),
                     principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        principal.require(Permission.PROJECT_WRITE)
        name = Path(filename.replace("\\", "/")).name[:255]
        if not name or Path(name).suffix.lower() not in TEXT_EXTENSIONS:
            raise WorkflowValidationError("Upload UTF-8 TXT, Markdown, CSV or JSON; PDFs use library ingestion.")
        raw = bytearray()
        async for part in request.stream():
            if len(raw) + len(part) > MAX_TEXT_BYTES:
                raise WorkflowValidationError("Text attachments must be 1 MB or smaller.")
            raw.extend(part)
        if not raw:
            raise WorkflowValidationError("The attachment is empty.")
        try:
            value = bytes(raw).decode("utf-8-sig")
        except UnicodeError as exc:
            raise WorkflowValidationError("Use UTF-8 text; binary documents are not text attachments.") from exc
        if "\x00" in value:
            raise WorkflowValidationError("Binary documents are not text attachments.")
        content = value.encode("utf-8")
        key = idempotency_key.strip() or str(uuid.uuid4())
        storage_id = str(uuid.uuid5(uuid.NAMESPACE_URL, principal.user_id + ":attachment:" + key))
        path = service.attachments.directory(principal) / (storage_id + ".txt")
        digest = hashlib.sha256(content).hexdigest()
        staged = path.parent / (str(uuid.uuid4()) + ".part")
        try:
            staged.write_bytes(content)
            try:
                os.link(staged, path)  # publish complete bytes without replacing another upload
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise WorkflowConflict("This upload identity belongs to different content; no file was replaced.")
        finally:
            staged.unlink(missing_ok=True)
        job = jobs.submit(principal, scope="library", project_id=None, job_type="conversation.attachment",
            operation_key="attachment:" + key, idempotency_key=key,
            payload={"filename": name, "storage_id": storage_id, "sha256": digest, "size": len(content)})
        return job_payload(job)

    @router.get("/{attachment_id}")
    def read(attachment_id: uuid.UUID, offset: int = 0, limit: int = 12000,
             principal=Depends(principal_dependency), _enabled=Depends(enabled)):
        return service.attachments.read(principal, str(attachment_id), offset=offset, limit=limit)
    return router
