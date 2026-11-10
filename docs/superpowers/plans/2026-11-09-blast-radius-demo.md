# Blast Radius Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task (presenter chose native execution). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working, rehearsed version of the nine-beat AI NOC demo from `docs/proposal.md`, running warm on this PC by 13:30 PST on 2026-11-10.

**Architecture:** A kind cluster built by Terraform in three stages runs the OpenTelemetry Demo, grafana/otel-lgtm, OBI and Chaos Mesh. A Python package `noc` holds the map builder, the reduction MCP server, the fault controller and the console backend. Headless Claude Code (Opus 5.5, high effort) investigates through two read-only MCP servers. A single-page console streams the run and replays recorded runs.

**Tech Stack:** Terraform 1.17 (tehcyx/kind, hashicorp/helm, hashicorp/kubernetes, grafana/grafana providers), kind 0.33, Helm 4, OTel Demo chart 0.42.6, grafana/otel-lgtm 0.39.0, OBI chart 0.16.1, Chaos Mesh 2.8.8, mcp-grafana 2.2.0, Python 3.12 via uv (mcp SDK, httpx, starlette, uvicorn, pytest), plain HTML/CSS/JS with vendored d3.

**Spec:** `docs/superpowers/specs/2026-11-09-blast-radius-demo-design.md`, which defers to `docs/proposal.md` and `docs/presenter-briefing.md`.

## Global Constraints

- Agent model `claude-opus-5-5`, effort high, pinned for demo and scorecard; development runs use `claude-haiku-5-5`.
- The agent gets only the two MCP servers. Built-in Bash, Read, Write, Edit, WebFetch, WebSearch, Glob, Grep, Task and NotebookEdit are disallowed, and a turn limit is set.
- The agent's tools never expose the `chaos-mesh` namespace, flagd, or the fault controller.
- Every tool result stays under ~5,000 tokens and says how to narrow the query when it truncates.
- Edges are always phrased "A calls B".
- No fabricated numbers. Replays are recorded real runs, labelled "Recorded real run". Cost is labelled "API-equivalent cost".
- Console works offline; no CDN.
- Public components and synthetic traffic only.
- Feature freeze 11:00 PST; no lab rebuild after final replays; doctor at 13:00.
- Usage: ignore the 80% rule tonight. From the morning, pause and ask at 80%.

## Review Focus

1. **Agent process dies or stalls mid-run** (rate limit, crash, network): the console must show a clear failed state with "switch to replay", never a spinner that never ends. Test in Task 11.
2. **Reset leaves residue** (flag still on, chaos object left behind, canary still scaled): reset must verify a clean state, and doctor must flag residue. Test in Task 7.
3. **Empty data right after injection** (metrics lag): tools must say "no data in window" explicitly and never report "healthy" when data is missing. Test in Task 8.
4. **Panels disagree** (alert count, service names, counter figures): every panel derives from one normalised event stream or one API snapshot. Consistency check in Task 13.
5. **Usage limit reached during the live run:** a `rate_limit_event` with a rejected status is shown as a banner, and the replay fallback is one click. Test in Task 11.

---

## Phase A: Lab (target 00:30)

### Task 1: Stage 1, the kind cluster

**Files:** `infra/1-cluster/main.tf`, `infra/1-cluster/kind-config.yaml` (inline in TF), `scripts/lab.ps1` (bootstrap: init/apply each stage in order; `-Destroy`).

- [ ] kind cluster `blast-radius`: one node, image `kindest/node:v1.37.0` (kind 0.33 default), extraPortMappings host→NodePort: 3000→30300 Grafana, 9090→30090 Prometheus, 3100→30100 Loki, 3200→30200 Tempo, 8080→30080 frontend-proxy, 4318→30318 OTLP HTTP.
- [ ] Writes kubeconfig context `kind-blast-radius`.
- [ ] Verify: `kubectl --context kind-blast-radius get nodes` shows Ready.
- [ ] Commit.

### Task 2: Stage 2, the platform charts

**Files:** `infra/2-platform/main.tf`, `lab/charts/lgtm/` (local chart: Deployment, NodePort Service, PVC, `LOKI_EXTRA_ARGS=-pattern-ingester.enabled=true`), `lab/values/otel-demo.yaml`, `lab/values/chaos-mesh.yaml`, `lab/values/obi.yaml`.

