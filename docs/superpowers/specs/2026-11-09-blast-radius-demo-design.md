# Blast Radius demo: build design

Date: 2026-11-09 · Interview: 2026-11-10 14:00 PST · Presenter machine: this PC

`docs/proposal.md` and `docs/presenter-briefing.md` are the product spec. This document records how the demo gets built in the ~17 hours available: the decisions the presenter made, the decisions I made (marked **my call**), and what each piece must do to count as finished.

## Intent

An interviewer at a large Kubernetes shop should see an AI "second analyst" that (1) builds a service dependency map from ordinary telemetry and (2) diagnoses a real fault the interviewer picks, with every claim checkable and no ability to change anything. The demo has to be **rock solid**: nothing on screen may contradict anything else, and every number shown must be real.

## Decisions made by the presenter

- **Scope: everything in the proposal.** All nine beats and all seven faults are attempted. The proposal's own rule still applies: a fault that does not pass rehearsal consistently comes off the menu, and the scorecard says so.
- **Presenter machine: this PC** (Windows 11, i7-10700KF, 32 GB, Docker Desktop on WSL2 with 16 GB, kernel 6.6). It is built and rehearsed here and left warm for the interview.
- **Agent model: Claude Opus 5.5, high effort**, pinned for the demo and the scorecard runs. Development iterations use Haiku 5.5, and Opus subagents judge results and review code. Scorecard target is 5 runs per kept fault; if the Max plan's usage limit gets close, it drops to 3 and the scorecard says so.
- **Testing:** manual testing at every step, plus a few tasteful automated tests. No heavy test infrastructure.
- **Design:** the demo screen is designed with the Artifact design tool, with deliberate motion design; the design is pre-approved.
- **Approvals given:** install CLI tools; commit and push to `main`; keep the PC awake; prune unused Docker images (never volumes); stop the unrelated `tracks_app` Supabase containers (done; their restart policy was set to `no`, so `supabase start` restores them).

## Anti-goals

- **Fake data presented as real.** No scripted telemetry, no invented numbers, no replay that is not a recorded real run. Replays are labelled as replays.
- **Inconsistency.** A count, name, colour or timing that disagrees between two parts of the screen, or between the screen and Grafana.
- **A demo-day failure with no fallback.** Every live beat has a recorded replay behind it, and the screen works with no internet.
- **Internal or confidential content.** Public components and synthetic traffic only.

## Architecture (my call where not in the proposal)

```
kind cluster (1 node, extraPortMappings → localhost)
 ├─ otel-demo        OpenTelemetry Demo (Helm), bundled backends off, exports OTLP to lgtm
 ├─ observability    grafana/otel-lgtm (pinned): Collector, Prometheus, Loki (+pattern ingester), Tempo (+service graphs), Grafana (+alert rules)
 ├─ obi              OBI DaemonSet (gated on BTF)
 ├─ chaos-mesh       Chaos Mesh (containerd runtime) (gated on smoke test)
 └─ default          the unlabelled "unknown" pod querying PostgreSQL

host
 ├─ mcp/reduction    custom MCP server, Python: the six tools (proposal table)
 ├─ mcp-grafana      official binary, --disable-write, Viewer service account
 ├─ agent/           system prompt + checklist; runner = claude -p --output-format stream-json
 ├─ console/         demo screen: local web server + one page
 ├─ faults/          inject/reset per fault (never visible to the agent)
 └─ runs/, replays/  recorded stream-json runs; replays are copies of real runs
```

- **Ports via kind `extraPortMappings` to NodePorts**, not `kubectl port-forward`, which drops connections. Grafana, Prometheus, Loki, Tempo and flagd's API are reachable on fixed localhost ports.
- **Python 3.12 via uv** for the reduction MCP server and the console backend: one backend language, and it matches the official MCP SDK. Frontend is plain HTML, CSS and JS with libraries **vendored locally**, so the screen works offline.
- **The console backend owns everything the agent must not see:** fault injection, reset, flagd and Chaos Mesh. The agent only gets the two MCP servers.
- **Terraform in three stages** (kind / Helm / config) as the proposal says, with a bootstrap script that runs them in order and a `doctor` script that checks every component is healthy. The doctor script is also the pre-interview checklist.
- **Map building is code**, and the result is saved as a versioned baseline JSON. Claude's grading and gap report runs once on the healthy baseline and is saved too. Beat 2 can therefore animate a real saved result instantly, or re-run live.

