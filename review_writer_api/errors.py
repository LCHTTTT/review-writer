"""Stable workflow-domain errors shared by services, APIs, and workers."""

from __future__ import annotations

from typing import Any


class WorkflowError(Exception):
    code = "WORKFLOW_ERROR"
    status_code = 400
    retryable = False

    def __init__(self, message: str = "Workflow operation failed.", *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "retryable": self.retryable,
                "details": self.details,
            }
        }


class WorkflowConflict(WorkflowError):
    code = "STATE_CONFLICT"
    status_code = 409


class WorkflowNotFound(WorkflowError):
    code = "WORKFLOW_NOT_FOUND"
    status_code = 404


class WorkflowStageNotReady(WorkflowNotFound):
    """No current output has been produced; not a missing registered file."""
    code = "WORKFLOW_STAGE_NOT_READY"


class WorkflowValidationError(WorkflowError):
    code = "WORKFLOW_VALIDATION_FAILED"
    status_code = 422


class ModelTaskFailed(WorkflowError):
    """A model failure whose safe diagnosis survives child/parent task handoff."""
    code = "MODEL_TASK_FAILED"
    status_code = 502

    def __init__(self, message: str, *, code: str = "", details: dict | None = None):
        super().__init__(message, details=details)
        self.code = code or type(self).code


class ConversationModelFailure(WorkflowError):
    """An incomplete dialogue response, not an opaque unhandled worker error."""
    code = "CONVERSATION_MODEL_FAILED"
    status_code = 502

    @classmethod
    def from_provider(cls, error):
        from review_writer_core.provider_errors import provider_error_message
        details = error.get("details", error)
        if details.get("message") == "Model provider returned an empty response.":
            details = {**details, "category": "reasoning_only" if details.get("reasoning_only") or details.get("category") == "reasoning_only" else "empty_response"}
        # The gateway's HTTP normalizer may classify 422 as unknown. Its
        # explicit terminal code still identifies an uncertain paid outcome.
        if error.get("code") == "MODEL_RESULT_UNKNOWN":
            details = {**details, "category": "outcome_unknown"}
        return cls(provider_error_message(details) + " 本轮未取得完整回复；问题和已完成操作已保留，未自动重发模型请求。")


class WorkflowMigrationRequired(WorkflowError):
    code = "WORKFLOW_MIGRATION_REQUIRED"
    status_code = 503

    def __init__(
        self,
        message: str = "Workflow migration must complete before workflow access.",
        *,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message, details=details)


class ArtifactFileMissing(WorkflowError):
    code = "ARTIFACT_FILE_MISSING"
    status_code = 404


class ArtifactRangeNotSatisfiable(WorkflowError):
    code = "ARTIFACT_RANGE_NOT_SATISFIABLE"
    status_code = 416


class ProjectArchiveFailed(WorkflowError):
    code = "PROJECT_ARCHIVE_FAILED"
    status_code = 500
    retryable = True


class LiteratureSearchFailed(WorkflowError):
    code = "LITERATURE_SEARCH_FAILED"
    status_code = 502
    retryable = True
