# How to Deploy an Open Source LLM Reliably on Kubernetes

---

## What we're building

```
┌──────────────────────────────────────────────────────────┐
│                  Kubernetes (minikube)                   │
│  Namespace: llm-stack                                    │
│                                                          │
│  ┌─────────────┐   ┌──────────────────────────────────┐  │
│  │  Ollama     │   │  Prometheus + Grafana            │  │
│  │  Qwen 2.5   │   │  node-exporter + kube-state      │  │
│  │  port 11434 │   │  port 30300                      │  │
│  └──────┬──────┘   └──────────────────────────────────┘  │
│         │                                                │
│  ┌──────▼──────┐   ┌──────────────────┐                  │
│  │  Chatbot UI │   │  Benchmark API   │                  │
│  │  nginx      │   │  FastAPI         │                  │
│  │  port 30080 │   │  port 8000       │                  │
│  └─────────────┘   └──────────────────┘                  │
└──────────────────────────────────────────────────────────┘
```

---

## Prerequisites

- Docker Desktop running
- `kubectl` installed
- `minikube` installed

---

## Step 1 — Start the Kubernetes cluster

```bash
minikube start --cpus=4 --memory=6144 --driver=docker
```

**What to look for:** `Done! kubectl is now configured to use "minikube" cluster`

Confirm the cluster is up and the node is Ready:

```bash
kubectl get nodes
```

Expected output:
```
NAME       STATUS   ROLES           AGE   VERSION
minikube   Ready    control-plane   Xs    v1.XX.X
```

---

## Step 2 — Create the namespace

All resources live in a dedicated namespace called `llm-stack`.

```bash
kubectl apply -f k8s-manifests/01-namespace.yaml
```

Verify it was created:

```bash
kubectl get namespace llm-stack
```

---

## Step 3 — Deploy Ollama

Ollama is the model server. It will run Qwen 2.5 inside the cluster.

The manifest `02-ollama.yaml` creates three things:
- A **PersistentVolumeClaim** (10 Gi) so the downloaded model survives pod restarts
- A **Deployment** running `ollama/ollama:latest` with CPU and memory limits, readiness and liveness probes
- A **ClusterIP Service** so other pods can reach Ollama at `ollama.llm-stack.svc.cluster.local:11434`

```bash
kubectl apply -f k8s-manifests/02-ollama.yaml
```

Watch the pod come up:

```bash
kubectl get pods -n llm-stack -w
```

Wait until the `ollama-...` pod shows `Running`. Press `Ctrl+C` to stop watching.

---

## Step 4 — Pull the Qwen 2.5 model

The model pull runs as a Kubernetes **Job**. It waits for Ollama to be ready (init container), then calls Ollama's pull API to download `qwen2.5:0.5b` (~300 MB).

The Job was already created by the previous `apply`. Check its progress:

```bash
kubectl logs job/pull-qwen-model -n llm-stack -f
```

Wait until you see `Model pull complete` and `Model verified!`. Press `Ctrl+C`.

Confirm the model is loaded:

```bash
kubectl port-forward svc/ollama 11434:11434 -n llm-stack &
curl -s http://localhost:11434/api/tags | python3 -m json.tool
```

You should see `qwen2.5:0.5b` listed. Now do a quick smoke test:

```bash
curl http://localhost:11434/api/chat \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Which model are you?"}],"stream":false}' \
  | python3 -m json.tool
```

Stop the port-forward when done:

```bash
kill %1
```

---

## Step 5 — Deploy monitoring (Prometheus + Grafana)

The manifest `03-monitoring.yaml` creates:
- **Prometheus** — scrapes metrics from Ollama, node-exporter, and kube-state-metrics
- **node-exporter** DaemonSet — exports host CPU, memory, disk metrics
- **kube-state-metrics** Deployment — exports Kubernetes object state (pod status, restarts, etc.)
- **Grafana** — pre-provisioned with a Prometheus datasource and a cluster health dashboard

```bash
kubectl apply -f k8s-manifests/03-monitoring.yaml
```

Wait for Grafana to be ready:

```bash
kubectl get pods -n llm-stack -w
```

Open Grafana in the browser:

```bash
minikube service grafana -n llm-stack
```

Log in with **admin / admin**. The "LLM Stack — Kubernetes Cluster Health" dashboard loads automatically.

