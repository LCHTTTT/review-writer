"""Display names are user text, not filesystem identifiers."""
from __future__ import annotations

import unicodedata


def validate_project_name(value: str) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 96 or any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("Project name must contain 1–96 characters without control characters.")
    return name
