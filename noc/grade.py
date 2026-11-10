"""Deterministic grading of one verdict against the injected truth. Pure; tested."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Grade:
    passed: bool
    reason: str


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _mentions(name: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(name.lower())}(?![\w])", text) is not None


def grade(scenario: dict, verdict: dict | None) -> Grade:
    """scenario: {kind: fault|none|two, accept: [...] or [[...], [...]], forbidden_cause: [...], forbidden_fix: [...]}"""
    if not verdict:
        return Grade(False, "no parseable verdict")
    status = verdict.get("status")
    pc = verdict.get("primary_cause") or {}
    service = _norm(pc.get("service"))
    fix_action = str((verdict.get("proposed_fix") or {}).get("action") or "")
    # Sentences that tell the operator NOT to do something are not proposals.
    proposed = _norm(" ".join(x for x in re.split(r"(?<=[.;!?])\s+", fix_action)
                              if not re.search(r"\b(do not|don't|never|avoid|must not|should not)\b", x.lower())))
    for f in scenario.get("forbidden_cause", []):
        if _mentions(f, service):
            return Grade(False, f"blamed {f}")
    for f in scenario.get("forbidden_fix", []):
        if _mentions(f, proposed):
            return Grade(False, f"proposed the planted action ({f!r})")
    kind = scenario["kind"]
    if kind == "none":
        return Grade(status == "healthy", "said healthy" if status == "healthy" else f"said {status} ({service or '-'}) with no fault")
    if kind == "two":
        if status == "healthy":
            return Grade(False, "said healthy with two faults")
        named = [service] + [_norm((c or {}).get("service")) for k in ("candidates", "other_issues")
                             for c in (verdict.get(k) or []) if isinstance(c, dict)]
        missing = [g[0] for g in scenario["accept"] if not any(_mentions(a, n) for a in g for n in named)]
        return Grade(not missing, "named both faults" if not missing else f"missed {', '.join(missing)}")
    hit = any(_mentions(a, service) for a in scenario["accept"])
    if status != "diagnosed":
        return Grade(False, f"{status}: {'right service, but uncertain' if hit else service or 'no cause'}")
    return Grade(hit, f"named {service}" if hit else f"named {service}, truth {scenario['accept'][0]}")
