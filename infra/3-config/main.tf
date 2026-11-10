# Stage 3: configuration on top of the running platform: the unknown workload,
# the canary Deployment, Grafana alert rules and the agent's read-only access.
terraform {
  required_version = ">= 1.9"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "3.5.0"
    }
    grafana = {
      source  = "grafana/grafana"
      version = "4.54.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "2.9.1"
    }
  }
}

variable "kubeconfig" {
  type    = string
  default = "~/.kube/blast-radius.kubeconfig"
}

variable "grafana_admin_password" {
  type    = string
  default = "blast-radius-admin" # lab-only; matches lab/charts/lgtm/values.yaml
}

provider "kubernetes" {
  config_path = pathexpand(var.kubeconfig)
}

provider "grafana" {
  url  = "http://127.0.0.1:3000"
  auth = "admin:${var.grafana_admin_password}"
}

locals {
  lab = "${path.module}/../../lab"
}

# ---- Workloads ------------------------------------------------------------------

resource "kubernetes_manifest" "unknown_pod" {
  manifest = yamldecode(file("${local.lab}/manifests/unknown-pod.yaml"))
}

resource "kubernetes_manifest" "fault_probe" {
  manifest = yamldecode(file("${local.lab}/manifests/probe.yaml"))
}

# ---- The agent's read-only Grafana identity --------------------------------------

resource "grafana_service_account" "agent" {
  name        = "noc-agent"
  role        = "Viewer"
  is_disabled = false
}

resource "grafana_service_account_token" "agent" {
  name               = "noc-agent-token"
  service_account_id = grafana_service_account.agent.id
}

resource "local_sensitive_file" "agent_token" {
  content  = grafana_service_account_token.agent.key
  filename = "${path.module}/../../.secrets/grafana-viewer-token"
}

resource "grafana_folder" "noc" {
  title = "NOC"
  uid   = "noc"
}
