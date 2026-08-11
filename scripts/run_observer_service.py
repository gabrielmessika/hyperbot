#!/usr/bin/env python3
"""Run the authenticated, read-only HyperBot UI/API service."""

from __future__ import annotations

import sys

from hyperbot.observability import ObservabilityReader, ObserverSettings
from hyperbot.ops import OpsConfigurationError
from hyperbot.services.observer import run_observer_server


def main() -> int:
    try:
        settings = ObserverSettings.from_environment()
    except OpsConfigurationError as exc:
        print(f"unsafe observer configuration: {exc}", file=sys.stderr)
        return 2
    run_observer_server(settings, ObservabilityReader(settings))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