## Demo screen

One page shaped like a NOC console: the dependency map (centre), alert feed, agent steps, a data counter ("log lines → patterns → anomalous → shown to model"), the verdict with evidence links, and a button per beat. It has a Live / Replay switch, with replay labelled on screen. Motion is used for meaning only: edges draw in as the map assembles, an edge fades to dashed when it goes silent, alerts cascade in, the cause node pulses while the counter shrinks. Every number comes from the same event stream, so the panels cannot disagree. Grafana links open the real queries.

## Definition of done, per beat

| Beat | Done when |
|---|---|
| 1 Stack | Grafana shows live traffic; architecture diagram on the screen |
| 2 Map | Real map from 3 sources, edges graded confirmed/likely/unexplained, evidence on click |
| 3 Unknown caller | The unlabelled pod's PostgreSQL connections appear as an unexplained edge (OBI), or the beat is replay-only if OBI cannot run |
| 4 Break something | A fault from the menu fires real Grafana alerts within ~2 min, and the map turns red |
| 5 Diagnosis | Live headless Opus run streams steps; verdict names the right cause; counter shows real counts |
| 6 Guardrail | Proposed fix awaits human approval; nothing is applied by the agent |
| 7 Honest answer | Recorded two-fault (agent hedges) and no-fault (agent says healthy) runs |
| 8 Postmortem | Drafted from the same run as beat 5 |
| 9 Scorecard | Real rehearsal results: k of N per fault, median time, cost per run |

## Testing approach

- Manual check after every component: query it, look at it, screenshot the screen (Playwright).
- Automated tests only where logic is pure and wrong answers would be silent: map reconciliation and grading inputs, log-pattern diff, metric-mover ranking, tool-output size limits, stream-event parsing.
- `doctor` health script, run before every rehearsal and before the interview.
- Rehearsal is the real test: each fault is injected fresh, the agent runs, and an Opus subagent grades the verdict against the injected truth. Results feed the scorecard.

## Resolved after the builder check

- **Screen design:** made by me with the Artifact design tool during the build; the presenter pre-approved it.
- **Work is unattended overnight.** I do every manual check myself (queries, Playwright screenshots), and Opus subagents judge results. There are no presenter sign-off checkpoints.
- **Usage limit:** the 7-day window was at 88% at 20:50 PST and resets at 22:00 PST. The presenter said to ignore the 80% pause rule tonight. From tomorrow morning: at 80% usage, pause and ask. The live run at 14:00 has priority over extra rehearsal runs.
- **Cut order (my call):** build the 5-minute set first (beats 2, 4, 5, 6), then 8, 7, 9, 3 and 1. Fault priority: flagd faults first (paymentFailure, productCatalogLockContention, kafkaQueueProblems, recommendationCacheFailure), then the canary bad-config, then Chaos Mesh network delay and DNS.
- **Timeline (my call):** feature freeze at 11:00 PST; after that only fixes and rehearsal. The lab is never rebuilt after the final replays are recorded. Doctor runs at 13:00, and the screen is open by 13:30.
- **Cost on screen (my call):** the run's `total_cost_usd`, labelled "API-equivalent cost", because on the Max plan it is an estimate at API prices, not a charge.
- **Evidence links in replays (my call):** every tool result is saved inside the run file, so a replay shows the recorded result directly. A secondary "open in Grafana" link opens the same query over the recorded time window. otel-lgtm gets a persistent volume so a pod restart does not lose data, and Loki retention covers the interview window.

## Left to the builder

Exact chart and image versions (pinned once verified), alert rule thresholds, the layout details of the screen within the approved design, and the order in which gated items are attempted.
