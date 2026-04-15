"""
Multi-provider LLM benchmark: Ollama (Qwen) vs optional Claude, OpenAI, Gemini.
Costs use approximate public list $/1M token rates; verify against vendor pricing.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://ollama.llm-stack.svc.cluster.local:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:0.5b")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
BENCHMARK_TOKEN = os.environ.get("BENCHMARK_TOKEN", "").strip()

# Approximate USD per 1M tokens (input / output) — for rough comparison only.
RATE_CARD: dict[str, tuple[float, float]] = {
    "claude": (3.0, 15.0),
    "openai": (2.5, 10.0),
    "gemini": (0.10, 0.40),
}

PROMPTS: list[dict[str, str]] = [
    {"id": "1", "category": "Self-identification", "prompt": "Which model are you?"},
    {"id": "2", "category": "Knowledge cutoff", "prompt": "What's the latest information you have?"},
    {"id": "3", "category": "Technical summary", "prompt": "Explain Kubernetes in 3 sentences"},
    {"id": "4", "category": "Code generation", "prompt": "Write a Python function to reverse a linked list"},
    {"id": "5", "category": "Structured analysis", "prompt": "Summarize the pros and cons of microservices"},
    {"id": "6", "category": "Multilingual", "prompt": "Translate 'good morning' to Japanese, Korean, and Hindi"},
    {"id": "7", "category": "Error detection", "prompt": "Debug this: `for i in range(10): print(i`"},
    {"id": "8", "category": "Domain knowledge", "prompt": "What causes a Kubernetes pod to enter CrashLoopBackOff?"},
    {"id": "9", "category": "SQL generation", "prompt": "Write a SQL query to find duplicate emails in a users table"},
    {"id": "10", "category": "Comparative reasoning", "prompt": "Compare REST and GraphQL in a table format"},
]

app = FastAPI(title="LLM Benchmark API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth_ok(request: Request) -> bool:
    if not BENCHMARK_TOKEN:
        return True
    auth = request.headers.get("authorization", "")
    return auth == f"Bearer {BENCHMARK_TOKEN}"


def _est_cost(provider: str, in_tok: int, out_tok: int) -> float | None:
    rates = RATE_CARD.get(provider)
    if not rates:
        return None
    inp, out = rates
    return (in_tok / 1_000_000) * inp + (out_tok / 1_000_000) * out


async def _ollama_chat(client: httpx.AsyncClient, prompt: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = await client.post(
        f"{OLLAMA_BASE}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=180.0,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    r.raise_for_status()
    data = r.json()
    msg = data.get("message") or {}
    text = msg.get("content", "") or ""
    return {
        "latency_ms": elapsed_ms,
        "chars_out": len(text),
        "cost_usd_est": 0.0,
        "error": None,
    }


async def _claude_chat(client: httpx.AsyncClient, api_key: str, prompt: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = await client.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180.0,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code >= 400:
        return {"latency_ms": elapsed_ms, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    usage = data.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    return {
        "latency_ms": elapsed_ms,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd_est": round(_est_cost("claude", in_tok, out_tok) or 0, 6),
        "error": None,
    }


async def _openai_chat(client: httpx.AsyncClient, api_key: str, prompt: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = await client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
        json={
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=180.0,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code >= 400:
        return {"latency_ms": elapsed_ms, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    usage = data.get("usage") or {}
    in_tok = int(usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or 0)
    return {
        "latency_ms": elapsed_ms,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd_est": round(_est_cost("openai", in_tok, out_tok) or 0, 6),
        "error": None,
    }


async def _gemini_chat(client: httpx.AsyncClient, api_key: str, prompt: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    r = await client.post(
        url,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=180.0,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if r.status_code >= 400:
        return {"latency_ms": elapsed_ms, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    data = r.json()
    meta = data.get("usageMetadata") or {}
    in_tok = int(meta.get("promptTokenCount") or 0)
    out_tok = int(meta.get("candidatesTokenCount") or 0)
    return {
        "latency_ms": elapsed_ms,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd_est": round(_est_cost("gemini", in_tok, out_tok) or 0, 6),
        "error": None,
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/prompts")
async def prompts() -> dict[str, Any]:
    return {"prompts": PROMPTS, "ollama_model": OLLAMA_MODEL, "claude_model": CLAUDE_MODEL, "openai_model": OPENAI_MODEL, "gemini_model": GEMINI_MODEL}


@app.post("/run")
async def run_benchmark(request: Request) -> dict[str, Any]:
    if not _auth_ok(request):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization: Bearer <BENCHMARK_TOKEN>")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    google_key = os.environ.get("GOOGLE_API_KEY", "").strip()

    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for p in PROMPTS:
            prompt = p["prompt"]
            entry: dict[str, Any] = {
                "id": p["id"],
                "category": p["category"],
                "prompt": prompt,
                "ollama": None,
                "claude": None,
                "openai": None,
                "gemini": None,
            }
            try:
                entry["ollama"] = await _ollama_chat(client, prompt)
            except Exception as e:
                entry["ollama"] = {"latency_ms": None, "error": str(e)[:500]}

            if anthropic_key:
                try:
                    entry["claude"] = await _claude_chat(client, anthropic_key, prompt)
                except Exception as e:
                    entry["claude"] = {"latency_ms": None, "error": str(e)[:500]}
            else:
                entry["claude"] = {"skipped": True, "reason": "ANTHROPIC_API_KEY not set"}

            if openai_key:
                try:
                    entry["openai"] = await _openai_chat(client, openai_key, prompt)
                except Exception as e:
                    entry["openai"] = {"latency_ms": None, "error": str(e)[:500]}
            else:
                entry["openai"] = {"skipped": True, "reason": "OPENAI_API_KEY not set"}

            if google_key:
                try:
                    entry["gemini"] = await _gemini_chat(client, google_key, prompt)
                except Exception as e:
                    entry["gemini"] = {"latency_ms": None, "error": str(e)[:500]}
            else:
                entry["gemini"] = {"skipped": True, "reason": "GOOGLE_API_KEY not set"}

            rows.append(entry)

    def _avg_latencies(key: str) -> float | None:
        vals = []
        for row in rows:
            cell = row.get(key)
            if not cell or cell.get("skipped") or cell.get("error"):
                continue
            ms = cell.get("latency_ms")
            if ms is not None:
                vals.append(ms)
        if not vals:
            return None
        return round(sum(vals) / len(vals), 1)

    def _sum_cost(key: str) -> float:
        total = 0.0
        for row in rows:
            cell = row.get(key)
            if not cell or not isinstance(cell.get("cost_usd_est"), (int, float)):
                continue
            total += float(cell["cost_usd_est"])
        return round(total, 6)

    summary = {
        "ollama": {
            "avg_latency_ms": _avg_latencies("ollama"),
            "total_cost_usd_est": 0.0,
        },
        "claude": {"avg_latency_ms": _avg_latencies("claude"), "total_cost_usd_est": _sum_cost("claude")},
        "openai": {"avg_latency_ms": _avg_latencies("openai"), "total_cost_usd_est": _sum_cost("openai")},
        "gemini": {"avg_latency_ms": _avg_latencies("gemini"), "total_cost_usd_est": _sum_cost("gemini")},
    }

    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ollama_base_url": OLLAMA_BASE,
        "ollama_model": OLLAMA_MODEL,
        "claude_model": CLAUDE_MODEL,
        "openai_model": OPENAI_MODEL,
        "gemini_model": GEMINI_MODEL,
        "pricing_note": "API costs use static approximate $/1M token rates in the benchmark service; use vendor billing for real totals.",
        "rows": rows,
        "summary": summary,
    }
