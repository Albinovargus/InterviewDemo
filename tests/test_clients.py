from noc import clients


def test_loki_lines_halves_the_limit_when_the_response_is_too_large(monkeypatch):
    asked = []

    def fake_get(url, params=None, headers=None, attempts=3):
        asked.append(params["limit"])
        if params["limit"] > 1250:
            raise clients.QueryError("returned 500: rpc error: code = ResourceExhausted desc = grpc: received message larger than max")
        return {"data": {"result": [{"values": [["1", "a"], ["2", "b"]]}]}}

    monkeypatch.setattr(clients, "_get", fake_get)
    assert clients.loki_lines("{x}", 0, 1, limit=5000) == ["a", "b"]
    assert asked == [5000, 2500, 1250]


def test_loki_lines_passes_other_errors_through(monkeypatch):
    def fake_get(url, params=None, headers=None, attempts=3):
        raise clients.QueryError("returned 400: bad selector")

    monkeypatch.setattr(clients, "_get", fake_get)
    try:
        clients.loki_lines("{x}", 0, 1, limit=5000)
    except clients.QueryError as e:
        assert "bad selector" in str(e)
    else:
        raise AssertionError("expected QueryError")


def test_fault_tooling_events_are_hidden_from_the_agent():
    chaos = {"involvedObject": {"kind": "PodNetworkChaos", "apiVersion": "chaos-mesh.org/v1alpha1", "name": "astronomy-db-7d7"},
             "reason": "Updated", "message": "Successfully update status of resource"}
    by_controller = {"involvedObject": {"kind": "Pod", "name": "astronomy-db-7d7"}, "reason": "Applied",
                     "source": {"component": "chaos-controller-manager"}, "message": "x"}
    flagd = {"involvedObject": {"kind": "Pod", "name": "flagd-abc"}, "reason": "Started", "message": ""}
    normal = {"involvedObject": {"kind": "Pod", "name": "cart-abc"}, "reason": "BackOff", "message": "Back-off restarting"}
    assert [clients._hidden_event(e) for e in (chaos, by_controller, flagd, normal)] == [True, True, True, False]
