import asyncio
import json
import logging
import os
import threading
import urllib.request
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()  # must run before any src.* imports that read env vars

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi.errors import RateLimitExceeded

from api.auth import ensure_default_admin
from api.limiter import limiter
from api.routers.auth import router as auth_router
from api.routers.chat import router as chat_router
from api.routers.data import router as data_router
from api.routers.observability import router as observability_router
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - postgres checkpointing is optional
    AsyncPostgresSaver = None

from src.agent import ExcelsisAgent
from src.rag_ingestor import run_ingestion
from src.rag_store import ExcelsisRAGStore
from src.sql_store import SQLDataStore


logger = logging.getLogger(__name__)


def _checkpointer_cm():
    """Returns the async checkpointer context manager for the API server's
    lifespan. Mirrors src.agent._build_checkpointer's SQLite-by-default,
    Postgres-when-CHECKPOINT_DB_URI-is-set behavior for the async path."""
    db_uri = os.environ.get("CHECKPOINT_DB_URI", "")
    if db_uri:
        if AsyncPostgresSaver is None:
            raise RuntimeError(
                "CHECKPOINT_DB_URI is set but langgraph-checkpoint-postgres is not "
                "installed. Run: pip install langgraph-checkpoint-postgres"
            )
        return AsyncPostgresSaver.from_conn_string(db_uri)
    return AsyncSqliteSaver.from_conn_string(os.getenv("CHAT_DB", "./chat.db"))


def _validate_startup(store: SQLDataStore) -> None:
    model  = os.environ.get("MODEL", "qwen2.5:14b")
    server = os.environ.get("SQL_SERVER", "<not set>")
    dbs    = os.environ.get("SQL_DATABASES", store.primary_db)

    jwt_secret = os.environ.get("JWT_SECRET", "change-me-in-production")
    if jwt_secret == "change-me-in-production":
        raise RuntimeError(
            "SECURITY: JWT_SECRET is the insecure default. "
            "Set JWT_SECRET to a strong random value in your .env before starting."
        )

    ollama_base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY", "")
    ollama_ok = False
    try:
        req = urllib.request.Request(f"{ollama_base_url}/api/tags")
        if ollama_api_key:
            req.add_header("Authorization", f"Bearer {ollama_api_key}")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = json.loads(resp.read())
        ollama_ok = True
        available = {m.get("name") or m.get("model") for m in body.get("models", [])}
        if available and model not in available:
            logger.error(
                "MODEL='%s' is not available at %s (it may have been retired or "
                "requires a different subscription tier) — chat requests will fail "
                "until MODEL is updated to one of the available models.",
                model, ollama_base_url,
            )
    except Exception:
        logger.warning("Ollama not reachable at %s — agent responses will fail", ollama_base_url)

    sql_ok = store.ping()
    if not sql_ok:
        logger.error("SQL Server check failed — verify SQL_SERVER and credentials in .env")

    logger.info("Excelsis 360 startup | model=%s (%s) | sql=%s (%s) | dbs=%s",
                model, "OK" if ollama_ok else "UNREACHABLE",
                server, "OK" if sql_ok else "UNREACHABLE",
                dbs)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_default_admin()
    store = SQLDataStore()
    rag_store = ExcelsisRAGStore(
        chroma_path=os.getenv("CHROMA_PATH", ".chroma"),
        embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
    )
    app.state.store     = store
    app.state.rag_store = rag_store

    async with _checkpointer_cm() as checkpointer:
        if os.environ.get("CHECKPOINT_DB_URI", ""):
            await checkpointer.setup()
        app.state.agent = ExcelsisAgent(store=store, rag_store=rag_store, checkpointer=checkpointer)


        app.state.rag_ready = False

        docs_path = os.getenv("DOCS_PATH", "docs")

        def _ingest_and_mark():
            run_ingestion(rag_store, store, docs_path)
            app.state.rag_ready = True

        threading.Thread(
            target=_ingest_and_mark,
            daemon=False,
            name="rag-ingestor",
        ).start()

        await asyncio.to_thread(_validate_startup, store)
        yield

    store.close()


async def _on_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    _rate_limit_counter.labels(path=request.url.path).inc()
    retry = getattr(exc, "retry_after", None)
    msg   = f"Rate limit exceeded. Try again in {retry}s." if retry else "Rate limit exceeded."
    return JSONResponse(status_code=429, content={"detail": msg})


app = FastAPI(title="Excelsis 360 API", lifespan=lifespan)
Instrumentator().instrument(app).expose(app, include_in_schema=False)

_rate_limit_counter: Counter = Counter(
    "rate_limit_exceeded_total",
    "Total rate limit exceeded rejections",
    ["path"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _on_rate_limit_exceeded)

_allowed_origins = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:3000"
).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(chat_router, prefix="/chat", tags=["chat"])
app.include_router(data_router, prefix="/data", tags=["data"])
app.include_router(observability_router, prefix="/observability", tags=["observability"])


@app.get("/health", tags=["health"])
async def health(request: Request):
    return {
        "status": "ok",
        "rag_ready": getattr(request.app.state, "rag_ready", False),
    }
