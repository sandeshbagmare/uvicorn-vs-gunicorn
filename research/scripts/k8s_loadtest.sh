#!/usr/bin/env bash
# Load-test the research deployment ON A REAL CLUSTER and capture latency vs pod count.
#
# Prereqs: kubectl context pointing at your cluster; the manifest applied
#          (kubectl apply -f research/manifests/k8s-gunicorn-4workers.yaml).
#
# It port-forwards the Service to localhost and drives it with this repo's
# benchmarks/loadtest.py at several pod counts so you get ground-truth numbers
# (each pod has its own real 4 CPUs -- the thing a single laptop cannot emulate).
#
# Usage:
#   research/scripts/k8s_loadtest.sh /async-io 4000 200
#   research/scripts/k8s_loadtest.sh /cpu      1200 100
set -euo pipefail

ENDPOINT="${1:-/async-io}"
REQUESTS="${2:-4000}"
CONCURRENCY="${3:-200}"
SVC="svc/uvg-research"
LOCAL_PORT="${LOCAL_PORT:-18080}"
OUT_DIR="research/data/k8s"

mkdir -p "$OUT_DIR"

echo "Port-forwarding $SVC -> localhost:$LOCAL_PORT ..."
kubectl port-forward "$SVC" "${LOCAL_PORT}:80" >/tmp/uvg_pf.log 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 3

for PODS in 1 2 3 4; do
  echo
  echo "=== Scaling deployment to $PODS pod(s) ==="
  kubectl scale deployment/uvg-research --replicas="$PODS"
  kubectl rollout status deployment/uvg-research --timeout=120s
  # give kube-proxy/endpoints a moment to converge
  sleep 5
  LABEL="k8s-${PODS}pods-4w$(echo "$ENDPOINT" | tr '/' '_')"
  echo "--- load test: $PODS pods, $ENDPOINT, $REQUESTS reqs @ conc $CONCURRENCY ---"
  python benchmarks/loadtest.py \
    --url "http://127.0.0.1:${LOCAL_PORT}" \
    --endpoint "$ENDPOINT" \
    --requests "$REQUESTS" \
    --concurrency "$CONCURRENCY" \
    --label "$LABEL" \
    --server "Gunicorn 4 UvicornWorkers x ${PODS} pods (real cluster)" \
    --out "$OUT_DIR"
done

echo
echo "Done. Per-pod-count JSON written to $OUT_DIR/."
echo "Compare the p95/p99 columns across pod counts to see latency improve as you scale out."
