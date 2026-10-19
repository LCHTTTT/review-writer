"""Execution-local inheritance of a trusted job's admitted text-model snapshot."""
from contextlib import contextmanager
from contextvars import ContextVar

_MODEL_SOURCE = ContextVar("review_writer_submission_model_source", default=None)


def submission_model_source():
    return _MODEL_SOURCE.get()


@contextmanager
def bind_submission_model_source(job_id):
    token = _MODEL_SOURCE.set(job_id)
    try:
        yield
    finally:
        _MODEL_SOURCE.reset(token)
