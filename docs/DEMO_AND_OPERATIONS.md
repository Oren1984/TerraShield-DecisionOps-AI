# Demo and operations

## Requirements

- Docker with Compose v2 (tested with Docker 29.9 / Docker Desktop on Windows 11).
- About 2 GB of disk for images. Local mode additionally needs about 2.3 GB for models.
- Initial image, package and model downloads need internet. After that, Mock mode and Local mode run without external services.

## Start with Docker

Bash:

```bash
cp .env.example .env          # or let the bootstrap script create it
bash scripts/bootstrap.sh     # creates .env with a random DB password if missing, builds, starts, waits
```

PowerShell:

```powershell
./scripts/bootstrap.ps1
```

Manual equivalent: copy `.env.example` to `.env`, set `POSTGRES_PASSWORD` to a long random value, then `docker compose up --build -d`.

Startup order: `db` (healthy) → `migrate` (Alembic upgrade, reference data, corpus, demo runs) → `api` and `worker` → `web`. A clean start took about 90 seconds on the test machine, including image builds.

| What | Where |
| --- | --- |
| Application | http://127.0.0.1:8080 (change with `TS_WEB_PORT`; bound to loopback by `TS_BIND_ADDRESS`) |
| Liveness / readiness | `/health/live`, `/health/ready` (database, migration head, pgvector, district) |
| API documentation | `/api/docs` |
| Health check script | `bash scripts/health.sh` or `./scripts/health.ps1` |

Port 8080 was free on the test machine; if it is taken, the bootstrap script stops and asks you to set `TS_WEB_PORT`.

## First use

1. Open the application. On first visit choose **Set up the owner account** and create the owner (12+ characters, not containing the username). There is no default password. Alternatively: `docker compose exec api python -m app.cli create-owner --username <name>` (reads `TS_OWNER_PASSWORD` or prompts).
2. The owner's workspace, *Harbor Vale Operations*, starts with the four seeded scenarios.
3. **Users** (owner only) adds operators and assigns workspaces. **Operations** shows queue, run counts, measured latency, validation rejections and retries.
4. **Explore the demo (read-only)** opens the demo workspace with seeded runs, without an account.

## Seeded journeys

| Scenario | Seed | What to look for |
| --- | --- | --- |
| Coastal storm | 1101 | Budget forces priorities; the plan protects the substation and powers the water works; Harbor Flats assets still flood |
| Wind and saturated ground | 2202 | West Descent closes for ground risk; mast securing is too late for the wind window; the coordinator requests one revision and the air specialist substitutes the mobile communications unit |
| Cascading infrastructure failure | 3303 | Substation flooding cuts power to both pump stations and the water works; protecting the substation early prevents the cascade |
| Conflict and insufficient resources | 4404 | Ground restrictions narrow the only route; with one crew, no barriers and a small budget the run ends **blocked**. Raise crews to 3, barrier sets to 2 and budget to 40 000 in scenario setup, save, and run again to get a verified plan |

An owner journey: open a scenario → **Run · Mock** → watch the trace and agent cards → **Plan comparison** → **Owner review** (reason required) → **Run approved plan in the simulator** → **Completion** → **Download report**. Editing the scenario after approval blocks the run and records the invalidated approval.

## Mock and Local modes

Mock is the default and needs nothing else. Local mode uses Ollama.

Host Ollama (default, `TS_OLLAMA_BASE_URL=http://host.docker.internal:11434`):

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
docker compose exec api python -m app.cli embed       # explicit, versioned re-embedding job
docker compose exec api python -m app.cli readiness
```

Ollama in Compose instead: set `TS_OLLAMA_BASE_URL=http://ollama:11434` in `.env`, then

```bash
docker compose --profile local-model up -d
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull nomic-embed-text
docker compose up -d api worker
docker compose exec api python -m app.cli embed
```

The workspace shows **Model readiness**; **Run · Local** is enabled only when the chat model and embedding model are present and the corpus is embedded. If the model is missing, slow or returns invalid output, the run fails with a recorded code (`model_missing`, `model_timeout`, `agent_output_invalid`, …); use **Retry with local model** or **Start a new Mock run**.

Measured on the test machine (Windows 11, 8 logical CPUs, 16 GB RAM, no GPU, Ollama 0.40.6, `qwen2.5:3b`): one complete local analysis with seven validated model calls took **781 s** (single calls 75–180 s). A GPU or a smaller context will change this; these figures are not hardware-independent.

## Backup and restore

```bash
bash scripts/backup.sh                                  # writes backups/terrashield-<UTC>.dump (pg_dump custom format)
bash scripts/restore.sh backups/terrashield-<UTC>.dump  # stops api/worker/web, recreates DB, restores, verifies, restarts
```

PowerShell: `./scripts/backup.ps1`, `./scripts/restore.ps1 -File backups/...`. Restore checks that the `vector` extension exists and reports embedding and run counts. The round trip (backup → restore → identical run count, health ready) is part of the Docker CI job and was run on the test machine.

## Shutdown, cancellation, migrations

- Stop: `docker compose down` (keeps volumes). Remove all data: `docker compose down -v`.
- The worker stops on SIGTERM after its current step; an interrupted job's lease expires and another worker resumes it from a clean slate.
- Cancel a run from the UI or `POST /api/runs/{id}/cancel`. Queued, awaiting-review and approved runs cancel immediately; running analyses and simulations stop at the next step.
- Migration failure: `migrate` exits non-zero, so `api` and `worker` do not start. Each migration runs in a transaction; read `docker compose logs migrate`, fix, and run `docker compose up -d` again. Readiness reports a migration head mismatch.

## Local development

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload      # TS_DATABASE_URL must point at PostgreSQL+pgvector
uv run python -m app.worker
cd frontend && npm ci && npm run dev                               # proxies /api to 127.0.0.1:8000
```

Tests:

```bash
cd backend && TS_TEST_DATABASE_URL=... TS_MIGRATION_TEST_DATABASE_URL=... uv run pytest
cd backend && TS_RUN_OLLAMA_TESTS=1 TS_TEST_DATABASE_URL=... uv run pytest tests/test_ollama_live.py
cd frontend && npm test && npx playwright test                      # Playwright needs the stack running
```

Evaluation:

```bash
docker compose exec api python -m app.cli evaluate-planning --per-scenario 5
docker compose exec api python -m app.cli evaluate-retrieval --model mock
docker compose exec api python -m app.cli evaluate-retrieval --model local
```

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `web` never starts | `docker compose logs migrate api`; readiness at `/health/ready` |
| "Port … is already in use" | Set `TS_WEB_PORT` in `.env` |
| Local mode button disabled | Model readiness panel: Ollama reachable, both models present, chunks embedded |
| Local run fails with `model_timeout` | CPU inference is slow; raise `TS_AGENT_TIMEOUT_S` or use a GPU |
| Corporate TLS interception on the host | Host-side `uv`/`pip` may need system certificates (`uv sync --system-certs`); container builds were unaffected on the test machine |
| Sign-in fails repeatedly | Five failures lock the account for five minutes |
