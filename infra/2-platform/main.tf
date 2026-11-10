# Stage 2: the platform. Helm charts only; custom resources and config come in
# stage 3, after the CRDs installed here exist.
terraform {
  required_version = ">= 1.9"
  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = "3.3.0"
    }
  }
}

variable "kubeconfig" {
  type    = string
  default = "~/.kube/blast-radius.kubeconfig"
}

provider "helm" {
  kubernetes = {
    config_path = pathexpand(var.kubeconfig)
  }
}

locals {
  lab = "${path.module}/../../lab"
}

resource "helm_release" "lgtm" {
  name             = "lgtm"
  chart            = "${local.lab}/charts/lgtm"
  namespace        = "observability"
  create_namespace = true
  wait             = true
  timeout          = 600
}

resource "helm_release" "otel_demo" {
  name             = "otel-demo"
  repository       = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart            = "opentelemetry-demo"
  version          = "0.42.6"
  namespace        = "otel-demo"
  create_namespace = true
  values           = [file("${local.lab}/values/otel-demo.yaml")]
  wait             = true
  timeout          = 1200
  depends_on       = [helm_release.lgtm]
}

resource "helm_release" "chaos_mesh" {
  name             = "chaos-mesh"
  repository       = "https://charts.chaos-mesh.org"
  chart            = "chaos-mesh"
  version          = "2.8.8"
  namespace        = "chaos-mesh"
  create_namespace = true
  values           = [file("${local.lab}/values/chaos-mesh.yaml")]
  wait             = true
  timeout          = 600
}

resource "helm_release" "obi" {
  name             = "obi"
  repository       = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart            = "opentelemetry-ebpf-instrumentation"
  version          = "0.16.1"
  namespace        = "obi"
  create_namespace = true
  values           = [file("${local.lab}/values/obi.yaml")]
  wait             = true
  timeout          = 600
  depends_on       = [helm_release.lgtm]
}
