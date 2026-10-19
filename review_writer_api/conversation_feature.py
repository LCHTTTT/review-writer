"""Dependency-free switch for the optional project conversation adapter."""
import os


def conversation_enabled() -> bool:
    return os.environ.get("REVIEW_WRITER_CONVERSATION_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
