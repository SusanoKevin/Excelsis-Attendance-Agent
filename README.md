# Excelsis 360 — Data Analyst Agent

AI-powered data analyst for the Excelsis360 platform, built on a LangGraph ReAct agent (Ollama local LLMs), SQL Server data backend, and a FastAPI + React full-stack web interface.

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
- **Persistent conversation history** — per-user chat history stored in a SQLite file (`CHAT_DB`) via LangGraph's `SqliteSaver` checkpointer; survives restarts and is shared across Uvicorn workers
- **Jupyter notebook** — full interactive analysis environment that shares the same `src/` backend
- **MCP server** — exposes Excelsis360 data tools to Claude Code via FastMCP
- **Observability** — Prometheus metrics (`agent_tool_invocations_total`, `agent_query_duration_seconds`, `agent_query_errors_total`, `cache_hits_total`, `cache_misses_total`) via `src/tracker.py`; Grafana dashboards in `docker/grafana/`; `/metrics` scrape endpoint always active. Prometheus, Grafana, and Garnet (Redis-compatible cache) run as native Windows processes via `start.ps1` — see [Native Observability Stack](#native-observability-stack)

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
│   └── mcp_server.py     # FastMCP server for Claude Code
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
├── docker/               # Local test infrastructure
│   └── docker-compose.yml  # SQL Server 2022 container (education_db + finance_db)
├── scripts/
│   └── seed_test_db.py   # Seed script — populates education_db (16 tables) and finance_db (18 tables)
├── Excelsis.ipynb        # Interactive Jupyter notebook
├── start.sh              # Start both servers (backend :8000, frontend :5173)
├── requirements.lock     # Pinned Python dependencies (use this for installs)
├── .env.example          # Environment variable template
└── .env.test             # Pre-filled config for the Docker test database
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
| `REDIS_URI` | No | `` | Redis connection URI for shared rate-limit counters across workers (e.g. `redis://localhost:6379`) |
| `DOCS_PATH` | No | `docs` | Directory scanned for policy documents |
| `MAX_MESSAGE_LEN` | No | `2000` | Maximum characters allowed in a single chat message |
| `MAX_PROMPT_TOKENS` | No | `2048` | Maximum estimated tokens (message + history) before rejection |
| `RAG_CACHE_TTL` | No | `3600` | TTL in seconds for RAG query result cache |

---

## Docker Test Database

For local development and integration testing without a production SQL Server, a Docker-based SQL Server 2022 instance is provided with two pre-seeded databases.

```bash
# Start the container (first run downloads ~1.5 GB image)
docker compose -f docker/docker-compose.yml up -d

# Install the seeding dependency (one-time)
pip install faker

# Seed both databases — choose a scale tier
python scripts/seed_test_db.py --scale small   # ~100K rows, fast
python scripts/seed_test_db.py --scale medium  # ~1M rows
python scripts/seed_test_db.py --scale large   # ~5M rows across 34 tables, ~5–10 min

# Use the pre-filled config
cp .env.test .env
```

| Database | Tables | Purpose |
|---|---|---|
| `education_db` | 16 | Attendance, students, teachers, grades, subjects |
| `finance_db` | 18 | Transactions, invoices, purchase orders, budgets, expenses |

---

## Native Windows SQL Server

If you have SQL Server installed locally (no Docker), you can seed the same test databases directly using Windows integrated authentication — no password needed.

**Prerequisites:** ODBC Driver 18 for SQL Server must be installed and the SQL Server service must be running.

```bash
# Install the seeding dependency
pip install -e ".[dev]"

# Create and seed both databases (Windows auth, local default instance)
python scripts/seed_test_db.py --scale medium --auth-method windows --server .
```

Then update `.env`:

```env
SQL_SERVER=.
SQL_AUTH_METHOD=windows
SQL_DATABASES=education_db,finance_db
SQL_PRIMARY_DB=education_db
```

Use `--server .\SQLEXPRESS` if running SQL Server Express, or `--server myhost` for a remote instance. For SQL auth on any server, omit `--auth-method windows` and add `--username sa --password yourpassword` instead.

| Scale | Students | Attendance rows | Approx. time |
|---|---|---|---|
| `small` | 1 000 | 100 000 | ~10 s |
| `medium` | 5 000 | 500 000 | ~1–2 min |
| `large` | 20 000 | 2 000 000 | ~5–10 min |

---

## Native Observability Stack

Prometheus, Grafana, and [Garnet](https://github.com/microsoft/garnet) (Microsoft's RESP-protocol-compatible cache store, used in place of Redis) run as plain native Windows processes — no Docker, no installers, no Windows services. `docker/docker-compose.yml` remains available as an alternative if you'd rather run these in containers instead.

```bash
# One-time: downloads portable binaries into tools/ (gitignored)
powershell -File scripts/setup_native_stack.ps1
```

Then `start.ps1` launches Prometheus (`:9090`), Grafana (`:3000`), and Garnet (`:6379`) alongside the FastAPI/Vite dev servers, and stops all of them together on Ctrl-C. Grafana reuses the same dashboards and datasource provisioning already in `docker/grafana/`; log in at `http://localhost:3000` with `admin`/`admin` (forced reset on first login). Set `REDIS_URI=redis://localhost:6379` in `.env` to point the rate limiter at Garnet instead of the in-memory fallback.

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
| GET | `/health` | None | Liveness check |

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