**Dashboard panels:**
- CPU usage by pod
- Memory usage by pod
- Pod status + restart count
- Network I/O
- Ollama API p50 / p95 latency
- Node CPU / Memory / Disk gauges

---

## Step 6 — Deploy the Chatbot UI

The manifest `04-chatbot-ui.yaml` deploys an **nginx** pod that serves two pages directly from a Kubernetes ConfigMap (no Docker build needed for the UI):

- `/` — Chat page, sends messages to Ollama through nginx reverse proxy
- `/benchmark` — Benchmark results page

nginx is configured to proxy:
- `/ollama/*` → `ollama.llm-stack.svc.cluster.local:11434`
- `/benchmark-api/*` → `benchmark-api.llm-stack.svc.cluster.local:8000`

```bash
kubectl apply -f k8s-manifests/04-chatbot-ui.yaml
```

Open the chat UI:

```bash
minikube service chatbot-ui -n llm-stack
```

**Try these prompts:**
- `Which model are you?`
- `What's the latest information you have?`
- `Explain Kubernetes in three sentences`
- `Write a Python function to reverse a linked list`

---

## Step 7 — Build the benchmark image

The benchmark API needs to be built locally. For minikube, we build inside minikube's Docker daemon so the image is available to pods without a registry.

Point Docker at minikube's daemon:

```bash
eval $(minikube docker-env)
```

Build the image:

```bash
docker build -t llm-benchmark:latest benchmark/
```

Verify the image exists inside minikube:

```bash
docker images | grep llm-benchmark
```

---

## Step 8 — Set API keys (optional, for commercial model comparison)

Skip this if you only want Qwen results. Keys must use **these exact names** (they map to the benchmark pod env vars):

- `ANTHROPIC_API_KEY` — Anthropic (Claude)
- `OPENAI_API_KEY` — OpenAI
- `GOOGLE_API_KEY` — Google AI Studio (Gemini)
- `BENCHMARK_TOKEN` — optional; if set, `POST /run` requires `Authorization: Bearer <token>`

### Option A — `.env` file (recommended with `./deploy.sh`)

```bash
cp .env.example .env
# Edit .env with your keys (do not commit .env — it is gitignored)
./deploy.sh
```

If `.env` exists in the repo root when you run `./deploy.sh`, the script applies it to the `benchmark-api-keys` Secret and restarts `benchmark-api`.

If both `.env` and `.enc` exist, **`.env` wins**.

### Option A2 — Legacy `.enc` file (with `./deploy.sh`)

Use this if you prefer the older key names (`CLAUDE-API`, `OPENAI-KEY`, `GEMINI-KEY`, optional `BENCHMARK-TOKEN`). Copy the template and fill in values (file stays gitignored):

```bash
cp .enc.example .enc
# Edit .enc — do not commit it
./deploy.sh
```

The script runs `scripts/enc-to-k8s-envfile.py` to map those names into the Secret. **The previous `.enc` contents cannot be recovered from this repo** (they were not in git); recreate the file from your password manager or vendor dashboards, ideally with **new** keys if the old ones may have been exposed.

### Option B — `kubectl` only

```bash
kubectl delete secret benchmark-api-keys -n llm-stack --ignore-not-found
kubectl create secret generic benchmark-api-keys -n llm-stack \
  --from-literal=ANTHROPIC_API_KEY="<your-key>" \
  --from-literal=OPENAI_API_KEY="<your-key>" \
  --from-literal=GOOGLE_API_KEY="<your-key>" \
  --from-literal=BENCHMARK_TOKEN=""
kubectl rollout restart deployment/benchmark-api -n llm-stack
```

Without keys, the Secret in `05-benchmark.yaml` keeps empty placeholders and only Qwen runs.

---

## Step 9 — Deploy the Benchmark API

```bash
kubectl apply -f k8s-manifests/05-benchmark.yaml
```

Wait for the pod to be ready:

```bash
kubectl get pods -n llm-stack -w
```

Run the benchmark via the API directly:

```bash
kubectl port-forward svc/benchmark-api 8000:8000 -n llm-stack &
curl -sS -X POST http://127.0.0.1:8000/run \
     -H 'Content-Type: application/json' \
     -d '{}' | python3 -m json.tool
```

Or open the Chatbot URL and click the **Benchmark** tab.

---

## Verify everything is running

```bash
kubectl get pods -n llm-stack
kubectl get svc -n llm-stack
kubectl get pvc -n llm-stack
```

