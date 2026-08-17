"""Exact runtime build provenance for server-generated events."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

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


def exact_code_version(
    environment: Mapping[str, str],
    *,
    repository_root: Path | None = None,
) -> str:
    """Resolve exact deployed or local Git provenance, never an ambiguous build."""

    commit = code_commit(environment, required=False)
    if commit == UNKNOWN_COMMIT and repository_root is not None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("cannot resolve an exact local Git commit") from exc
        commit = completed.stdout.strip().lower()
    if _COMMIT_PATTERN.fullmatch(commit) is None or commit == UNKNOWN_COMMIT:
        raise ValueError("an exact non-zero Git commit is required")
    return code_version(commit)
