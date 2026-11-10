"""Claude's review of the healthy baseline map: `uv run python -m noc.mapreport`.

One headless call, no tools: the map JSON goes in, a short review comes out, saved as
baselines/map-report-latest.json and shown in beat 2 labelled as Claude's review.
"""
from __future__ import annotations

import json
import subprocess
import time

from noc import tools
from noc.agent_run import SANDBOX

PROMPT = """You are reviewing a service dependency map that code built from traces, eBPF network flows and the
Kubernetes inventory. Edges read "A calls B" (A depends on B). Grades: confirmed (traces and network flows agree),
likely (one source), unexplained (seen only in network flows from a caller with no owner label and no traces).

Map:
```json
{map}
```

Reply with only one JSON object, no prose:
{{"summary": "<two sentences for an operations audience: how much of the map is trustworthy, and the one thing they should look at>",
  "trust": "<one sentence on which edges are solid and why>",
  "gaps": ["<a specific gap in coverage, naming the service>", "..."],
  "questions": ["<a question an owner should answer, naming the workload>", "..."]}}
Only state what the map shows. At most 3 gaps and 3 questions."""


def main(model: str = "claude-opus-5-5") -> dict:
    base = tools.load_baseline()
    if base is None:
        raise SystemExit("no baseline map saved")
    m = {"edges": [{"edge": e.sentence(), "grade": e.grade, "sources": sorted(e.sources), "evidence": e.evidence}
                   for e in base.edges],
         "nodes": base.nodes}
    SANDBOX.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(["claude", "-p", "--output-format", "json", "--model", model, "--tools", "",
                        "--setting-sources", "local", "--no-session-persistence"],
                       input=PROMPT.format(map=json.dumps(m, indent=1)), capture_output=True, text=True,
                       encoding="utf-8", timeout=300, cwd=SANDBOX)
    res = json.loads(p.stdout)
    text = res.get("result", "")
    body = json.loads(text[text.index("{"):text.rindex("}") + 1])
    report = {**body, "model": model, "generated_utc": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
              "baseline_captured_at": base.captured_at, "cost_usd": res.get("total_cost_usd")}
    (tools.BASELINE.parent / "map-report-latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
