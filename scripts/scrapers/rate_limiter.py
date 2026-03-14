"""
rate_limiter.py — Shared engine-level circuit breaker for search scrapers.
"""

import threading
import time
from dataclasses import asdict, dataclass
from typing import Dict, Optional


@dataclass
class EngineState:
    engine: str
    state: str = "closed"
    blocked_until: float = 0.0
    consecutive_rate_limits: int = 0
    last_error: str = ""
    last_state_change: float = 0.0
    probe_in_flight: bool = False


_STATE: Dict[str, EngineState] = {}
_LOCK = threading.RLock()


def _breaker_enabled(config: Optional[dict], engine: str) -> bool:
    config = config or {}
    enabled = bool(config.get("enable_circuit_breaker", True))
    engines = config.get("circuit_breaker_engines", ["google", "duckduckgo"])
    return enabled and engine in set(engines)


def reset_state():
    with _LOCK:
        _STATE.clear()


def _get_engine_state(engine: str) -> EngineState:
    entry = _STATE.get(engine)
    if not entry:
        entry = EngineState(engine=engine)
        _STATE[engine] = entry
    return entry


def can_query(engine: str, config: Optional[dict] = None) -> bool:
    if not _breaker_enabled(config, engine):
        return True

    with _LOCK:
        now = time.time()
        entry = _get_engine_state(engine)

        if entry.state == "open":
            if entry.blocked_until > now:
                return False
            entry.state = "half-open"
            entry.probe_in_flight = True
            entry.last_state_change = now
            return True

        if entry.state == "half-open":
            if entry.probe_in_flight:
                return False
            entry.probe_in_flight = True
            entry.last_state_change = now
            return True

        return True


def record_success(engine: str, config: Optional[dict] = None):
    if not _breaker_enabled(config, engine):
        return

    with _LOCK:
        now = time.time()
        entry = _get_engine_state(engine)
        entry.state = "closed"
        entry.blocked_until = 0.0
        entry.consecutive_rate_limits = 0
        entry.last_error = ""
        entry.last_state_change = now
        entry.probe_in_flight = False


def record_rate_limit(engine: str, config: Optional[dict] = None, reason: str = "") -> float:
    if not _breaker_enabled(config, engine):
        return 0.0

    config = config or {}
    threshold = int(config.get("rate_limit_max_429_consecutive", 2))
    base_cooldown = float(config.get("rate_limit_cooldown_seconds", 120))
    max_cooldown = float(config.get("circuit_breaker_max_duration_seconds", 300))

    with _LOCK:
        now = time.time()
        entry = _get_engine_state(engine)
        entry.consecutive_rate_limits += 1
        entry.last_error = reason
        entry.last_state_change = now
        entry.probe_in_flight = False

        should_open = entry.state == "half-open" or entry.consecutive_rate_limits >= threshold
        if not should_open:
            return 0.0

        multiplier = 2 ** max(0, entry.consecutive_rate_limits - threshold)
        cooldown = min(max_cooldown, base_cooldown * multiplier)
        entry.state = "open"
        entry.blocked_until = now + cooldown
        return cooldown


def get_engine_snapshot(engine: str) -> dict:
    with _LOCK:
        now = time.time()
        entry = _get_engine_state(engine)
        snapshot = asdict(entry)
        snapshot["remaining_seconds"] = max(0, int(entry.blocked_until - now))
        return snapshot


def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in (
        "429",
        "too many requests",
        "rate limit",
        "captcha",
        "quota",
        "unusual traffic",
    ))