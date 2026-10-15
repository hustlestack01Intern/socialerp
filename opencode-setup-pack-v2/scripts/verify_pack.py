#!/usr/bin/env python3
"""Self-check for the OpenCode setup pack. Exit code 0 = pack is consistent."""
import glob
import os
import pathlib
import re
import sys

try:
    import yaml
except Exception:
    yaml = None
root = pathlib.Path(pathlib.Path(pathlib.Path(__file__).resolve()).parent).parent
errors = []


def need(path):
    if not pathlib.Path(os.path.join(root, path)).exists():
        errors.append(f"missing file: {path}")


def read(path):
    return pathlib.Path(os.path.join(root, path)).open(encoding="utf-8").read()


for p in ["AGENTS.md", "README.md", "docs/SPEC.md", "docs/CONSTITUTION.md", "docs/STATE.md", "docs/MAP.md",
          "docs/UNCERTAINTY.md", "docs/LEARNINGS.md", "docs/GLOSSARY.md", "docs/ROADMAP_90_DAYS.md", "docs/KICKOFF.md"]:
    need(p)
agents = ["chief", "architect", "invariants", "geometer", "decision-scientist", "cadence-analyst",
          "incentives", "taxonomist", "skeptic", "scribe"]
for a in agents: need(f".opencode/agents/{a}.md")
for c in ["plan", "derive", "review", "evolve", "explain", "retro", "mapsync", "phase"]: need(f".opencode/commands/{c}.md")
if errors:
    sys.exit(1)

agents_md = read("AGENTS.md")
if len(agents_md.encode()) > 9000: errors.append("AGENTS.md too large (>9 KB); move detail to docs/SPEC.md")
for ref in ["docs/CONSTITUTION.md", "docs/SPEC.md", "docs/STATE.md", "docs/MAP.md", "docs/UNCERTAINTY.md"]:
    if ref not in agents_md: errors.append(f"AGENTS.md does not reference {ref}")
spec = read("docs/SPEC.md")
for ph in ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "GATE G1"]:
    if ph not in agents_md: errors.append(f"AGENTS.md missing phase {ph}")
    if ph not in spec: errors.append(f"SPEC.md missing phase {ph}")
for bad in ["concurrent conversations per second", "Phase 10", "TODO", "FIXME", "Next.js PWA", "/apps/dashboard"]:
    if bad in spec or bad in agents_md: errors.append(f"stale or unfinished text found: {bad}")
if "ALWAYS human-approved" not in spec or "never does money" not in agents_md.lower().replace("the llm never does money or stock arithmetic", "never does money"):
    errors.append("core safety rules missing")

for f in glob.glob(os.path.join(root, ".opencode", "**", "*.md"), recursive=True):
    parts = pathlib.Path(f).open(encoding="utf-8").read().split("---")
    if len(parts) < 3: errors.append(f"bad frontmatter: {f}"); continue
    if yaml:
        try:
            meta = yaml.safe_load(parts[1])
        except Exception as e:
            errors.append(f"yaml error in {f}: {e}"); continue
        if "description" not in meta: errors.append(f"no description: {f}")
        if "/agents/" in f.replace("\\", "/") and meta.get("mode") not in ("primary", "subagent", "all"):
            errors.append(f"bad mode: {f}")
        if "/commands/" in f.replace("\\", "/") and meta.get("agent") != "chief":
            errors.append(f"command not routed to chief: {f}")
    "/agents/" in f.replace("\\", "/") and "docs/CONSTITUTION.md" not in parts[2] and not f.endswith("chief.md")

secret = re.compile(r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|consumer_secret\s*=\s*['\"][^'\"]+)")
for f in glob.glob(os.path.join(root, "**", "*"), recursive=True):
    if pathlib.Path(f).is_file() and f.endswith((".md", ".py", ".json")) and secret.search(pathlib.Path(f).open(encoding="utf-8", errors="ignore").read()):
        errors.append(f"possible secret in {f}")

if errors:
    sys.exit(1)
