# Stage 1: the kind cluster. One node keeps Tempo's trace-based service graph
# accurate (every span of a request reaches the same generator).
terraform {
  required_version = ">= 1.9"
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "0.13.0"
    }
  }
}

provider "kind" {}

locals {
  # host port -> NodePort. Fixed NodePorts avoid flaky kubectl port-forwards.
  ports = {
    grafana    = { host = 3000, node = 30300 }
    prometheus = { host = 9090, node = 30090 }
    loki       = { host = 3100, node = 30100 }
    tempo      = { host = 3200, node = 30200 }
    shop       = { host = 8080, node = 30080 }
    otlp_http  = { host = 4318, node = 30318 }
  }
}

resource "kind_cluster" "lab" {
  name            = "blast-radius"
  node_image      = "kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5"
  wait_for_ready  = true
  kubeconfig_path = pathexpand("~/.kube/blast-radius.kubeconfig")

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"

      # etcd on the node's tmpfs (/tmp). On Docker Desktop's WSL2 disk, etcd
      # fsyncs stall for seconds under load (image pulls, Loki/Tempo writes),
      # the control plane loses its leader lease and crash-loops. Memory-backed
      # etcd removes that failure mode; the cost is that cluster state does not
      # survive a restart of the node container, and the lab is rebuilt instead.
      kubeadm_config_patches = [
        <<-EOT
        kind: ClusterConfiguration
        etcd:
          local:
            dataDir: /tmp/etcd
        EOT
      ]

      dynamic "extra_port_mappings" {
        for_each = local.ports
        content {
          container_port = extra_port_mappings.value.node
          host_port      = extra_port_mappings.value.host
          listen_address = "127.0.0.1"
          protocol       = "TCP"
        }
      }
    }
  }
}

output "kubeconfig_path" {
  value = kind_cluster.lab.kubeconfig_path
}

output "context" {
  value = "kind-blast-radius"
}
