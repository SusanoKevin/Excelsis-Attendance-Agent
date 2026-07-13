# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## Environment Setup

```bash
ollama pull qwen2.5:14b
cp .env.example .env
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock          # use the lock file for reproducible installs
pip install -e ".[dev]"                   # adds faker (needed by scripts/seed_test_db.py)
cd web && npm install && cd ..
```

Ollama must be running on `http://localhost:11434` before starting the stack. The embedding model (`BAAI/bge-small-en-v1.5` by default) is downloaded automatically from HuggingFace on first run — no `ollama pull` needed for embeddings.

Required `.env` variables:
- `JWT_SECRET` — must be changed from the default before any production use
- `SQL_SERVER` — SQL Server hostname or IP
- `SQL_DATABASES` — comma-separated list of databases to expose
- `SQL_USERNAME` / `SQL_PASSWORD` — credentials when using `SQL_AUTH_METHOD=sql`
- `SQL_POOL_SIZE` — default `5`; SQLAlchemy `QueuePool` base connection count per database
- `SQL_QUERY_TIMEOUT` — default `30`; per-query connection timeout in seconds

Key optional variables:
- `MODEL` — default `qwen2.5:14b`; Ollama model used by the ReAct agent
- `AT_RISK_THRESHOLD` — default `75.0`
- `ADMIN_PASSWORD` — default `admin123`; sets the initial admin password on first run
- `CHAT_DB` — default `./chat.db`; SQLite file for LangGraph `SqliteSaver` conversation history (persists across restarts, shared across workers)
- `REDIS_URI` — no default (empty); Redis URI for shared rate-limit counters (e.g. `redis://localhost:6379`); falls back to per-process in-memory when unset

Schema / domain config (all optional, override to point at any tabular SQL schema):
- `PRIMARY_TABLE` — default `attendance`; main table the agent queries
- `METRIC_COLUMN` / `POSITIVE_VALUE` — default `status` / `active`; what counts as a success
- `DATE_COLUMN` — default `date`; time column for period filtering
- `ENTITY_COLUMN` / `ENTITY_NAME_COLUMN` — default `entity_id` / `entity_name`
- `GROUP_COLUMNS` — no default (empty); comma-separated grouping dimensions

RAG / vector store config:
- `CHROMA_PATH` — default `.chroma`; persistent ChromaDB directory
- `EMBED_MODEL` — default `BAAI/bge-small-en-v1.5`; HuggingFace embedding model (downloaded automatically on first run)
- `DOCS_PATH` — default `docs`; directory scanned for policy PDFs and Markdown files
- `RAG_CACHE_TTL` — default `3600`; TTL in seconds for RAG query result cache

Prompt validation config:
- `MAX_MESSAGE_LEN` — default `2000`; maximum characters in a single chat message
- `MAX_PROMPT_TOKENS` — default `2048`; maximum estimated tokens (message + history) before rejection

## Docker Test Database

For local development without a production SQL Server:

```bash
docker compose -f docker/docker-compose.yml up -d
pip install faker                              # dev dependency — seeder only
python scripts/seed_test_db.py --scale large   # ~5M rows across 34 tables
cp .env.test .env                              # pre-filled Docker config
```

Databases created: `education_db` (16 tables) and `finance_db` (18 tables).

## Running the Stack

```bash
bash start.sh          # FastAPI :8000 + React :5173
```

On Windows, `start.ps1` is the equivalent (FastAPI + Vite, plus Prometheus/Grafana/Garnet if `tools/` exists — run `scripts/setup_native_stack.ps1` once first to download those portable binaries).

Or individually:
```bash
source .venv/bin/activate
uvicorn api.main:app --reload          # backend only
cd web && npm run dev                  # frontend only
```

Interactive API docs: `http://localhost:8000/docs`

## Tests

```bash
pytest tests/test_qa.py -v                 # unit tests only (no Ollama needed)
pytest tests/test_qa.py -v -m integration  # integration tests (requires Ollama)
pytest tests/test_qa.py -v --run-all       # everything
```

Unit tests (`TestZeroKnowledge`, `TestPromptValidation`) call tools and guards directly — no LLM involved, fast. Integration tests (`TestModelStress`) hit a live Ollama instance.

## Jupyter Notebook

```bash
source .venv/bin/activate
jupyter notebook Excelsis.ipynb
```

Run cells in order (1 → 9). To change the analyst identity, edit `CURRENT_USER` in Cell 5.

---

## Architecture

### Request flow

Browser → React (`web/`) → FastAPI (`api/`) → `validate_message` / `check_token_budget` (`src/prompt_guard.py`) → `ExcelsisAgent` (`src/agent.py`) → LangGraph ReAct loop → tools (`src/tools.py`) → `SQLDataStore` (`src/sql_store.py`)

For schema/policy questions, tools also call → `ExcelsisRAGStore` (`src/rag_store.py`) → ChromaDB vector search.

The `/chat/stream` endpoint uses SSE (`StreamingResponse`); the frontend consumes `on_chat_model_stream`, `on_tool_start`, and `on_tool_end` events from LangGraph's `astream_events`.

### Prompt validation (`src/prompt_guard.py`)