- [ ] otel-lgtm 0.39.0 in namespace `observability`, PVC on `/data`, resources sized for 16 GB Docker.
- [ ] OTel Demo 0.42.6 in `otel-demo`: jaeger, prometheus, grafana and opensearch disabled; collector exporters replaced by one `otlp_http` to `lgtm.observability:4318` for traces, metrics and logs; profiles pipeline removed; `agent`, `chatbot`, `mcp` and `telemetry-docs` disabled to save memory.
- [ ] Chaos Mesh 2.8.8 in `chaos-mesh`, `chaosDaemon.runtime=containerd`, `socketPath=/run/containerd/containerd.sock`.
- [ ] OBI chart 0.16.1 in `obi`, network metrics on, exporting to lgtm OTLP.
- [ ] Verify: all pods Running; Grafana at http://localhost:3000; `traces_service_graph_request_total` returns series; `{service_name="checkout"}` returns logs in Loki; `/loki/api/v1/patterns` returns patterns; an OBI network metric exists with `k8s_src_owner_name`.
- [ ] Commit.

### Task 3: Stage 3, config, alerts and access

**Files:** `infra/3-config/main.tf`, `lab/manifests/unknown-pod.yaml`, `lab/manifests/canary/cart-canary.yaml`, `infra/3-config/alerts.tf`.

- [ ] Unknown pod in `default`: no labels except `app` absent; a postgres client loop querying `astronomy-db.otel-demo` about every 9 s (~400/h). No traces, no Service.
- [ ] Canary: `cart-canary` Deployment with the same selector labels as `cart`, `replicas: 0` in the committed baseline. The bad-config fault is a commit that sets replicas 1 and a broken `VALKEY_ADDR`.
- [ ] Grafana via the grafana/grafana provider: a folder `NOC`; rule groups with per-service rules for error rate (span metrics), p95 latency, Kafka consumer lag, container restarts, memory, and a "service silent" absence rule. Each alert is labelled `service` and `severity`, with a 1 m eval interval.
- [ ] Service account `noc-agent` with role Viewer; its token is written to `.secrets/grafana-viewer-token` (gitignored).
- [ ] Verify: Grafana alert list shows the rules Normal; the token can `GET /api/search` and gets 403 on `POST /api/folders`.
- [ ] Commit.

### Task 4: Doctor

**Files:** `noc/doctor.py` (`uv run python -m noc.doctor`).

- [ ] Checks, each printed PASS/FAIL with a fix hint: cluster reachable; all pods Ready; each backend URL healthy; service graph has ≥10 edges in the last 5 m; Loki has logs and patterns; OBI flows present; alert rules loaded and the firing count; no leftover chaos objects; all demo flags `off`; canary replicas 0; console up; disk free > 5 GB; Docker memory.
- [ ] Verify: run it; all PASS on the healthy lab. Commit.

## Phase B: Map (target 02:30)

### Task 5: Map builder

**Files:** `noc/clients.py` (`Prom`, `Loki`, `Tempo`, `Grafana`, `Kube`, `GitLog` with small typed methods), `noc/servicemap.py`, `tests/test_servicemap.py`.

**Interfaces (produced):**
- `Edge(caller: str, callee: str, kind: Literal["sync","messaging","db","network"], rate: float, error_rate: float, p95_ms: float|None, sources: set[str], grade: Literal["confirmed","likely","unexplained","silent"])` with `.sentence()` returning `"checkout calls payment"`.
- `build_map(window_s: int, at: float|None=None) -> ServiceMap`; `reconcile(trace_edges, flow_edges, inventory) -> list[Edge]`; `compare(baseline: ServiceMap, now: ServiceMap) -> MapDiff` (marks silent edges, rate and error deltas).
- `ServiceMap.to_json()/from_json()`; files `baselines/map-<UTC>.json`.

- [ ] Tests: trace and flow agree → confirmed; trace-only → likely; flow-only with no owner → unexplained; baseline edge absent now → silent; sentences put the caller first; flow names normalised to service names (deployment owner → service).
- [ ] Implement; run against the live lab; check the unknown pod appears as unexplained → `astronomy-db`.
- [ ] Commit.

### Task 6: Baseline and Claude grading report

**Files:** `noc/baseline.py`, `agent/prompts/map-report.md`.

