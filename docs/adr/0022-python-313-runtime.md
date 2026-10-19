# ADR 0022: Python 3.13 as the single runtime

## Status
Accepted (2026-10-19, human-approved in the M0–M2 plan approval)

## Context
The repository had drifted into a three-way split:
- `pyproject.toml`, `packages/core/pyproject.toml`, `apps/api/Dockerfile`,
  `apps/worker/Dockerfile`, `AGENTS.md` and `docs/SPEC.md` said Python 3.12.
- `[tool.mypy] python_version = "3.13"` and the developer venv run 3.13.
- An uncommitted Slice-A fragment already moved CI (`PYTHON_VERSION`), the two
  fake-server Dockerfiles, `packages/core/requires-python` and `uv.lock` to 3.13.

A partial bump is worse than either pin: CI, local venv, type checker and images
can disagree, and `uv sync --frozen` can resolve a lockfile for a different
interpreter than the image. UNCERTAINTY U7 tracked this since 2026-10-18.

## Decision
Adopt **Python 3.13** everywhere, as one atomic change:
- `requires-python = ">=3.13"` in the root and `packages/core` projects.
- Ruff `target-version = "py313"`; mypy `python_version = "3.13"` (already).
- CI `PYTHON_VERSION: "3.13"` (already in the dirty tree).
- `apps/api` and `apps/worker` images on `python:3.13-slim`.
- Fake servers on `python:3.13-slim`.
- `AGENTS.md`, `docs/SPEC.md`, `docs/SHIPPING_GUIDE.md` updated to match.
- `uv lock` regenerated in the same commit.

## Consequences
- One interpreter; `uv sync --frozen`, CI, Docker and mypy agree.
- The developer venv (3.13.15) needs no rebuild.
- `packages/integrations/{meta,daraja}/fakes` are legacy, unreferenced by
  Compose or the workspace; their 3.12 pins are left untouched and tracked as
  dead code for a later cleanup (not part of the gate).
- Reverting means reverting this commit and the lockfile together.

## Alternatives rejected
- **Pin everything to 3.12**: requires rebuilding the venv and reverting the
  already-working CI/Docker/lock changes; no product benefit.
- **Leave the split**: fails the "definition of done" gate and makes the
  Docker image untrustworthy.
