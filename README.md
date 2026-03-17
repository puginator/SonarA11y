# SonarA11y v1 (Gradient-First)

SonarA11y is a hackathon-ready accessibility remediation pipeline with two services:

- `phase1-scanner` (Node.js): scans a public web page with Playwright + Axe.
- `phase2-gradient-core` (Python): routes findings through Gradient-hosted models with LangGraph and emits remediation reports.
- `frontend-dashboard` (Nginx static app): interactive UI for web scans, PDF jobs, and remediation review.

## Architecture

- All model inference is DigitalOcean Gradient only (`provider="digitalocean-gradient"`).
- No alternate model providers or mocked inference in runtime paths.
- Phase 2 fails hard on startup without required Gradient env vars.
- ADK tracing decorators are applied to routing and agent nodes.
- Recommended inference endpoint: `https://inference.do-ai.run/v1`.
- Web remediation throughput is controlled by `WEB_PARALLELISM`, `WEB_NODE_TIMEOUT_SECONDS`, and `MAX_WEB_NODES`.
- Scanner defaults to `wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22a,wcag22aa` (override with `AXE_TAGS`).
- Phase 2 deduplicates equivalent web findings per request and reuses remediations to reduce token/time cost.
- Phase 2 trims noisy HTML (for example large inline SVG/script/style blocks) before model calls to improve latency.
- Phase 2 can persist successful remediations in a local SQLite cache and reuse them across repeated scans.

## Repository Layout

- `contracts/` shared JSON schemas
- `phase1-scanner/` Node HTTP scanner
- `phase2-gradient-core/` Python orchestrator and PDF job service
- `frontend-dashboard/` browser dashboard (served on port 3000)
- `scripts/smoke.sh` basic end-to-end smoke script

## Contracts

- `contracts/axe-violation-payload.schema.json`
- `contracts/pdf-violation-payload.schema.json`
- `contracts/fix-report.schema.json`

## Quick Start (Docker)

## Public Setup Notes

This repo is public-run friendly, but you must provide your own DigitalOcean Gradient credentials.

- You need:
  - Docker and Docker Compose
  - A valid `GRADIENT_API_KEY`
  - Access to the Gradient model IDs you place in `.env`
- You do not need my personal Gradient agent to run this project.
- The default configuration uses the direct Gradient inference endpoint:
  - `GRADIENT_BASE_URL=https://inference.do-ai.run/v1`
- If you want to use your own custom Gradient agent endpoint instead, replace `GRADIENT_BASE_URL` with your own `agents.do-ai.run` URL and supply model access that matches your account setup.
- If any secret was previously shared publicly, rotate it before publishing or forking this repo.

1. Copy environment template and provide Gradient credentials:

```bash
cp .env.example .env
```

2. Start services:

```bash
docker compose up --build
```

3. Verify health:

```bash
curl http://localhost:4001/health
curl http://localhost:8000/health
curl http://localhost:3000
```

4. Open dashboard:

```bash
open http://localhost:3000
```

## API

### Phase 1 Scanner

- `POST /scan`
- `GET /health`

Request:

```json
{
  "url": "https://example.com",
  "viewport": { "width": 1920, "height": 1080 }
}
```

### Phase 2 Gradient Core

- `POST /process`
- `POST /scan-and-process`
- `POST /web/jobs`
- `GET /web/jobs/{job_id}`
- `GET /web/jobs/{job_id}/report?format=json|html|pdf`
- `GET /cache/stats`
- `POST /pdf/jobs`
- `GET /pdf/jobs/{job_id}`
- `GET /pdf/jobs/{job_id}/report?format=json|html|pdf`
- `GET /health`

### Frontend Dashboard

- Runs at `http://localhost:3000`
- Calls backend via `/api/*` proxy
- Supports:
  - URL scan + remediation jobs (`/api/web/jobs`)
  - PDF URL/upload jobs (`/api/pdf/jobs`)
  - HTML/PDF report links after job completion

`POST /process` accepts raw `AxeViolationPayload` JSON.

`POST /scan-and-process` request:

```json
{
  "url": "https://example.com",
  "viewport": { "width": 1920, "height": 1080 }
}
```

`POST /pdf/jobs` accepts either:

- query param: `pdf_url=https://.../file.pdf`
- multipart upload: `file=@document.pdf`

## Local Test Commands

Phase 1:

```bash
cd phase1-scanner
npm install
npm test
```

Phase 2:

```bash
cd phase2-gradient-core
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONPATH=. pytest -q
```

## Performance Tuning (Web)

If web scans are slow (>3-4 minutes on small pages), check:

- Use `GRADIENT_BASE_URL=https://inference.do-ai.run/v1` for direct model inference.
- Increase `WEB_PARALLELISM` (start at `6`).
- Reduce `WEB_NODE_TIMEOUT_SECONDS` (start at `40`).
- Keep `MAX_WEB_NODES` bounded (`20-40`) for demo runs.
- Leave `REMEDIATION_CACHE_ENABLED=true` and mount `/data` in Docker to keep successful remediations warm between runs.

Example:

```bash
WEB_PARALLELISM=6 WEB_NODE_TIMEOUT_SECONDS=40 MAX_WEB_NODES=30 docker compose up --build
```

Cache stats:

```bash
curl http://localhost:8000/cache/stats
```

## Demo Script

Run a smoke test once services are up:

```bash
./scripts/smoke.sh
```

This script executes one web scan/remediation and one PDF job, then prints summarized report fields.
