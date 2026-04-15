#!/usr/bin/env python3
"""
Embed benchmark/benchmark.html into k8s-manifests/04-chatbot-ui.yaml under the chatbot-ui ConfigMap.
Run after editing benchmark/benchmark.html, then kubectl apply -f k8s-manifests/04-chatbot-ui.yaml
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
html_path = ROOT / "benchmark" / "benchmark.html"
yaml_path = ROOT / "k8s-manifests" / "04-chatbot-ui.yaml"

html = html_path.read_text(encoding="utf-8")
benchmark_block = "  benchmark.html: |\n" + "\n".join("    " + line for line in html.splitlines())

text = yaml_path.read_text(encoding="utf-8")
start = text.find("  benchmark.html: |")
end_nginx = text.find("\n  nginx.conf:")
if start == -1 or end_nginx == -1:
    raise SystemExit("Could not find benchmark.html or nginx.conf anchor in 04-chatbot-ui.yaml")

new_text = text[:start] + benchmark_block + text[end_nginx:]
yaml_path.write_text(new_text, encoding="utf-8")
print(f"Synced {html_path} -> {yaml_path} (benchmark.html key)")
