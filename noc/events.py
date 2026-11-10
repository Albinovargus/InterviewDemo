"""Normalise Claude Code stream-json events into the console's UI events.

One normaliser feeds every panel (steps, counter, verdict, cost), so the panels
cannot disagree. Live runs and replays go through the same code.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

COUNT_KEYS = ("scanned_log_lines", "patterns", "anomalous", "returned")


@dataclass
class UiEvent:
    type: str  # run_started narration tool_call tool_result counter verdict cost rate_limit error run_finished
    ts: float
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "ts": self.ts, "data": self.data}


_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def extract_verdict(text: str) -> dict | None:
    blocks = _JSON_BLOCK.findall(text or "")
    for raw in reversed(blocks):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and "status" in d:
            return d
    return None


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def _split_tool(name: str) -> tuple[str, str]:
    if name.startswith("mcp__"):
        _, server, tool = name.split("__", 2)
        return server, tool
    return "builtin", name


class Normaliser:
    def __init__(self) -> None:
        self.pending: dict[str, dict] = {}
        self.steps = 0
        self.totals = {k: 0 for k in COUNT_KEYS}
        self.finished = False
        self.started_ts: float | None = None

    def feed(self, ev: dict, ts: float) -> list[UiEvent]:
        if self.started_ts is None:
            self.started_ts = ts
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            return [UiEvent("run_started", ts, {"model": ev.get("model"),
                                                "tools": [x for x in ev.get("tools", []) if x.startswith("mcp__")]})]
        if t == "assistant":
            return self._assistant(ev, ts)
        if t == "user":
            return self._user(ev, ts)
        if t == "rate_limit_event":
            info = ev.get("rate_limit_info") or {}
            if info.get("status") == "rejected":
                return [UiEvent("rate_limit", ts, {"status": "rejected", "window": info.get("rateLimitType"),
                                                   "resets_at": info.get("resetsAt")})]
            return []
        if t == "result":
            return self._result(ev, ts)
        return []

    def _assistant(self, ev: dict, ts: float) -> list[UiEvent]:
        out = []
        for c in ev.get("message", {}).get("content", []):
            if c.get("type") == "text" and c.get("text", "").strip():
                out.append(UiEvent("narration", ts, {"text": c["text"].strip()}))
            elif c.get("type") == "tool_use":
                server, tool = _split_tool(c.get("name", ""))
                self.steps += 1
                self.pending[c["id"]] = {"tool": tool, "server": server, "ts": ts, "step": self.steps}
                out.append(UiEvent("tool_call", ts, {"id": c["id"], "tool": tool, "server": server,
                                                     "input": c.get("input", {}), "step": self.steps}))
        return out

    def _user(self, ev: dict, ts: float) -> list[UiEvent]:
        out = []
        content = ev.get("message", {}).get("content", [])
        if not isinstance(content, list):
            return out
        for c in content:
            if c.get("type") != "tool_result":
                continue
            call = self.pending.pop(c.get("tool_use_id"), {"tool": "?", "server": "?", "ts": ts, "step": 0})
            text = _result_text(c.get("content"))
            out.append(UiEvent("tool_result", ts, {"id": c.get("tool_use_id"), "tool": call["tool"],
                                                   "server": call["server"], "step": call["step"],
                                                   "is_error": bool(c.get("is_error")), "text": text,
                                                   "duration_s": round(ts - call["ts"], 3)}))
            counts = None
            try:
                parsed = json.loads(text)
                counts = parsed.get("counts") if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                pass
            if isinstance(counts, dict):
                for k in COUNT_KEYS:
                    self.totals[k] += int(counts.get(k, 0) or 0)
                out.append(UiEvent("counter", ts, dict(self.totals)))
        return out

    def _result(self, ev: dict, ts: float) -> list[UiEvent]:
        self.finished = True
        out = []
        verdict = extract_verdict(ev.get("result", ""))
        ok = not ev.get("is_error") and ev.get("subtype") == "success"
        if verdict is not None:
            out.append(UiEvent("verdict", ts, {**verdict, "text": ev.get("result", "")}))
        else:
            ok = False
            out.append(UiEvent("error", ts, {"message": "The run ended without a verdict block.",
                                             "subtype": ev.get("subtype"), "text": ev.get("result", "")}))
        out.append(UiEvent("cost", ts, {"usd": ev.get("total_cost_usd"), "label": "API-equivalent cost",
                                        "turns": ev.get("num_turns")}))
        out.append(UiEvent("run_finished", ts, {"ok": ok, "duration_s": round(ts - (self.started_ts or ts), 1)}))
        return out

    def close(self, ts: float) -> list[UiEvent]:
        """Call when the stream ends. A stream with no result event is a failed run."""
        if self.finished:
            return []
        self.finished = True
        return [UiEvent("error", ts, {"message": "The agent stopped before finishing (process ended early)."}),
                UiEvent("run_finished", ts, {"ok": False, "duration_s": round(ts - (self.started_ts or ts), 1)})]
