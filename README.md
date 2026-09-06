# Excelsis 360 — Data Analyst Agent

**Ask your database a question in plain English, get a grounded, cited answer streamed back token-by-token.** Deployed in production across 100+ educational institutions, this agent turns SQL Server data into conversational insight — no BI dashboard required, no hallucinated numbers.

A full-stack AI data analyst built on a LangGraph ReAct agent (local LLMs via Ollama), a SQL Server backend, and a FastAPI + React web interface — with RAG-grounded answers, JWT-secured multi-user access, and a native Prometheus/Grafana observability stack.

---

## Features

- **Natural-language chat** — ask questions about your data in plain English; the agent reasons across multiple tools and streams the answer token-by-token
- **ReAct reasoning loop** — the agent decides which tools to call (metric stats, threshold alerts, ad-hoc SQL, trend analysis, anomaly detection) and in what order
- **RAG knowledge base** — ChromaDB vector store (`BAAI/bge-small-en-v1.5` embeddings via HuggingFace, auto-downloaded) indexes SQL schema metadata and policy documents; the agent uses `retrieve_schema` and `retrieve_policy` for accurate, grounded answers
- **Two-model setup** — `qwen2.5:14b` (default, configurable via `MODEL`) drives the ReAct reasoning loop via Ollama; `BAAI/bge-small-en-v1.5` (configurable via `EMBED_MODEL`) handles vector encoding via HuggingFace (no Ollama pull needed)
- **Prompt guardrails** — every chat message is validated before reaching the agent: length cap (2000 chars), token-budget check, and injection-pattern detection
- **SQL Server backend** — connects to one or more SQL Server databases; schema is fully configurable via env vars; the agent can run ad-hoc T-SQL SELECT queries alongside structured tools
- **Interactive web dashboard** — five Recharts-powered charts (metric by group, weekly trend, metric breakdown, day-of-week bar, period comparison) with Power BI-style cross-filtering and drill-down
- **Agent ↔ dashboard link** — asking the agent to show a chart or filter the data updates the dashboard live via SSE
- **Web UI** — React 18 + Tailwind interface with live streaming chat, KPI cards, threshold alerts table, sparkline trends, and user management
- **REST API** — FastAPI backend with JWT auth, SSE streaming, and rate limiting
- **Rate limiting** — chat endpoint capped at 10 requests/minute per user/IP (slowapi); Redis-backed (`REDIS_URI`) for accurate limits across multiple workers, falls back to in-memory for single-worker deployments
- **Persistent conversation history** — per-user chat history stored in a SQLite file (`CHAT_DB`) via LangGraph's `SqliteSaver` checkpointer; survives restarts and is shared across Uvicorn workers; optionally Postgres-backed (`CHECKPOINT_DB_URI`) to share history across multiple app replicas — see [Scaling to Multiple Replicas](#scaling-to-multiple-replicas)
- **Jupyter notebook** — full interactive analysis environment that shares the same `src/` backend
- **MCP server** — exposes Excelsis360 data tools to Claude Code via FastMCP
- **Observability** — Prometheus metrics (`agent_tool_invocations_total`, `agent_query_duration_seconds`, `agent_query_errors_total`, `cache_hits_total`, `cache_misses_total`) via `src/tracker.py`; Grafana dashboards provisioned from `tools/grafana-provisioning/`; `/metrics` scrape endpoint always active — see [Observability Stack](#observability-stack)

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | `qwen2.5:14b` via Ollama (`langchain-ollama`, configurable via `MODEL`) |
| Agent | LangGraph ReAct (`create_react_agent`) |
| Database | SQL Server via SQLAlchemy + `pyodbc` (ODBC Driver 18, `QueuePool`) |
| Backend | FastAPI + Uvicorn |
| Auth | JWT (python-jose) + bcrypt |
| Frontend | React 18 + Vite + Tailwind CSS |
| Charts | Recharts (bar, line, pie, area — five chart components) |
| Data wrangling | pandas + numpy |
| Vector DB | ChromaDB (persistent) |
| Embeddings | `BAAI/bge-small-en-v1.5` via HuggingFace (auto-downloaded) |
| Rate limiter | slowapi + Redis (`REDIS_URI`, optional) |
| Conversation history | LangGraph `SqliteSaver` (`chat.db`) |
| Metrics | `prometheus-client` + `prometheus-fastapi-instrumentator` |


---

## Project Structure

```
├── api/                  # FastAPI backend
│   ├── main.py           # App startup, CORS, static file mounts
│   ├── auth.py           # User registry (users.json), JWT, bcrypt
│   ├── deps.py           # FastAPI dependencies (get_current_user, etc.)
│   ├── models.py         # Pydantic request/response models
│   ├── users.json        # Persisted user accounts (auto-created)
│   └── routers/
│       ├── auth.py       # Login, user CRUD
│       ├── chat.py       # SSE streaming chat endpoint
│       └── data.py       # Data stats, at-risk, trends, sparklines
│
├── src/                  # Shared Python backend (used by API + notebook)
│   ├── security.py       # UserContext dataclass (user_id)
│   ├── sql_store.py      # SQLDataStore — primary data backend (SQL Server via SQLAlchemy + pyodbc, pooled)
│   ├── tools.py          # LangGraph tools (13 tools, all security-aware)
│   ├── prompt_guard.py   # Input validation: length cap, token budget, injection patterns
│   ├── rag_store.py      # ChromaDB collections for schema and policy vector search
│   ├── rag_ingestor.py   # Ingests PDFs/Markdown from docs/ + auto-indexes SQL schema
│   ├── agent.py          # ExcelsisAgent — LangGraph ReAct agent (qwen2.5:14b)
│   ├── mcp_server.py     # FastMCP server for Claude Code
│   ├── eval/             # RAG retrieval-quality eval harness + LLM-judge groundedness check
│   ├── observability/    # Per-run agent trace recorder (loop detection, step timing)
│   └── routing/          # Cost/latency-aware model tier router
│
├── docs/                 # Policy documents scanned by rag_ingestor.py (.pdf and .md)
│
├── web/                  # React frontend
│   └── src/
│       ├── pages/        # Login, Chat, Dashboard, Users
│       ├── components/   # Sidebar, ChatPanel, MessageBubble, DrilldownPanel,
│       │                 # FilterBar, Breadcrumb, ProtectedRoute, charts/
│       ├── hooks/        # useDashboardData, useChartSelection
│       ├── lib/          # useChat, suggestions
│       └── api/client.ts # Axios instance + SSE streaming helper
│
├── tools/                # Native (non-Docker) observability stack binaries — gitignored
│   │                     # except grafana-provisioning/; see Observability Stack below
│   ├── garnet/           # Microsoft Garnet — Redis-protocol-compatible cache/server
│   ├── prometheus/       # Prometheus binary + scrape config
│   ├── grafana/          # Grafana binary + compiled frontend
│   └── grafana-provisioning/  # Datasource + dashboard provisioning (tracked in git)
├── scripts/
│   ├── seed_test_db.py                 # Seed script — populates education_db (16 tables) and finance_db (18 tables)
│   ├── run_rag_eval.py                 # RAG retrieval-quality golden-set eval CLI
│   └── estimate_routing_savings.py     # Illustrative cost projection for tiered model routing
├── Excelsis.ipynb        # Interactive Jupyter notebook
├── start.sh              # Start both servers (backend :8000, frontend :5173)
├── requirements.lock     # Pinned Python dependencies (use this for installs)
├── .env.example          # Environment variable template
└── .env.test             # Pre-filled config for a local test SQL Server
```

---

## Quick Start

### 1. Install and start Ollama

Download Ollama from [ollama.com](https://ollama.com) and pull the required model:

```bash
ollama pull qwen2.5:14b
```

Ollama must be running on `http://localhost:11434` before starting the app. The embedding model (`BAAI/bge-small-en-v1.5` by default) is downloaded automatically from HuggingFace on first run.

### 2. Clone and configure

```bash
cp .env.example .env
```

Edit `.env` and fill in as needed:

```env
JWT_SECRET=change-me-in-production
ADMIN_PASSWORD=your-admin-password
```

### 3. Install Python dependencies

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r requirements.lock
```

> Ollama model weights are downloaded on first `ollama pull`. Subsequent starts are instant. `qwen2.5:14b` drives the ReAct agent. The embedding model is downloaded automatically from HuggingFace on first run — no separate pull needed.

### 4. Install frontend dependencies

```bash
cd web && npm install && cd ..
```

### 5. Start both servers

```bash
# macOS / Linux
bash start.sh

# Windows
pwsh start.ps1
```

This starts:
- **Ollama** must already be running (see step 1)
- **FastAPI** on `http://localhost:8000`
- **React** on `http://localhost:5173`

Redis, Prometheus, and Grafana are optional for local dev (the app falls back to in-memory rate limiting and works fine without metrics/dashboards) — see [Observability Stack](#observability-stack) to start them.

Open **http://localhost:5173** in your browser. You'll be redirected to the login page.

Default credentials: `admin` / the value of `ADMIN_PASSWORD` in your `.env` (defaults to `admin123` if not set).

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `MODEL` | No | `qwen2.5:14b` | Ollama model for the ReAct agent |
| `SQL_SERVER` | Yes | — | SQL Server hostname or IP (use `.` for local default instance) |
| `SQL_DATABASES` | Yes | — | Comma-separated list of databases to expose |
| `SQL_PRIMARY_DB` | No | first in list | Default database for queries |
| `SQL_DRIVER` | No | `{ODBC Driver 18 for SQL Server}` | ODBC driver string |
| `SQL_AUTH_METHOD` | No | `sql` | `sql` (username/password) or `windows` (Windows integrated auth) |
| `SQL_USERNAME` | SQL auth only | — | SQL Server login username |
| `SQL_PASSWORD` | SQL auth only | — | SQL Server login password |
| `SQL_POOL_SIZE` | No | `5` | SQLAlchemy `QueuePool` base connection count per database |
| `SQL_QUERY_TIMEOUT` | No | `30` | Per-query connection timeout in seconds |
| `AT_RISK_THRESHOLD` | No | `75.0` | Default metric % threshold for below-threshold flagging |
| `JWT_SECRET` | Yes (prod) | `change-me-in-production` | Secret key for JWT signing |
| `ADMIN_PASSWORD` | No | `admin123` | Password for the default admin account |
| `PRIMARY_TABLE` | No | `attendance` | Primary SQL table the agent queries |
| `METRIC_COLUMN` | No | `status` | Column holding the measured metric |
| `POSITIVE_VALUE` | No | `active` | Value counted as a positive outcome |
| `DATE_COLUMN` | No | `date` | Date column for time-based queries |
| `ENTITY_COLUMN` | No | `entity_id` | Primary entity key column |
| `ENTITY_NAME_COLUMN` | No | `entity_name` | Human-readable entity name column |
| `GROUP_COLUMNS` | No | `` | Comma-separated grouping columns |
| `CHROMA_PATH` | No | `.chroma` | Persistent ChromaDB directory path |
| `EMBED_MODEL` | No | `BAAI/bge-small-en-v1.5` | HuggingFace embedding model for RAG (auto-downloaded) |
| `CHAT_DB` | No | `./chat.db` | SQLite file for persistent per-user conversation history |
| `REDIS_URI` | No | `` | Redis connection URI for shared rate-limit counters across workers and shared agent-trace history (e.g. `redis://localhost:6379`) |
| `DOCS_PATH` | No | `docs` | Directory scanned for policy documents |
| `MAX_MESSAGE_LEN` | No | `2000` | Maximum characters allowed in a single chat message |
| `MAX_PROMPT_TOKENS` | No | `2048` | Maximum estimated tokens (message + history) before rejection |
| `RAG_CACHE_TTL` | No | `3600` | TTL in seconds for RAG query result cache |
| `CHECKPOINT_DB_URI` | No | `` | Postgres connection string for shared conversation-history storage across replicas (e.g. `postgresql://user:pass@host/db`); unset uses the local `CHAT_DB` SQLite file |
| `CHROMA_SERVER_HOST` | No | `` | Hostname of a shared `chroma run` server; unset uses the local `CHROMA_PATH` persistent directory |
| `CHROMA_SERVER_PORT` | No | `8000` | Port for `CHROMA_SERVER_HOST` |
| `CHROMA_SERVER_SSL` | No | `false` | Set `true` if the Chroma server requires SSL |
| `SQL_LOCK_TIMEOUT_MS` | No | `5000` | `SET LOCK_TIMEOUT` applied to every new SQL Server connection, so a misconfigured or overly-privileged login can't hang the pool waiting on a lock |
| `SQL_IDENTIFIER_GUARD` | No | `true` | Set `false` to disable validating `run_sql_query`'s table/column references against the live schema catalog before execution |
| `SQL_IDENTIFIER_CATALOG_TTL` | No | `3600` | TTL in seconds for the cached schema catalog used by the identifier guard |
| `AUDIT_DB` | No | `./audit.db` | SQLite file for the admin action audit trail (user create/delete) |
| `APP_ENV` | No | `development` | Set to `production` to enforce stricter startup checks: `JWT_SECRET` length, non-default `ADMIN_PASSWORD`, no wildcard `ALLOWED_ORIGINS` |
| `RETENTION_DAYS` | No | `0` (disabled) | Purge conversation history for threads inactive longer than this many days |
| `RETENTION_PURGE_INTERVAL_HOURS` | No | `24` | How often the retention purge job runs when `RETENTION_DAYS` is set |

---

## Local Test Database

This project runs against a native SQL Server instance — no Docker is required or supported (some hosts, e.g. EC2 instances without nested-virtualization/hypervisor access, cannot run Docker Desktop at all). `scripts/seed_test_db.py` populates two databases (`education_db`, `finance_db`) against any reachable SQL Server, local or remote.

**Prerequisites:** ODBC Driver 18 for SQL Server must be installed, and a SQL Server instance must be reachable (local install, LAN host, or cloud instance).

```bash
# Install the seeding dependency
pip install -e ".[dev]"

# Windows integrated auth, local default instance — no password needed
python scripts/seed_test_db.py --scale medium --auth-method windows --server .

# SQL auth, any host (local, LAN, or remote)
python scripts/seed_test_db.py --scale medium --server myhost --username sa --password yourpassword
```

Use `--server .\SQLEXPRESS` for SQL Server Express, or `--server myhost` / `--server myhost,1433` for a remote instance.

Then update `.env` (or `cp .env.test .env` for a pre-filled starting point):

```env
SQL_SERVER=.
SQL_AUTH_METHOD=windows
SQL_DATABASES=education_db,finance_db
SQL_PRIMARY_DB=education_db
```

| Database | Tables | Purpose |
|---|---|---|
| `education_db` | 16 | Attendance, students, teachers, grades, subjects |
| `finance_db` | 18 | Transactions, invoices, purchase orders, budgets, expenses |

| Scale | Students | Attendance rows | Approx. time |
|---|---|---|---|
| `small` | 1 000 | 100 000 | ~10 s |
| `medium` | 5 000 | 500 000 | ~1–2 min |
| `large` | 20 000 | 2 000 000 | ~5–10 min |

---

## Observability Stack

Redis (rate-limit counters), Prometheus (metrics), and Grafana (dashboards) run as **native binaries**, not containers. This works identically on Windows and Linux hosts, including EC2 instances without hypervisor access for Docker Desktop.

| Service | Binary | Default port | Notes |
|---|---|---|---|
| Redis-compatible cache | [Microsoft Garnet](https://github.com/microsoft/garnet) (`tools/garnet/`) | `6379` | Drop-in Redis-protocol server. Must be started with `--lua` — the rate limiter (`limits` library) requires `EVALSHA`/Lua scripting, which is off by default. |
| Metrics | [Prometheus](https://prometheus.io) (`tools/prometheus/`) | `9090` | Scrapes the FastAPI `/metrics` endpoint on `localhost:8000`. |
| Dashboards | [Grafana](https://grafana.com) (`tools/grafana/`) | `3001` | Datasources/dashboards provisioned from `tools/grafana-provisioning/` (tracked in git — no secrets, credentials are env-var substituted at launch). |

None of `tools/garnet`, `tools/prometheus`, or `tools/grafana` are checked into git (they're large vendored binaries) — download them once per host:

```bash
# Garnet (Redis-compatible) — download the release matching your OS/arch from
# https://github.com/microsoft/garnet/releases and extract to tools/garnet/

# Prometheus — https://prometheus.io/download/, extract to tools/prometheus/

# Grafana — https://grafana.com/grafana/download (OSS, matching OS), extract to tools/grafana/
```

Start each (adjust paths for your OS):

```bash
# Garnet
tools/garnet/<target>/GarnetServer(.exe) --port 6379 --lua

# Prometheus
cd tools/prometheus && ./prometheus(.exe) --config.file=prometheus.yml

# Grafana — set EXCELSIS_HOME to the repo root (used by dashboard provisioning path)
# and the real SQL_SERVER/SQL_PRIMARY_DB/SQL_USERNAME/SQL_PASSWORD for the datasource
cd tools/grafana
EXCELSIS_HOME=/path/to/repo GF_PATHS_PROVISIONING=../grafana-provisioning \
GF_SERVER_HTTP_PORT=3001 GF_SECURITY_ADMIN_PASSWORD=admin \
./bin/grafana(.exe) server --homepath .
```

Then set `REDIS_URI=redis://localhost:6379` in `.env` so rate limiting is Redis-backed instead of falling back to in-memory (which doesn't share state across workers).

Grafana UI: `http://localhost:3001` (default login `admin`/`admin` unless `GF_SECURITY_ADMIN_PASSWORD` is set). Two dashboards are provisioned automatically: **API Health** and **Business Metrics**.

---

## Models

All models run locally via [Ollama](https://ollama.com). No API keys or internet access required at inference time.

### LLM — `qwen2.5:14b` (default, set via `MODEL`)

Drives the LangGraph ReAct loop via `ChatOllama`. Handles tool calling (data queries, at-risk identification, dashboard requests, knowledge-base lookups) and direct conversational replies in a single unified pipeline. Supports Ollama Cloud (`OLLAMA_BASE_URL` + `OLLAMA_API_KEY`) or a local Ollama instance.

### Embeddings — `BAAI/bge-small-en-v1.5` (default, set via `EMBED_MODEL`)

Encodes queries and documents for ChromaDB vector search. Loaded via `HuggingFaceEmbeddings` — downloaded automatically from HuggingFace on first run, no Ollama pull required.

---

## Web Interface

| Page | Path | Access |
|---|---|---|
| Login | `/login` | Public |
| Chat | `/chat` | All authenticated users |
| Dashboard | `/dashboard` | All authenticated users |
| Users | `/users` | Admin only |

### Chat
Type any question in natural language. The agent streams its response token-by-token, showing tool-use badges for each data source consulted (e.g. *Querying data…*, *SQL query*, *Detecting anomalies…*).

Example questions:
- *Which groups have the lowest metric rate this month?*
- *List all entities below 70% threshold*
- *How does this week compare to last month?*
- *Show me the trend — is it improving or declining?*

---

## REST API

The FastAPI backend is available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/auth/login` | None | Username + password → JWT |
| GET | `/auth/me` | Any | Current user info |
| GET | `/auth/users` | Admin | List all users |
| POST | `/auth/users` | Admin | Create user |
| DELETE | `/auth/users/{username}` | Admin | Delete user |
| POST | `/chat/stream` | Any | SSE streaming chat |
| GET | `/data/summary` | Any | Data overview |
| GET | `/data/alerts` | Any | Threshold alerts — entities below a metric % |
| GET | `/data/stats` | Any | Stats by group/period |
| GET | `/data/trends` | Any | Period comparison: last 30 days vs prior 30 days |
| GET | `/data/sparklines` | Any | Sparkline trend data |
| GET | `/observability/traces` | Admin | Recent agent run traces — per-tool timing, repeated-call/loop flags, error status |
| GET | `/observability/admin-audit` | Admin | Recent admin actions — who created/deleted which account, when, and from where |
| GET | `/health` | None | Liveness check |

---

## Reliability & Evaluation Tooling

Three additions on top of the existing Prometheus/Grafana stack, aimed at the parts of running an LLM agent in production that raw request metrics don't cover: is retrieval actually finding the right document, is a run looping instead of failing fast, and is every query paying for the same model tier regardless of how simple it is.

### RAG retrieval-quality eval (`src/eval/`)

A golden-set harness that measures retrieval quality — hit@k and mean reciprocal rank — against the policy vector store, independent of any LLM call, so it's fast and deterministic enough to run on every docs/schema change:

```bash
python scripts/run_rag_eval.py                        # runs src/eval/golden_sets/policy_golden_set.json
python scripts/run_rag_eval.py --json report.json      # write the full report
python scripts/run_rag_eval.py --fail-under 0.9        # non-zero exit if hit_rate drops below 90% — CI-friendly
python scripts/run_rag_eval.py --llm-judge             # add an LLM groundedness pass (requires Ollama)
```

`src/eval/judge.py` layers an optional LLM-as-judge groundedness check on top, for catching the case where retrieval succeeded but the generated answer still drifted from the retrieved context — the actual hallucination-in-production failure mode, not just a retrieval miss.

### Agent reliability trace (`src/observability/trace.py`)

`TracingQueryTracker` (wired into `ExcelsisAgent` as a drop-in replacement for the plain `QueryTracker`) records every tool step of a run — not just aggregate counts — so a repeated-tool-call loop can be flagged well before LangGraph's hard recursion limit kicks in, and per-step latency can be inspected instead of only end-to-end duration. Two new Prometheus metrics (`agent_repeated_tool_calls_total`, `agent_step_duration_seconds{tool=}`) feed the existing Grafana dashboards, and the last 200 runs are queryable directly:

```bash
curl -H "Authorization: Bearer $ADMIN_JWT" http://localhost:8000/observability/traces?limit=10
```

### Cost/latency-aware model router (`src/routing/model_router.py`)

A tiered router — cheap/fast local tier for direct lookups and greetings, a stronger tier for multi-step analytical questions — with a fast, explainable heuristic classifier (`classify_complexity`, no extra model call) and Prometheus tracking of tier selection, latency, and estimated cost. This targets the deployment shape the project already supports via `OLLAMA_BASE_URL`/`OLLAMA_API_KEY` (Ollama Cloud), where per-token cost becomes real — it's available as a tested, standalone component rather than force-wired into the current single-model setup:

```bash
python scripts/estimate_routing_savings.py   # illustrative cost projection over a sample query set
```

Run the new test suites with `pytest tests/test_rag_eval.py tests/test_trace.py tests/test_model_router.py` (no Ollama or SQL Server required).

---

## Scaling to Multiple Replicas

The default configuration (SQLite checkpointer, local ChromaDB directory, in-memory trace buffer, single Ollama instance) is correct for one instance and needs no configuration. Every option below is opt-in via an env var — leaving it unset preserves that exact single-instance behavior. Set them once several app replicas need to share state behind a load balancer:

| Bottleneck | Env var(s) | What changes |
|---|---|---|
| Conversation history tied to one instance's local `chat.db` | `CHECKPOINT_DB_URI` | Swaps the LangGraph checkpointer from `SqliteSaver`/`AsyncSqliteSaver` to `PostgresSaver`/`AsyncPostgresSaver` (`src/agent.py`, `api/main.py`), so any replica can resume any user's thread |
| RAG vector index built separately per replica | `CHROMA_SERVER_HOST`, `CHROMA_SERVER_PORT`, `CHROMA_SERVER_SSL` | Points `ExcelsisRAGStore` (`src/rag_store.py`) at one shared `chroma run` server via `chromadb.HttpClient` instead of each replica's own `.chroma` directory |
| Agent traces only visible from the replica that served the request | `REDIS_URI` | `TraceRecorder` (`src/observability/trace.py`) pushes traces to a capped Redis list in addition to its in-memory ring buffer, so `/observability/traces` sees runs from every replica |
| One Ollama process is a hard concurrency ceiling | none (code-level) | `ReplicaPool` (`src/routing/model_router.py`) round-robins a `ModelTier` across several `ReplicaHandle`s — construct one per Ollama endpoint and pass the pool as a tier's `replicas=` instead of `build=` |

Every path degrades gracefully: a Redis outage falls back to the in-memory trace buffer without raising, and Postgres/Chroma-server misconfiguration fails fast at startup rather than silently falling back (so a bad `CHECKPOINT_DB_URI` or `CHROMA_SERVER_HOST` is caught immediately instead of masking data loss).

---

## Security Hardening

Since the underlying data is often student records, the SQL execution path is defended in depth — each layer assumes the one before it could fail:

| Layer | What it catches |
|---|---|
| **SQL guard** (`_assert_select_only`, `src/sql_store.py`) | Rejects any non-SELECT statement via a full AST walk (not just the top-level statement), `SELECT ... INTO` (which creates a table), and a regex blocklist for dangerous T-SQL constructs (`xp_cmdshell`, `OPENROWSET`, `OPENQUERY`, `WAITFOR DELAY`, `BULK INSERT`, etc.) that could otherwise hide inside an on-its-face-valid SELECT |
| **Identifier guard** (`src/identifier_guard.py`) | Validates every table and column an LLM-generated query references against the real schema catalog before execution — rejects hallucinated identifiers (e.g. `students.gpa_unweighted`) instead of letting them hit SQL Server or silently match a similarly named column. Enabled by default (`SQL_IDENTIFIER_GUARD=false` to disable); fails open (skips validation) if schema introspection is unavailable, never blocking a request on its own failure |
| **Connection hardening** | Every SQL Server connection sets `LOCK_TIMEOUT` (`SQL_LOCK_TIMEOUT_MS`) so a misconfigured or overly-privileged login can't hang the pool. SQL Server has no session-level read-only mode — that guarantee must come from the DB login itself: **grant the app's SQL login `db_datareader` only, never `db_owner` or `sysadmin`**. `scripts/create_readonly_sql_login.sql` creates such a login (`excelsis_readonly`) — run it once as a DBA/sysadmin account, then point `SQL_USERNAME`/`SQL_PASSWORD` at it |
| **Admin audit trail** (`src/observability/audit.py`) | Every user create/delete records actor, action, target, client IP, and timestamp to a dedicated SQLite log (`AUDIT_DB`), readable via `GET /observability/admin-audit` — a FERPA-style "who touched the account plane" record |
| **Startup validation** (`APP_ENV=production`) | Fails boot rather than silently running insecurely: `JWT_SECRET` under 32 characters, `ADMIN_PASSWORD` still the default, or `ALLOWED_ORIGINS` set to `*` |
| **Retention purge** (`src/retention.py`, `RETENTION_DAYS`) | Disabled by default; when set, deletes checkpoint history for conversation threads inactive past the window — data-minimization for chat history that holds a copy of query results |

These were ported and adapted from the equivalent safeguards in a sibling project, then fitted to this app's SQLAlchemy + pyodbc + SQL Server stack (no ORM layer, so the audit trail and retention tracker use dedicated SQLite tables rather than ORM models).

### Self-owned local Postgres (`scripts/local_postgres.py`)

The SQL Server connection-hardening story above has one gap in practice: **we don't administer that server.** It belongs to the school's SIS, and the app's own SQL login has neither `sysadmin` nor `db_owner` — so `scripts/create_readonly_sql_login.sql` can be written and handed off, but not run by this project's own credentials.

`CHECKPOINT_DB_URI` doesn't have that constraint — conversation-checkpoint storage is infrastructure this project can own outright. `scripts/local_postgres.py` manages a self-contained, embedded PostgreSQL server (via the [`pgserver`](https://pypi.org/project/pgserver/) package) that requires no system install, Docker, or admin rights on the host — appropriate for an EC2 instance without hypervisor access, same constraint as the rest of this project's [Observability Stack](#observability-stack). On first `start` it creates a dedicated `excelsis_app` role and `excelsis_checkpoints` database, scoped so that role can reach *only* that one database (Postgres grants `CONNECT` on every database to `PUBLIC` by default — this is explicitly revoked for `postgres`/`template1`), authenticated with a real `scram-sha-256` password rather than trust. This database holds only the app's own LangGraph checkpoints — never a copy of the school's SIS data.

`pgserver` has no wheels for the project's main Python version, so it runs from its own dedicated venv:

```bash
python -m venv .venv-pg
.venv-pg/Scripts/python.exe -m pip install pgserver psycopg2-binary   # Windows
.venv-pg/Scripts/python.exe scripts/local_postgres.py start           # idempotent; safe to re-run
.venv-pg/Scripts/python.exe scripts/local_postgres.py status
.venv-pg/Scripts/python.exe scripts/local_postgres.py stop
```

`start` prints the `excelsis_app` password once (rotated on every call) and writes the resulting `CHECKPOINT_DB_URI` into `.env` automatically — including after a restart, since the underlying server picks a fresh TCP port on Windows each time it cold-starts. The data directory (`.pgdata/`) and this venv (`.venv-pg/`) are both gitignored; nothing here is meant to be shared across machines.

---

## Jupyter Notebook

For interactive analysis, run the notebook directly:

```bash
source .venv/bin/activate
jupyter notebook Excelsis.ipynb
```

Run cells in order (1 → 9). The notebook uses the same `src/` modules as the web backend. Cell 2 verifies that Ollama is reachable before continuing.

To change who the analyst is (and what data they can access), edit `CURRENT_USER` in Cell 5:

```python
CURRENT_USER = UserContext(user_id="ms_johnson")
```

---

## Data

Data is read directly from SQL Server. Configure the connection in `.env` (see [Environment Variables](#environment-variables)).

The agent can query any database listed in `SQL_DATABASES`. The agent will adapt its T-SQL to whatever schema it finds via `run_sql_query`.

---

## AWS Deployment

The app runs as native OS processes — no containers required, which also means no ECS/EKS/Fargate dependency. This works on any EC2 instance, including ones without hypervisor access for Docker Desktop (nested-virtualization-restricted instance types).

**Minimum setup on a fresh EC2 instance:**

1. Install Python 3.11+, Node 18+, and the ODBC Driver 18 for SQL Server.
2. Provision SQL Server — either on the same instance, a separate EC2 instance, or Amazon RDS for SQL Server (set `SQL_SERVER` to the RDS endpoint).
3. Install Ollama, or configure `OLLAMA_BASE_URL`/`OLLAMA_API_KEY` to use Ollama's hosted cloud API instead of running a local model — cloud is the practical choice on smaller instance types that can't host a multi-GB model.
4. Follow [Quick Start](#quick-start) steps 2–4 (env config, Python deps, frontend deps).
5. Download the observability binaries once (see [Observability Stack](#observability-stack)).
6. Register each long-running process (Uvicorn, Vite/build output served statically, Garnet, Prometheus, Grafana) with a process supervisor so they survive reboots and restart on crash:
   - **Linux**: `systemd` unit files (one per service) or a process manager like `pm2`.
   - **Windows**: `nssm` (Non-Sucking Service Manager) to wrap each `.exe`/script as a Windows Service, or Task Scheduler with "run at startup."
7. For production, build the frontend once (`npm run build` in `web/`) and serve the static output from a proper web server or CDN in front of FastAPI, rather than running the Vite dev server.

**Networking / security group notes:**
- Only `:8000` (API) and the frontend's serving port need to be reachable from outside the instance.
- `:6379` (Redis/Garnet), `:9090` (Prometheus), and `:3001` (Grafana) should stay restricted to localhost or a private subnet/VPN — none of them have auth enabled by default in this setup.
- Store secrets (`JWT_SECRET`, `SQL_PASSWORD`, `OLLAMA_API_KEY`, `ADMIN_PASSWORD`) in AWS Secrets Manager or Parameter Store rather than a plain `.env` file on production hosts; load them into the process environment at startup instead of committing them to disk.
- Rotate `JWT_SECRET` and `ADMIN_PASSWORD` away from their defaults before any production use — the app hard-fails startup if `JWT_SECRET` is still the placeholder value, but `ADMIN_PASSWORD` has no such guard.

---
