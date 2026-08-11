#!/usr/bin/env python3
"""Check the local public health endpoint of the HyperBot observer."""

from __future__ import annotations

import json
import os
import sys
import urllib.request


def main() -> int:
    port = os.getenv("HYPERBOT_UI_PORT", "3002")
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310
            payload = json.load(response)
            if response.status != 200 or payload.get("status") != "ok":
                return 1
    except Exception as exc:
        print(f"observer healthcheck failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
