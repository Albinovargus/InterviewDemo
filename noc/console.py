"""Demo console backend: http://127.0.0.1:8765

Serves the single-page console and a small JSON/SSE API. Live runs and replays are
both read from run files through the same normaliser, so they render identically.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from noc import clients, faults, mapview, tools
from noc.agent_run import RUNS, RunHandle, read_run, run_agent
from noc.events import Normaliser

WEB = clients.ROOT / "console" / "web"
REPLAYS = clients.ROOT / "replays"
SCORECARD = RUNS / "scorecard.json"
DEMO_MODEL, DEMO_EFFORT = "claude-opus-5-5", "high"

log = logging.getLogger("console")
logging.getLogger("httpx").setLevel(logging.WARNING)

_lock = asyncio.Lock()  # an asyncio lock: a thread lock held across await would block the event loop
_live: dict[str, RunHandle] = {}
_fault_job: dict = {"state": "idle"}


def _err(msg: str, status: int = 500) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


# ---- read-only views ---------------------------------------------------------------

async def health(_: Request) -> JSONResponse:
    def check():
        out = {}
        for name, url in [("grafana", f"{clients.GRAFANA}/api/health"), ("prometheus", f"{clients.PROM}/-/ready"),
                          ("loki", f"{clients.LOKI}/ready"), ("tempo", f"{clients.TEMPO}/ready")]:
            try:
                out[name] = clients._http.get(url, timeout=3).status_code == 200
            except Exception:
                out[name] = False
        return out
    return JSONResponse({"backends": await asyncio.to_thread(check), "time": time.time()})


async def map_view(_: Request) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(mapview.payload))
    except clients.QueryError as e:
        return _err(str(e), 503)


async def alerts(_: Request) -> JSONResponse:
    try:
        return JSONResponse(await asyncio.to_thread(mapview.alerts))
    except clients.QueryError as e:
        return _err(str(e), 503)


async def fault_list(_: Request) -> JSONResponse:
    return JSONResponse({"faults": [{"id": f.id, "title": f.title} for f in faults.FAULTS.values()],
                         "job": _fault_job})


def _fault_worker(action: str, fid: str | None) -> None:
    try:
        if action == "inject":
            rec = faults.inject(fid)  # type: ignore[arg-type]
            _fault_job.update(state="done", action=action, fault=fid, at=rec.started_at)
        else:
            rep = faults.reset_all()
            _fault_job.update(state="done" if rep.clean else "error", action=action, residue=rep.residue,
                              at=time.time(), message=None if rep.clean else f"residue: {rep.residue}")
    except Exception as e:
        _fault_job.update(state="error", action=action, fault=fid, message=str(e)[:300], at=time.time())


async def fault_action(request: Request) -> JSONResponse:
    fid = request.path_params.get("id")
    action = "reset" if request.url.path.endswith("/reset") else "inject"
    if action == "inject" and fid not in faults.FAULTS:
        return _err(f"unknown fault {fid}", 404)
    if _fault_job.get("state") == "running":
        return _err("another fault action is still running", 409)
    _fault_job.clear()
    _fault_job.update(state="running", action=action, fault=fid, started=time.time())
    threading.Thread(target=_fault_worker, args=(action, fid), daemon=True).start()
    return JSONResponse(_fault_job, status_code=202)


async def scorecard(_: Request) -> JSONResponse:
    if not SCORECARD.exists():
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, **json.loads(SCORECARD.read_text(encoding="utf-8"))})


def _replay_meta(path: Path) -> dict:
    recs = read_run(path)
    meta = next((r["meta"] for r in recs if "meta" in r), {})
    n = Normaliser()
    verdict, ok, dur, cost = None, False, None, None
    for r in recs:
        if "raw" in r:
            for ev in n.feed(r["raw"], r["ts"]):
                if ev.type == "verdict":
                    verdict = {k: v for k, v in ev.data.items() if k in ("status", "primary_cause", "confidence")}
                elif ev.type == "cost":
                    cost = ev.data.get("usd")
                elif ev.type == "run_finished":
                    ok, dur = ev.data["ok"], ev.data["duration_s"]
    return {"id": path.stem, **{k: meta.get(k) for k in ("title", "scenario", "model", "effort", "started_utc", "beat")},
            "verdict": verdict, "ok": ok, "duration_s": dur, "cost_usd": cost}


async def replays(_: Request) -> JSONResponse:
    REPLAYS.mkdir(exist_ok=True)
    items = await asyncio.to_thread(lambda: [_replay_meta(p) for p in sorted(REPLAYS.glob("*.jsonl"))])
    return JSONResponse({"replays": items})


# ---- runs -------------------------------------------------------------------------------

async def start_run(request: Request) -> JSONResponse:
    async with _lock:
        if any(h.proc.poll() is None for h in _live.values()):
            return _err("an investigation is already running", 409)
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        model = body.get("model") or DEMO_MODEL
        try:
            h = await asyncio.to_thread(run_agent, model, body.get("effort") or DEMO_EFFORT, 40, None, "live")
        except Exception as e:
            return _err(f"could not start the agent: {e}", 500)
        _live[h.id] = h
    return JSONResponse({"id": h.id, "model": model}, status_code=201)


async def active_run(_: Request) -> JSONResponse:
    running = [h for h in _live.values() if h.proc.poll() is None or not h.done.is_set()]
    return JSONResponse({"id": running[-1].id if running else None})


async def stop_run(request: Request) -> JSONResponse:
    h = _live.get(request.path_params["id"])
    if h:
        h.stop()
    return JSONResponse({"stopped": bool(h)})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


async def _stream(path: Path, live: RunHandle | None, speed: float):
    n = Normaliser()
    pos = 0
    prev_ts = None
    idle_since = time.time()
    while True:
        lines = []
        if path.exists():
            with path.open(encoding="utf-8") as f:
                f.seek(pos)
                chunk = f.read()
                if chunk:
                    complete, _, rest = chunk.rpartition("\n")
                    if complete:
                        lines = complete.split("\n")
                        pos += len((complete + "\n").encode("utf-8"))
        if not lines:
            if live is None or (live.done.is_set() and pos >= path.stat().st_size):
                break
            if time.time() - idle_since > 600:
                yield _sse({"type": "error", "ts": time.time(), "data": {"message": "No events for 10 minutes."}})
                break
            await asyncio.sleep(0.25)
            continue
        idle_since = time.time()
        for line in lines:
            if not line.strip():
                continue
            rec = json.loads(line)
            ts = rec.get("ts", time.time())
            if live is None and prev_ts is not None and speed > 0:
                await asyncio.sleep(min(max(ts - prev_ts, 0) / speed, 20))
            prev_ts = ts
            if "meta" in rec:
                yield _sse({"type": "meta", "ts": ts, "data": rec["meta"]})
            elif "prefetch" in rec:
                p = rec["prefetch"]
                yield _sse({"type": "prefetch", "ts": ts, "data": p})
            elif "raw" in rec:
                for ev in n.feed(rec["raw"], ts):
                    yield _sse(ev.to_dict())
            elif "exit" in rec:
                for ev in n.close(ts):
                    ev.data.setdefault("stderr", rec["exit"].get("stderr", "")[-300:])
                    yield _sse(ev.to_dict())
    for ev in n.close(time.time()):
        yield _sse(ev.to_dict())
    yield _sse({"type": "stream_end", "ts": time.time(), "data": {}})


def _sse_response(gen) -> StreamingResponse:
    return StreamingResponse(gen, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def run_events(request: Request) -> StreamingResponse | JSONResponse:
    rid = request.path_params["id"]
    path = RUNS / f"{rid}.jsonl"
    if not path.exists():
        return _err("no such run", 404)
    return _sse_response(_stream(path, _live.get(rid) or RunHandle(rid, path, None, 0, _done()), 0))  # type: ignore[arg-type]


def _done() -> threading.Event:
    e = threading.Event()
    e.set()
    return e


async def replay_events(request: Request) -> StreamingResponse | JSONResponse:
    path = REPLAYS / f"{request.path_params['id']}.jsonl"
    if not path.exists() or path.parent != REPLAYS:
        return _err("no such replay", 404)
    speed = float(request.query_params.get("speed", "1"))
    return _sse_response(_stream(path, None, speed))


async def index(_: Request) -> FileResponse:
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})


app = Starlette(routes=[
    Route("/", index),
    Route("/api/health", health),
    Route("/api/map", map_view),
    Route("/api/alerts", alerts),
    Route("/api/faults", fault_list),
    Route("/api/faults/reset", fault_action, methods=["POST"]),
    Route("/api/faults/{id}", fault_action, methods=["POST"]),
    Route("/api/scorecard", scorecard),
    Route("/api/replays", replays),
    Route("/api/replays/{id}/events", replay_events),
    Route("/api/runs", start_run, methods=["POST"]),
    Route("/api/runs/active", active_run),
    Route("/api/runs/{id}/stop", stop_run, methods=["POST"]),
    Route("/api/runs/{id}/events", run_events),
    Mount("/", StaticFiles(directory=WEB), name="static"),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
