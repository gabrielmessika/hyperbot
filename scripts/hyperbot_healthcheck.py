#!/usr/bin/env python3
"""Check the local HyperBot collector guardrails, feed age, and disk reserve."""

from __future__ import annotations

import json
import time
from dataclasses import asdict

from hyperbot.ops import OpsConfigurationError, OpsSettings, evaluate_collector_health


def main() -> int:
    try:
        settings = OpsSettings.from_environment(require_enabled=True)
        result = evaluate_collector_health(
            settings,
            now_ms=int(time.time() * 1_000),
        )
    except (OpsConfigurationError, OSError, ValueError) as exc:
        print(json.dumps({"healthy": False, "reasons": [str(exc)]}))
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