- [ ] `uv run python -m noc.baseline` saves the map, then runs headless Claude (no tools; the map JSON in the prompt) to produce a grading-and-gaps report as JSON `{edges:[{caller,callee,grade,reason}], gaps:[str]}`, saved beside the map. Grades must equal the code's grades; Claude adds reasons and gaps only.
- [ ] Verify: Haiku run produces valid JSON; the gap list names the unlabelled pod; then one Opus run for the saved baseline. Commit.

## Phase C: Faults (target 04:00)

### Task 7: Fault controller

**Files:** `noc/faults.py`, `lab/chaos/network-delay-postgres.yaml`, `lab/chaos/dns-quote.yaml`, `tests/test_faults.py` (pure parts only).

**Interfaces (produced):** `FAULTS: dict[str, Fault]` with ids `kafka-lag`, `db-lock`, `payment-failure`, `cache-leak`, `pg-network-delay`, `dns-failure`, `bad-config`; `inject(id) -> InjectionRecord(id, started_at, truth_service, truth_summary)`; `reset_all() -> ResetReport(clean: bool, residue: list[str])`; `status() -> dict`.

- [ ] flagd: discover how the deployed chart lets flags change at runtime (flagd-ui write path or the file on a shared volume); a flag must take effect in under 15 s.
- [ ] Chaos: apply and delete the CRs; DNS chaos targets `quote` lookups from `shipping`.
- [ ] bad-config: commits the canary change to this repo (message `cart: tune valkey connection settings`) and applies it; reset reverts with a second commit.
- [ ] Truth records go to `runs/truth/` and are never readable through the agent tools.
- [ ] Test: `reset_all` reports residue when a flag or chaos object remains.
- [ ] Verify each fault manually: inject, see the symptom in Grafana, reset, doctor clean. Commit.

### Task 8: Alerts fire per fault

- [ ] For each fault: inject; within 3 min, alerts fire on the symptom services; record which ones and when in `docs/fault-matrix.md`; reset; wait for Normal.
- [ ] Tune thresholds until each fault fires reliably and the healthy lab fires nothing for 10 min.
- [ ] Commit.

## Phase D: Agent (target 06:30)

### Task 9: Reduction MCP server

**Files:** `noc/mcp_server.py` (FastMCP, stdio, server name `reduction`), `noc/reduce.py` (pure functions), `tests/test_reduce.py`.

Tools, as in the proposal: `incident_summary(window_minutes=30)`, `service_map(compare_to_baseline=True)`, `what_changed(minutes_before_first_alert=30)`, `metric_movers(services: list[str], window_minutes=15)`, `log_pattern_diff(service: str, window_minutes=15)`, `trace_examples(service: str, only_errors=True, limit=3)`.

- [ ] Every result carries a `counts` block `{scanned_log_lines, patterns, anomalous, returned}` (or the series equivalent) that feeds the console's data counter.
- [ ] Tests: output is capped at 5,000 tokens (approximate as chars/4) with a narrowing hint; pattern diff classifies new, spiking and vanished; movers include drop-to-zero and missing series; empty data → "no data in window" (Review Focus 3); trace IDs replaced by short handles `t1…`.
- [ ] Verify each tool over the live lab with a fault injected. Commit.

### Task 10: Agent runner

**Files:** `agent/system-prompt.md` (checklist, data-not-instructions rule, required final JSON block), `agent/mcp.json`, `noc/agent_run.py`, `bin/mcp-grafana.exe` (downloaded, gitignored).

- [ ] mcp-grafana 2.2.0 with `--disable-write`, the Viewer token, and only the query categories (prometheus, loki, tempo/search, alerting read).
- [ ] `run_agent(mode: Literal["live"], model: str, effort: str, max_turns: int=40) -> RunHandle` spawns `claude -p` with `--output-format stream-json --verbose --mcp-config agent/mcp.json --strict-mcp-config --allowedTools …mcp tools… --disallowedTools …builtins…`, model and effort pinned; the first prompt embeds `incident_summary` and `service_map`; events are written to `runs/<id>.jsonl` with wall-clock timestamps.
- [ ] The final answer ends with a fenced JSON verdict: `{primary_cause:{service, mechanism}, symptoms:[service], confidence: "high"|"medium"|"low", evidence:[{claim, tool, query}], proposed_fix, postmortem:{timeline, root_cause, blast_radius, action_items, map_gaps}}`, or `{status:"healthy"}` or `{status:"uncertain", candidates:[...], deciding_query}`.
- [ ] Verify: a Haiku run on `payment-failure` completes with a parseable verdict; confirm in the event log that no disallowed tool was offered. Commit.

