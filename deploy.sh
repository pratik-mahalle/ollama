#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ============================================================
# deploy.sh — Deploy Open Source LLM Stack on Kubernetes
# Ollama + Qwen 2.5 + Grafana + Prometheus + Chatbot UI (incl. benchmark.html) + Benchmark API
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

echo ""
echo "============================================"
echo "  Open Source LLM on Kubernetes — Deployer"
echo "  Model: Qwen 2.5 (0.5B) via Ollama"
echo "============================================"
echo ""

# Pre-flight checks
command -v kubectl >/dev/null 2>&1 || { err "kubectl not found. Install it first."; exit 1; }
command -v docker >/dev/null 2>&1 || { err "docker not found (required to build llm-benchmark:latest)."; exit 1; }
command -v minikube >/dev/null 2>&1 && PLATFORM="minikube" || PLATFORM="generic"

if [ "$PLATFORM" = "minikube" ]; then
  if ! minikube status | grep -q "Running"; then
    warn "minikube is not running. Starting with 4 CPUs and 6GB RAM..."
    minikube start --cpus=4 --memory=6144 --driver=docker
  fi
  log "minikube is running"
fi

kubectl cluster-info >/dev/null 2>&1 || { err "Cannot connect to Kubernetes cluster."; exit 1; }
log "Connected to Kubernetes cluster"

# Build benchmark API image (benchmark UI is embedded in 04-chatbot-ui ConfigMap)
echo ""
log "Building benchmark API image (llm-benchmark:latest)..."
if [ "$PLATFORM" = "minikube" ]; then
  eval "$(minikube docker-env)"
fi
docker build -t llm-benchmark:latest "${SCRIPT_DIR}/benchmark"

# Deploy in order
echo ""
log "Step 1/4: Creating namespace..."
kubectl apply -f "${SCRIPT_DIR}/k8s-manifests/01-namespace.yaml"

log "Step 2/4: Deploying Ollama with Qwen 2.5..."
kubectl apply -f "${SCRIPT_DIR}/k8s-manifests/02-ollama.yaml"

log "Step 3/4: Deploying monitoring stack (Prometheus + Grafana)..."
kubectl apply -f "${SCRIPT_DIR}/k8s-manifests/03-monitoring.yaml"

log "Step 4/4: Deploying Chatbot UI + benchmark API..."
kubectl apply -f "${SCRIPT_DIR}/k8s-manifests/04-chatbot-ui.yaml"
kubectl apply -f "${SCRIPT_DIR}/k8s-manifests/05-benchmark.yaml"

if [ -f "${SCRIPT_DIR}/.env" ]; then
  echo ""
  log "Found .env — syncing keys into Secret benchmark-api-keys (values not shown)..."
  kubectl create secret generic benchmark-api-keys -n llm-stack \
    --from-env-file="${SCRIPT_DIR}/.env" \
    --dry-run=client -o yaml | kubectl apply -f -
  kubectl rollout restart deployment/benchmark-api -n llm-stack 2>/dev/null || true
  log "benchmark-api restarted to pick up API keys from .env"
elif [ -f "${SCRIPT_DIR}/.enc" ]; then
  command -v python3 >/dev/null 2>&1 || { err "python3 not found (required to read .enc). Install python3 or use .env instead."; exit 1; }
  echo ""
  log "Found .enc — converting and syncing Secret benchmark-api-keys (values not shown)..."
  _bench_env="$(mktemp)"
  python3 "${SCRIPT_DIR}/scripts/enc-to-k8s-envfile.py" "${SCRIPT_DIR}/.enc" > "$_bench_env"
  kubectl create secret generic benchmark-api-keys -n llm-stack \
    --from-env-file="$_bench_env" \
    --dry-run=client -o yaml | kubectl apply -f -
  rm -f "$_bench_env"
  kubectl rollout restart deployment/benchmark-api -n llm-stack 2>/dev/null || true
  log "benchmark-api restarted to pick up API keys from .enc"
else
  echo ""
  warn "No .env or .enc in repo root — commercial APIs stay disabled until you add keys."
  warn "Use .env (see .env.example) or legacy .enc (see .enc.example), then re-run ./deploy.sh"
fi

echo ""
log "All resources applied. Waiting for pods to start..."
echo ""

# Wait for Ollama to be ready
echo -n "  Waiting for Ollama pod... "
kubectl wait --for=condition=ready pod -l app=ollama -n llm-stack --timeout=180s 2>/dev/null && echo "ready" || echo "timeout (may still be pulling image)"

# Wait for Grafana
echo -n "  Waiting for Grafana pod... "
kubectl wait --for=condition=ready pod -l app=grafana -n llm-stack --timeout=120s 2>/dev/null && echo "ready" || echo "timeout"

# Wait for chatbot
echo -n "  Waiting for Chatbot UI pod... "
kubectl wait --for=condition=ready pod -l app=chatbot-ui -n llm-stack --timeout=60s 2>/dev/null && echo "ready" || echo "timeout"

echo -n "  Waiting for benchmark-api pod... "
kubectl wait --for=condition=ready pod -l app=benchmark-api -n llm-stack --timeout=120s 2>/dev/null && echo "ready" || echo "timeout"

# Wait for model pull job
echo ""
log "Waiting for Qwen 2.5 model pull (this takes 2-5 minutes on first run)..."
kubectl wait --for=condition=complete job/pull-qwen-model -n llm-stack --timeout=600s 2>/dev/null && log "Model pulled successfully!" || warn "Model pull still in progress. Check with: kubectl logs job/pull-qwen-model -n llm-stack"

echo ""
echo "============================================"
echo "  Deployment Complete!"
echo "============================================"
echo ""

# Show access info
if [ "$PLATFORM" = "minikube" ]; then
  CHATBOT_URL=$(minikube service chatbot-ui -n llm-stack --url 2>/dev/null || echo "Run: minikube service chatbot-ui -n llm-stack")
  GRAFANA_URL=$(minikube service grafana -n llm-stack --url 2>/dev/null || echo "Run: minikube service grafana -n llm-stack")
  echo "  Chatbot UI:  $CHATBOT_URL"
  echo "  Grafana:     $GRAFANA_URL"
else
  echo "  Chatbot UI:  http://<node-ip>:30080"
  echo "  Grafana:     http://<node-ip>:30300"
fi

echo ""
echo "  Grafana credentials: admin / admin"
echo ""
echo "  Benchmark: open Chatbot URL → Benchmark, or:"
echo "    kubectl port-forward svc/benchmark-api 8000:8000 -n llm-stack"
echo "    curl -sS -X POST http://127.0.0.1:8000/run -H 'Content-Type: application/json' -d '{}' | jq ."
echo ""
echo "  Useful commands:"
echo "    kubectl get pods -n llm-stack"
echo "    kubectl logs -f deployment/ollama -n llm-stack"
echo "    kubectl logs -f deployment/benchmark-api -n llm-stack"
echo "    kubectl logs job/pull-qwen-model -n llm-stack"
echo ""
echo "  To test Ollama directly:"
echo "    kubectl port-forward svc/ollama 11434:11434 -n llm-stack"
echo '    curl http://localhost:11434/api/chat -d '\''{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Which model are you?"}],"stream":false}'\'''
echo ""
