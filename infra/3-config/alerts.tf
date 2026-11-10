# The NOC's alert feed: a small set of standard, per-service Grafana alert rules.
# Each rule is multi-dimensional, so one fault fires one alert per affected
# service, which is what produces the storm the agent has to see through.

locals {
  server = "span_kind=~\"SPAN_KIND_SERVER|SPAN_KIND_CONSUMER\", service!~\"flagd|frontend-web|load-generator\""
  client = "span_kind=~\"SPAN_KIND_CLIENT|SPAN_KIND_PRODUCER\", service!~\"flagd|frontend-web|load-generator\""
  calls  = "traces_spanmetrics_calls_total"

  rules = {
    ErrorRateHigh = {
      severity = "critical"
      summary  = "{{ $labels.service }}: more than 5% of requests are failing"
      expr     = "(sum by (service)(rate(${local.calls}{${local.server}, status_code=\"STATUS_CODE_ERROR\"}[2m])) / sum by (service)(rate(${local.calls}{${local.server}}[2m]))) > 0.05 and on (service) sum by (service)(rate(${local.calls}{${local.server}}[2m])) > 0.02"
    }
    DownstreamCallErrors = {
      severity = "warning"
      summary  = "{{ $labels.service }}: calls to its dependencies are failing"
      expr     = "sum by (service)(rate(${local.calls}{${local.client}, status_code=\"STATUS_CODE_ERROR\"}[2m])) > 0.02"
    }
    LatencyHigh = {
      severity = "warning"
      summary  = "{{ $labels.service }}: p95 latency is more than double its usual level"
      expr     = "histogram_quantile(0.95, sum by (service, le)(rate(traces_spanmetrics_latency_bucket{${local.server}}[2m]))) > 2 * histogram_quantile(0.95, sum by (service, le)(rate(traces_spanmetrics_latency_bucket{${local.server}}[10m] offset 30m))) and on (service) histogram_quantile(0.95, sum by (service, le)(rate(traces_spanmetrics_latency_bucket{${local.server}}[2m]))) > 0.25"
    }
    KafkaConsumerLag = {
      severity = "critical"
      summary  = "{{ $labels.service }}: Kafka consumer group is falling behind"
      expr     = "label_replace(sum by (group)(kafka_consumer_group_lag_sum_ratio), \"service\", \"$1\", \"group\", \"(.*)\") > 100"
    }
    ContainerRestarts = {
      severity = "critical"
      summary  = "{{ $labels.service }}: container restarted"
      expr     = "label_replace(sum by (k8s_container_name)(increase(k8s_container_restarts{k8s_namespace_name=\"otel-demo\"}[10m])), \"service\", \"$1\", \"k8s_container_name\", \"(.*)\") > 0.5"
    }
    MemoryNearLimit = {
      severity = "warning"
      summary  = "{{ $labels.service }}: memory above 80% of its limit"
      expr     = "label_replace(sum by (k8s_container_name)(container_memory_working_set_bytes{k8s_namespace_name=\"otel-demo\"}) / sum by (k8s_container_name)(k8s_container_memory_limit_bytes{k8s_namespace_name=\"otel-demo\"}), \"service\", \"$1\", \"k8s_container_name\", \"(.*)\") > 0.8"
    }
    ServiceSilent = {
      severity = "critical"
      summary  = "{{ $labels.service }}: stopped handling requests"
      expr     = "(sum by (service)(rate(${local.calls}{${local.server}}[10m] offset 20m)) > 0.05) unless on (service) (sum by (service)(rate(${local.calls}{${local.server}}[2m])) > 0.002)"
    }
  }
}

resource "grafana_rule_group" "noc" {
  name             = "noc-standard"
  folder_uid       = grafana_folder.noc.uid
  interval_seconds = 30

  dynamic "rule" {
    for_each = local.rules
    content {
      name           = rule.key
      condition      = "B"
      for            = "1m"
      no_data_state  = "OK"
      exec_err_state = "OK"
      labels         = { severity = rule.value.severity, team = "noc" }
      annotations    = { summary = rule.value.summary }

      data {
        ref_id         = "A"
        datasource_uid = "prometheus"
        relative_time_range {
          from = 600
          to   = 0
        }
        model = jsonencode({ refId = "A", expr = rule.value.expr, instant = true, range = false })
      }

      data {
        ref_id         = "B"
        datasource_uid = "__expr__"
        relative_time_range {
          from = 0
          to   = 0
        }
        model = jsonencode({
          refId      = "B"
          type       = "threshold"
          expression = "A"
          conditions = [{ evaluator = { type = "gt", params = [0] } }]
        })
      }
    }
  }
}