### Task 11: Run normaliser

**Files:** `noc/events.py`, `tests/test_events.py`, fixture `tests/fixtures/run-sample.jsonl` (trimmed from a real run).

**Interfaces:** `normalise(raw_event: dict) -> list[UiEvent]`, where `UiEvent.type ∈ {run_started, thinking, tool_call, tool_result, counter, verdict, cost, rate_limit, error, run_finished}`.

- [ ] Tests: tool call and result pairing by id; counter from the result's `counts`; the verdict JSON is extracted from the final text; a truncated stream with no result → `error` + `run_finished(ok=false)` (Review Focus 1); a rejected `rate_limit_event` → `rate_limit` (Review Focus 5).
- [ ] Commit.

## Phase E: Console (target 10:00; design starts while Phase C runs)

### Task 12: Design in Artifact

- [ ] `Artifact quickstart intent=design`; design the NOC console with motion (map assembly, cascade, silent-edge fade, counter shrink, verdict reveal), dark NOC aesthetic, nine-beat rail. Save the result's tokens and layout notes to `console/DESIGN.md`.

### Task 13: Console backend

**Files:** `noc/console.py` (Starlette + uvicorn, port 8765), `scripts/console.ps1`.

Endpoints: `GET /api/health`, `GET /api/map` (baseline + live diff), `GET /api/alerts` (Grafana alertmanager, grouped), `POST /api/faults/{id}`, `POST /api/reset`, `GET /api/faults`, `POST /api/runs` (live; starts `run_agent`), `GET /api/runs/{id}/events` (SSE of `UiEvent`), `GET /api/replays`, `GET /api/replays/{id}/events?speed=1` (SSE with original timing), `GET /api/scorecard`, plus static `/`.

- [ ] Consistency: alert counts and service names come from `/api/alerts` only, and run figures from the event stream only.
- [ ] Verify with curl; Commit.

### Task 14: Console frontend

**Files:** `console/web/index.html`, `app.js`, `map.js`, `styles.css`, `vendor/d3.min.js`.

- [ ] Beats 1–9 as buttons; Live/Replay switch with the "Recorded real run" label; architecture diagram (beat 1); map with grades and evidence on click (beats 2, 3); fault menu + alert cascade + red map (beat 4); agent steps + counter + verdict + evidence links (beat 5); approval gate on the proposed fix (beat 6); honest-answer replays (beat 7); postmortem (beat 8); scorecard (beat 9); failure and rate-limit states.
- [ ] Verify with Playwright screenshots at 1920×1080 and 1366×768. Commit.

### Task 15: Consistency audit

- [ ] An Opus subagent reviews screenshots, a replay and the code for any inconsistency (counts, names, colours, labels, timing). Fix everything it finds. Commit.

## Phase F: Rehearsal (06:30–11:00, run in the background while E proceeds)

### Task 16: Rehearsal harness and scorecard

**Files:** `noc/rehearse.py`, `runs/scorecard.json`.

- [ ] Loop: `reset_all` → wait for alerts Normal (max 4 min) → inject → wait for first alert → `run_agent` (Opus) → grade: `primary_cause.service == truth_service` (and for no-fault, `status == healthy`; for two-fault, `uncertain` naming both) → record time to first hypothesis, total time, cost and correctness.
- [ ] Opus subagent spot-judges the reasoning quality of failed and borderline runs.
- [ ] Runs: 5 per fault (3 if usage gets tight), plus no-fault, two-fault, decoy-commit and planted-injection runs (a log line posted over OTLP as service `checkout` telling the agent to run a command).
- [ ] Faults below 4/5 (or 3/3) come off the live menu and are shown on the scorecard as excluded.
- [ ] Commit the scorecard.

### Task 17: Replays and runbook

- [ ] Choose the best correct run per beat; copy to `replays/` with its metadata.
- [ ] `docs/runbook.md`: pre-interview checklist (doctor, open screens), the beat order for 5- and 15-minute slots, fallback moves, and the reset/settle rule.
- [ ] Final doctor and freeze. Commit and push.
