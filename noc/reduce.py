"""Pure reduction logic: turn big telemetry into small, ranked answers.

No I/O here, so every rule is unit-tested. The MCP tools call these after querying.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

NO_DATA = ("No data in this window. This is NOT evidence that the service is healthy: "
           "the signal may be missing, delayed, or the service may have gone silent.")


# ---- log patterns -------------------------------------------------------------

@dataclass
class PatternChange:
    pattern: str
    status: str  # new | spiking | vanished
    baseline_count: float
    incident_count: float

    @property
    def magnitude(self) -> float:
        return abs(self.incident_count - self.baseline_count)


def pattern_diff(baseline: dict[str, float], incident: dict[str, float],
                 spike_ratio: float = 3.0, min_count: float = 5.0) -> list[PatternChange]:
    """Compare per-pattern counts from two equal-length windows."""
    out: list[PatternChange] = []
    for p, n in incident.items():
        b = baseline.get(p, 0.0)
        if b == 0.0:
            if n >= min_count:
                out.append(PatternChange(p, "new", 0.0, n))
        elif n >= spike_ratio * b and n - b >= min_count:
            out.append(PatternChange(p, "spiking", b, n))
    for p, b in baseline.items():
        if b >= min_count and incident.get(p, 0.0) == 0.0:
            out.append(PatternChange(p, "vanished", b, 0.0))
    out.sort(key=lambda c: -c.magnitude)
    return out


# ---- metric movers -----------------------------------------------------------

@dataclass
class Mover:
    signal: str
    kind: str  # rose | fell | dropped_to_zero | missing
    baseline: float
    latest: float | None
    first_moved_at: float | None


def movers(series: dict[str, list[tuple[float, float]]], baseline: dict[str, float],
           rel: float = 0.5, abs_floor: float = 1e-9) -> list[Mover]:
    """Signals that left their baseline, earliest first. Silence and absence count as moves."""
    out: list[tuple[float, float, Mover]] = []
    for name, b in baseline.items():
        pts = series.get(name) or []
        if not pts:
            if b > 0:
                out.append((float("inf"), 0.0, Mover(name, "missing", b, None, None)))
            continue
        threshold = max(rel * abs(b), abs_floor)
        for t, v in pts:
            if abs(v - b) > threshold:
                latest = pts[-1][1]
                if b > 0 and v == 0.0:
                    kind = "dropped_to_zero"
                else:
                    kind = "rose" if v > b else "fell"
                score = abs(v - b) / abs(b) if b else float("inf")
                out.append((t, -score, Mover(name, kind, b, latest, t)))
                break
    out.sort(key=lambda x: (x[0], x[1]))
    return [m for _, _, m in out]


# ---- size control --------------------------------------------------------------

def approx_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return len(s) // 4 + 1


def fit(items: list, budget_tokens: int, hint: str) -> tuple[list, str | None]:
    """Keep leading items until the budget is spent. Returns (kept, truncation note or None)."""
    kept, used = [], 0
    for it in items:
        cost = approx_tokens(it)
        if used + cost > budget_tokens:
            break
        kept.append(it)
        used += cost
    if len(kept) == len(items):
        return kept, None
    return kept, f"truncated: showing {len(kept)} of {len(items)}; {hint}"


# ---- readable handles ------------------------------------------------------------

class Handles:
    """Short, stable names for long IDs (readable names reduce made-up answers)."""

    def __init__(self, prefix: str = "t"):
        self.prefix = prefix
        self._fwd: dict[str, str] = {}
        self._rev: dict[str, str] = {}

    def short(self, long_id: str) -> str:
        if long_id not in self._fwd:
            h = f"{self.prefix}{len(self._fwd) + 1}"
            self._fwd[long_id] = h
            self._rev[h] = long_id
        return self._fwd[long_id]

    def full(self, handle: str) -> str | None:
        return self._rev.get(handle)
