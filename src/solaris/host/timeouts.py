"""Whole-request budgets, including connection, queuing and transport.

The caller reserves time for a host error to come back. The host reserves a second margin
around core work (internal/control/timeouts.go). Human approval is reported separately;
these budgets apply to requests that are actually doing work.
"""

from __future__ import annotations

import asyncio
import math
import time

from solaris.protocol import control_pb2 as pb

DEFAULT_TIMEOUT_S = 15.0
_TIMEOUTS = {
    **dict.fromkeys(
        (
            "validate",
            "start",
            "reload",
            "inspect_source",
            "preview_group",
            "match_rule",
            "convert_to_plan",
            "export_plan",
        ),
        60.0,
    ),
    **dict.fromkeys(("stop", "claim", "set_system_proxy", "login_mesh"), 30.0),
    **dict.fromkeys(("register_warp", "health_check", "test_delay", "update_provider"), 120.0),
    "list_vpn_gate": 75.0,
}


def request_timeout(request: pb.Request, timeout: float | None = None) -> float:
    value = _TIMEOUTS.get(request.WhichOneof("method") or "", DEFAULT_TIMEOUT_S)
    if timeout is not None:
        value = timeout
    if not math.isfinite(value) or value <= 0:
        raise ValueError("request timeout must be positive and finite")
    return value


def remaining_seconds(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("the request budget was exhausted")
    return remaining


def set_host_deadline(request: pb.Request, remaining: float) -> None:
    """Called immediately before serialization; leave a bounded response margin."""
    if remaining <= 0:
        raise TimeoutError("the request expired before it could be sent")
    budget = max(0.001, remaining - min(2.0, remaining / 5))
    request.timeout_ms = min(0xFFFFFFFF, max(1, int(budget * 1000)))
    request.deadline_unix_ms = int((time.time() + budget) * 1000)
