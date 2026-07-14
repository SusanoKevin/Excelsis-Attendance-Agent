"""Shared SQL Server engine builder for the seed_*.py scripts."""
from __future__ import annotations

from urllib.parse import quote_plus

from sqlalchemy import create_engine


def build_engine(
    database: str,
    server: str,
    driver: str = "{ODBC Driver 18 for SQL Server}",
    auth_method: str = "sql",
    username: str | None = None,
    password: str | None = None,
    **engine_kwargs,
):
    if auth_method == "windows":
        dsn = (
            f"DRIVER={driver};SERVER={server};DATABASE={database};"
            f"Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
    else:
        dsn = (
            f"DRIVER={driver};SERVER={server};DATABASE={database};"
            f"UID={username};PWD={password};TrustServerCertificate=yes;"
        )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={quote_plus(dsn)}", **engine_kwargs)
