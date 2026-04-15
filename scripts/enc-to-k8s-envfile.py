#!/usr/bin/env python3
"""
Read a local .enc file (KEY: value lines) and print KEY=value lines for kubectl --from-env-file.
Maps legacy names to Kubernetes env names expected by benchmark/app.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Left-hand side from .enc (case-insensitive) -> env var name for the Secret
ALIASES: dict[str, str] = {
    "CLAUDE-API": "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI-KEY": "OPENAI_API_KEY",
    "OPENAI_KEY": "OPENAI_API_KEY",
    "GEMINI-KEY": "GOOGLE_API_KEY",
    "GEMINI_KEY": "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY": "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY": "OPENAI_API_KEY",
    "GOOGLE_API_KEY": "GOOGLE_API_KEY",
    "BENCHMARK-TOKEN": "BENCHMARK_TOKEN",
    "BENCHMARK_TOKEN": "BENCHMARK_TOKEN",
}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not path or not path.is_file():
        print("Usage: enc-to-k8s-envfile.py <path-to-.enc>", file=sys.stderr)
        sys.exit(1)
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lhs, sep, rhs = line.partition(":")
        if not sep:
            continue
        name = lhs.strip().upper().replace(" ", "")
        env_name = ALIASES.get(name) or ALIASES.get(lhs.strip())
        if not env_name:
            continue
        val = rhs.strip()
        if val:
            out[env_name] = val
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "BENCHMARK_TOKEN"):
        print(f"{k}={out.get(k, '')}")


if __name__ == "__main__":
    main()
