import json

from noc.events import Normaliser, extract_verdict

TOOL_RESULT_TEXT = json.dumps({"summary": "1 incident", "counts": {"scanned_log_lines": 120000, "patterns": 80,
                                                                    "anomalous": 4, "returned": 3}})


def assistant(*content):
    return {"type": "assistant", "message": {"content": list(content)}}


def test_tool_call_and_result_pair_by_id_and_strip_server_prefix():
    n = Normaliser()
    out = n.feed(assistant({"type": "tool_use", "id": "toolu_1", "name": "mcp__reduction__log_pattern_diff",
                            "input": {"service": "payment"}}), ts=10.0)
    assert out[0].type == "tool_call"
    assert out[0].data == {"id": "toolu_1", "tool": "log_pattern_diff", "server": "reduction",
                           "input": {"service": "payment"}, "step": 1}
    out = n.feed({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": TOOL_RESULT_TEXT}]}]}},
        ts=12.5)
    kinds = [e.type for e in out]
    assert kinds == ["tool_result", "counter"]
    assert out[0].data["tool"] == "log_pattern_diff" and out[0].data["duration_s"] == 2.5
    assert out[1].data == {"scanned": 120000, "found": 80, "anomalous": 4,
                           "tokens_to_model": len(TOOL_RESULT_TEXT) // 4 + 1}


def test_counter_accumulates_across_tools():
    n = Normaliser()
    for i, counts in enumerate([{"scanned_log_lines": 100, "patterns": 10, "anomalous": 2, "returned": 2},
                                {"scanned_series_points": 5000, "signals": 40, "moved": 3}]):
        n.feed(assistant({"type": "tool_use", "id": f"t{i}", "name": "mcp__reduction__x", "input": {}}), ts=0)
        out = n.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}",
                                                               "content": json.dumps({"counts": counts})}]}}, ts=1)
    assert {k: v for k, v in out[-1].data.items() if k != "tokens_to_model"} == {"scanned": 5100, "found": 50, "anomalous": 5}


def test_counter_counts_tokens_for_results_without_counts():
    n = Normaliser()
    n.feed(assistant({"type": "tool_use", "id": "g1", "name": "mcp__grafana__query_prometheus", "input": {}}), ts=0)
    out = n.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "g1",
                                                           "content": "x" * 400}]}}, ts=1)
    assert [e.type for e in out] == ["tool_result", "counter"]
    assert out[1].data == {"scanned": 0, "found": 0, "anomalous": 0, "tokens_to_model": 101}


def test_verdict_extracted_from_final_text():
    text = 'Payment is failing.\n\n```json\n{"status": "diagnosed", "primary_cause": {"service": "payment"}}\n```'
    assert extract_verdict(text) == {"status": "diagnosed", "primary_cause": {"service": "payment"}}
    assert extract_verdict("no json here") is None


def test_result_event_emits_verdict_cost_and_finish():
    n = Normaliser()
    final = 'Done.\n```json\n{"status": "healthy", "why": "nothing moved"}\n```'
    out = n.feed({"type": "result", "subtype": "success", "is_error": False, "result": final,
                  "total_cost_usd": 1.23, "num_turns": 9, "duration_ms": 70000}, ts=70.0)
    assert [e.type for e in out] == ["verdict", "cost", "run_finished"]
    assert out[0].data["status"] == "healthy"
    assert out[1].data == {"usd": 1.23, "label": "API-equivalent cost", "turns": 9}
    assert out[2].data["ok"] is True


def test_missing_verdict_is_a_failed_run():
    n = Normaliser()
    out = n.feed({"type": "result", "subtype": "success", "is_error": False, "result": "I think it is payment.",
                  "total_cost_usd": 0.5, "num_turns": 3}, ts=5.0)
    assert out[-1].type == "run_finished" and out[-1].data["ok"] is False
    assert any(e.type == "error" for e in out)


def test_stream_that_ends_without_result_is_an_error():
    n = Normaliser()
    n.feed(assistant({"type": "text", "text": "Looking at the incident"}), ts=1.0)
    out = n.close(ts=2.0)
    assert [e.type for e in out] == ["error", "run_finished"]
    assert out[1].data["ok"] is False


def test_rejected_rate_limit_is_surfaced():
    n = Normaliser()
    out = n.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                                                  "resetsAt": 1794295200}}, ts=1.0)
    assert out[0].type == "rate_limit" and out[0].data["status"] == "rejected"


def test_allowed_rate_limit_is_quiet():
    n = Normaliser()
    assert n.feed({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}, ts=1.0) == []


def test_assistant_text_becomes_thinking_note():
    n = Normaliser()
    out = n.feed(assistant({"type": "text", "text": "Checking what changed first."}), ts=3.0)
    assert out[0].type == "narration" and out[0].data["text"] == "Checking what changed first."


def test_prefetch_results_count_toward_the_counter():
    n = Normaliser()
    out = n.add_prefetch({"service_map": {"edges": [], "counts": {"scanned_requests": 22000, "edges": 23, "changed_edges": 2}},
                          "incident_summary": {"counts": {"alerts": 3}}})
    assert out.type == "counter"
    assert out.data["scanned"] == 22000 and out.data["found"] == 23 and out.data["anomalous"] == 2
    assert out.data["tokens_to_model"] > 0