Two functions gate every message before it reaches the agent:
- `validate_message(message)` — strips whitespace, rejects empty strings, enforces `MAX_MESSAGE_LEN` (default 2000 chars), and scans for injection patterns (e.g. "ignore previous instructions", "jailbreak", "DAN"). Returns the stripped message or raises `ValueError`.
- `check_token_budget(message, history_chars=0)` — estimates token count as `(len(message) + history_chars) // 4` and raises `ValueError` if it exceeds `MAX_PROMPT_TOKENS` (default 2048). Called from `ExcelsisAgent._prepare` with only `message` (history is now managed by the LangGraph checkpointer, not tracked separately).

`validate_message` is called in `api/routers/chat.py` before streaming. `check_token_budget` is called in `ExcelsisAgent._prepare`.

### Security (`src/security.py`)

`UserContext` is a simple dataclass with a single field: `user_id`. There is no role enum, no permission system, and no row-level filtering in the current implementation. Authentication is purely identity-based — the JWT `sub` claim is decoded to a username and wrapped in `UserContext`.

### User management (`api/auth.py` + `api/users.json`)

Users are stored as bcrypt-hashed records in `api/users.json`. On startup, `ensure_default_admin()` creates the admin account if missing. JWTs embed only `sub` (username) and `exp` (expiry); `decode_token()` reconstructs a `UserContext(user_id=username)` from them. Token TTL is 24 hours.

### Agent (`src/agent.py`)

`ExcelsisAgent` wraps a LangGraph `create_react_agent` graph. Conversation history is persisted per-user via a LangGraph `SqliteSaver` checkpointer pointed at `CHAT_DB` (default `./chat.db`). Each user's history is keyed by `thread_id=user.user_id` in the graph config; the checkpointer loads and saves state automatically on every call. No in-process history dict, lock, or eviction loop — those were removed. The `ask()` sync path uses a daemon thread + `queue.Queue` to enforce the `_TIMEOUT = 240` s deadline. The `astream_events()` async path uses `asyncio.timeout`.

### RAG layer (`src/rag_store.py` + `src/rag_ingestor.py`)

`ExcelsisRAGStore` holds two ChromaDB collections: `excelsis_schema` (SQL table/column metadata, 6 results) and `excelsis_policy` (policy documents, 4 results). Embeddings use `BAAI/bge-small-en-v1.5` via HuggingFace (auto-downloaded, no Ollama pull needed). On startup, a background thread runs `run_ingestion`, which indexes every `.pdf` and `.md` file under `DOCS_PATH` and auto-ingests `INFORMATION_SCHEMA` for all databases via `sql_store._exec` (bypasses the read-only guard, which would otherwise block system schema queries). Chunk size: 800 chars, overlap: 80.

### MCP server (`src/mcp_server.py`)

FastMCP stdio server that gives a model direct access to Excelsis 360 data. On startup it initialises `SQLDataStore`, `ExcelsisRAGStore`, and `ExcelsisAgent`; user identity is fixed for the process lifetime via `MCP_USER_ID`.

The server exposes two interaction modes:

- **`ask_analyst(query)`** — routes a natural-language question through the full LangGraph ReAct loop; the agent selects tools, reasons step-by-step, and returns a complete answer. Use this when the model wants the agent to do the work.
- **Direct data tools** — bypass the agent and return raw results the model can reason about itself:
  - `data_summary()` — JSON overview (record count, entity count, date range, metric rate)
  - `threshold_alerts(threshold)` — entities below a metric threshold
  - `group_statistics(group_by, period)` — metric stats grouped by dimension and period
  - `schema_lookup(query)` — vector search of DB table/column metadata
  - `knowledge_lookup(query)` — vector search of policy and rule documents

### Data backend (`src/sql_store.py`)

`SQLDataStore` connects to SQL Server via SQLAlchemy (`mssql+pyodbc`) with a `QueuePool` connection pool (one engine per database, created at startup). Pool size and per-query timeout are controlled by `SQL_POOL_SIZE` (default `5`) and `SQL_QUERY_TIMEOUT` (default `30` s). All other connection settings come from env vars: `SQL_SERVER`, `SQL_DATABASES`, `SQL_PRIMARY_DB`, `SQL_AUTH_METHOD`, `SQL_USERNAME`, `SQL_PASSWORD`. The table and column names are fully configurable via `PRIMARY_TABLE`, `METRIC_COLUMN`, `POSITIVE_VALUE`, `DATE_COLUMN`, `ENTITY_COLUMN`, `ENTITY_NAME_COLUMN`, and `GROUP_COLUMNS` — code defaults are `status`, `active`, `date`, `entity_id`, `entity_name`, and empty (no grouping); `PRIMARY_TABLE` has no default and must be set. The agent can also query other databases listed in `SQL_DATABASES` via the `run_sql_query` tool's `database` parameter.

Two internal execution methods exist: `_exec(sql, params, database)` is the trusted pool executor used by all internal methods (`summary`, `compute_stats`, `get_threshold_alerts`, etc.); `_query(sql, params, database)` wraps `_exec` with `_assert_select_only` — a `sqlglot` parse-time guard that blocks DML/DDL and system schemas. Only the `run_sql_query` tool (which receives LLM-provided SQL) calls `_query`; all hardcoded internal SQL goes through `_exec` directly. `store.close()` disposes all engines and is called automatically on FastAPI lifespan shutdown.
