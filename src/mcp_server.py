from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP

from src.agent import ExcelsisAgent
from src.rag_store import ExcelsisRAGStore
from src.security import UserContext
from src.sql_store import SQLDataStore

MCP_USER = UserContext(user_id=os.getenv("MCP_USER_ID", "mcp_user"))

store = SQLDataStore()
rag_store = ExcelsisRAGStore(
    chroma_path=os.getenv("CHROMA_PATH", ".chroma"),
    embed_model=os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
)
agent = ExcelsisAgent(store=store, rag_store=rag_store)

mcp = FastMCP(
    name="Excelsis 360 Data Analyst",
    instructions=(
        "An AI data analyst for Excelsis 360. "
        "Provides data statistics and threshold alert lists. "
        f"Connected as user '{MCP_USER.user_id}'."
    ),
)


@mcp.tool()
def ask_analyst(query: str) -> str:
    """
    Ask the data analyst a natural-language question.
    The agent will reason across multiple tools and return a comprehensive answer.
    Examples: 'Which group has the worst metric rate?',
              'Show me entities below the threshold',
              'What are the top 5 groups by metric rate this month?'
    """
    return agent.ask(query, user=MCP_USER)


@mcp.tool()
def data_summary() -> str:
    """
    Return a JSON summary of all data:
    record count, entity count, date range, overall metric rate, and dimension list.
    """
    return json.dumps(store.summary(), indent=2)


@mcp.tool()
def threshold_alerts(threshold: float = 75.0) -> str:
    """
    List entities whose metric rate is below the given threshold (default 75%).
    Returns a table with entity ID, label, group, and metric rate.
    """
    df = store.get_threshold_alerts(threshold=threshold)
    if df.empty:
        return f"No entities below {threshold}% threshold."
    return df.to_string(index=False)


@mcp.tool()
def group_statistics(group_by: str = "", period: str = "all") -> str:
    """
    Return metric statistics grouped by a configured dimension.
    group_by: leave empty for default, or specify 'week', 'month', 'day_of_week', or any configured group column.
    period:   'all' | 'last_7_days' | 'last_30_days'
    """
    df = store.compute_stats(group_by=group_by, period=period)
    return df.to_string(index=False) if not df.empty else "No data available."


@mcp.tool()
def schema_lookup(query: str) -> str:
    """
    Look up database table and column definitions from the schema knowledge base.
    Use before writing SQL to verify table names, column names, and data types.
    Examples: 'primary table columns', 'list all configured databases'
    """
    return rag_store.retrieve_schema(query)


@mcp.tool()
def knowledge_lookup(query: str) -> str:
    """
    Search policy and rule documents from the knowledge base.
    Use for questions about thresholds, consequences, exemptions, or procedures.
    Examples: 'threshold for low performers', 'exemption policy'
    """
    return rag_store.retrieve_policy(query)


if __name__ == "__main__":
    # log to stderr, not stdout — FastMCP's stdio transport uses stdout for JSON-RPC
    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info("Starting Excelsis MCP server as '%s'", MCP_USER.user_id)
    mcp.run()