Expected pods (all `Running` or `Completed`):

```
NAME                             STATUS
ollama-...                       Running
prometheus-...                   Running
node-exporter-...                Running
kube-state-metrics-...           Running
grafana-...                      Running
chatbot-ui-...                   Running
benchmark-api-...                Running
pull-qwen-model-...              Completed
```

---

## Useful debug commands

```bash
# Follow logs for any component
kubectl logs -f deployment/ollama          -n llm-stack
kubectl logs -f deployment/chatbot-ui      -n llm-stack
kubectl logs -f deployment/grafana         -n llm-stack
kubectl logs -f deployment/benchmark-api   -n llm-stack
kubectl logs    job/pull-qwen-model        -n llm-stack

# Inspect a pod (events, image, resource limits)
kubectl describe pod <pod-name> -n llm-stack

# Restart a deployment after config change
kubectl rollout restart deployment/<name> -n llm-stack
```

---

## Swap models

To use a larger Qwen model, change the model name in two files:

1. `k8s-manifests/02-ollama.yaml` — the pull job line: `"name": "qwen2.5:0.5b"`
2. `k8s-manifests/04-chatbot-ui.yaml` — the JavaScript line: `model: 'qwen2.5:0.5b'`

Options:

| Model | Download size | Min RAM |
|-------|--------------|---------|
| `qwen2.5:0.5b` | ~300 MB | 2 GB |
| `qwen2.5:1.5b` | ~1 GB | 3 GB |
| `qwen2.5:7b` | ~5 GB | 8 GB |
| `qwen2.5:14b` | ~9 GB | 16 GB |

---

## Comparative analysis: open source vs commercial

The 10 benchmark prompts:

| # | Prompt | Category |
|---|--------|----------|
| 1 | Which model are you? | Self-identification |
| 2 | What's the latest information you have? | Knowledge cutoff |
| 3 | Explain Kubernetes in three sentences | Technical summary |
| 4 | Python function to reverse a linked list | Code generation |
| 5 | Pros and cons of microservices | Structured analysis |
| 6 | Translate "good morning" to Japanese, Korean, Hindi | Multilingual |
| 7 | Debug: `for i in range(10): print(i` | Error detection |
| 8 | What causes CrashLoopBackOff? | Domain knowledge |
| 9 | SQL: find duplicate emails in `users` | SQL generation |
| 10 | REST vs GraphQL in a table | Comparative reasoning |

**Qwen 2.5 (self-hosted, CPU) vs Claude Sonnet:**

| # | Qwen speed | Claude speed | Qwen quality | Claude quality | Qwen cost | Claude cost |
|---|------------|--------------|--------------|----------------|-----------|-------------|
| 1 | 0.8s | 1.2s | 8/10 | 10/10 | $0 | ~$0.003 |
| 2 | 0.6s | 1.0s | 5/10 | 9/10 | $0 | ~$0.003 |
| 3 | 1.1s | 1.4s | 6/10 | 9/10 | $0 | ~$0.004 |
| 4 | 2.3s | 2.1s | 4/10 | 9/10 | $0 | ~$0.008 |
| 5 | 1.8s | 2.0s | 5/10 | 9/10 | $0 | ~$0.006 |
| 6 | 0.9s | 1.1s | 7/10 | 10/10 | $0 | ~$0.003 |
| 7 | 1.0s | 0.9s | 6/10 | 10/10 | $0 | ~$0.003 |
| 8 | 1.5s | 1.8s | 6/10 | 10/10 | $0 | ~$0.005 |
| 9 | 1.2s | 1.3s | 7/10 | 9/10 | $0 | ~$0.004 |
| 10 | 2.0s | 2.2s | 5/10 | 9/10 | $0 | ~$0.006 |

**Averages:** Qwen ~1.32s · 5.9/10 quality · $0/query | Claude ~1.50s · 9.4/10 · ~$0.0045/query

Self-hosting wins on marginal cost and data control. Commercial APIs win on consistent quality.

Full write-up: [blog-post.md](blog-post.md)

---

## Cleanup

```bash
kubectl delete namespace llm-stack
minikube stop
```

---

## Built by

Pratik Mahalle — [LinkedIn](https://linkedin.com/in/mahalle-pratik) · [Twitter](https://twitter.com/pratikstwts) · [YouTube](https://youtube.com/@thedevopsduo)
