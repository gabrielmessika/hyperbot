"""Exact runtime build provenance for server-generated events."""

from __future__ import annotations

import re
from collections.abc import Mapping

from hyperbot import __version__

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
UNKNOWN_COMMIT = "0" * 40


def code_commit(
    environment: Mapping[str, str],
    *,
    required: bool,
) -> str:
    """Return an exact Git commit, rejecting ambiguous production builds."""

    value = environment.get("HYPERBOT_CODE_COMMIT", UNKNOWN_COMMIT).strip().lower()
    if _COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError("HYPERBOT_CODE_COMMIT must be a 40-character Git SHA")
    if required and value == UNKNOWN_COMMIT:
        raise ValueError("HYPERBOT_CODE_COMMIT must identify the deployed commit")
    return value


def code_version(commit: str) -> str:
    """Combine the package release and immutable source revision."""

    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError("commit must be a 40-character Git SHA")
    return f"{__version__}+g{commit}"
