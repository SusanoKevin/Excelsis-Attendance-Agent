"""Manages a self-owned, embedded PostgreSQL server for Excelsis-Data-Agent's
own infrastructure (LangGraph conversation checkpoints today; audit/retention
could move here too). This exists specifically because the project's actual
SQL Server data source (the school's SIS) is administered by someone else --
we have no sysadmin/securityadmin rights there, so we cannot create the
db_datareader-only login documented in create_readonly_sql_login.sql.

This server is DIFFERENT: we own it outright (superuser access), so the
least-privilege pattern is applied here for real -- a dedicated `excelsis_app`
role with a real password, scoped to only the `excelsis_checkpoints`
database, is created automatically on first `start`. It holds ONLY the app's
own conversation-checkpoint data, never a copy of the school's SIS data.

Must be run with the dedicated Python 3.12 venv (.venv-pg), NOT the main
project .venv (Python 3.14) -- `pgserver` has no wheels for 3.14.

Usage (from the repo root):
    .venv-pg/Scripts/python.exe scripts/local_postgres.py start
    .venv-pg/Scripts/python.exe scripts/local_postgres.py status
    .venv-pg/Scripts/python.exe scripts/local_postgres.py stop

`start` is idempotent and self-healing: since pgserver picks a fresh random
port on Windows every time the underlying process cold-starts (there is no
public API to pin one), `start` always re-derives the live connection URI
and rewrites CHECKPOINT_DB_URI in .env to match -- so a restart after an
instance reboot does not require any manual .env editing.
"""
from __future__ import annotations

import argparse
import re
import secrets
import string
import sys
from pathlib import Path

import pgserver
import psycopg2

REPO_ROOT = Path(__file__).resolve().parent.parent
PGDATA = REPO_ROOT / ".pgdata"
ENV_FILE = REPO_ROOT / ".env"
APP_DB = "excelsis_checkpoints"
APP_ROLE = "excelsis_app"
_PASSWORD_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits + "!@#%^&*()-_+.,"
    if c not in ";{}=\"'"
)


def _generate_password(length: int = 28) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def _harden_pg_hba(pgdata: Path) -> None:
    """Requires a real password (scram-sha-256) specifically for excelsis_app
    connecting to its own database -- inserted before the general 'trust'
    rule pgserver/initdb ships by default, so it wins (pg_hba matches
    top-to-bottom, first match wins). The postgres superuser keeps using
    local trust for admin convenience: this server only ever binds to
    127.0.0.1, so that guarantee already comes from OS-level access to this
    box, same as every other tool in this project's Observability Stack.
    Idempotent -- a no-op if the app-specific rule is already present.
    """
    hba_path = pgdata / "pg_hba.conf"
    text = hba_path.read_text()
    if APP_ROLE in text:
        return

    marker = "# TYPE  DATABASE        USER            ADDRESS                 METHOD"
    app_rules = (
        f"\n# {APP_ROLE} requires a real password even though the postgres\n"
        f"# superuser is trusted locally (see comment above marker).\n"
        f"host    {APP_DB}         {APP_ROLE}         127.0.0.1/32            scram-sha-256\n"
        f"host    {APP_DB}         {APP_ROLE}         ::1/128                 scram-sha-256\n"
    )
    hba_path.write_text(text.replace(marker, marker + app_rules, 1))


def _bootstrap_app_role(uri: str) -> str:
    """Creates the excelsis_app role/database on first run if missing, and
    (re)sets its password so `start` always knows a working credential --
    even after a fresh pgdata init. Returns the app role's current password.
    Connects as the postgres superuser (still trust-authenticated over the
    admin path used here) to perform the one-time bootstrap.
    """
    password = _generate_password()
    conn = psycopg2.connect(uri)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE ROLE "{APP_ROLE}" LOGIN PASSWORD %s', (password,))
            else:
                cur.execute(f'ALTER ROLE "{APP_ROLE}" PASSWORD %s', (password,))

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (APP_DB,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{APP_DB}" OWNER "{APP_ROLE}"')

            # Postgres grants CONNECT on every database to PUBLIC by default,
            # so without this, excelsis_app could also reach the postgres/
            # template1 maintenance databases via the general trust rule --
            # defeating the point of scoping it to excelsis_checkpoints only.
            cur.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")
            cur.execute("REVOKE CONNECT ON DATABASE template1 FROM PUBLIC")
    finally:
        conn.close()
    return password


def _update_env_checkpoint_uri(new_uri: str) -> None:
    if not ENV_FILE.exists():
        print(f"warning: {ENV_FILE} not found -- not updating CHECKPOINT_DB_URI", file=sys.stderr)
        return
    lines = ENV_FILE.read_text().splitlines(keepends=True)
    pattern = re.compile(r"^CHECKPOINT_DB_URI=.*$")
    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line.rstrip("\n")):
            lines[i] = f"CHECKPOINT_DB_URI={new_uri}\n"
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"CHECKPOINT_DB_URI={new_uri}\n")
    ENV_FILE.write_text("".join(lines))


def _reload_config(admin_uri: str) -> None:
    # Not server.psql(): it shells out to the bundled psql.exe, which on
    # Windows can invoke a pager that blocks forever with no TTY attached in
    # a non-interactive shell. psycopg2 has no such subprocess/TTY step.
    conn = psycopg2.connect(admin_uri)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_reload_conf();")
    finally:
        conn.close()


def cmd_start(_args: argparse.Namespace) -> None:
    PGDATA.mkdir(exist_ok=True)

    server = pgserver.get_server(str(PGDATA), cleanup_mode=None)
    admin_uri = server.get_uri()  # postgres superuser, always trust-authenticated locally

    password = _bootstrap_app_role(admin_uri)
    _harden_pg_hba(PGDATA)
    _reload_config(admin_uri)

    host_port = admin_uri.split("@", 1)[1].split("/", 1)[0]
    app_uri = f"postgresql://{APP_ROLE}:{password}@{host_port}/{APP_DB}"

    _update_env_checkpoint_uri(app_uri)

    print(f"Postgres running at {host_port}, database '{APP_DB}'.")
    print(f"CHECKPOINT_DB_URI written to {ENV_FILE}.")
    print(f"App role password (save this -- shown once): {password}")


def cmd_status(_args: argparse.Namespace) -> None:
    if not (PGDATA / "postmaster.pid").exists():
        print("Not running (no postmaster.pid found).")
        return
    server = pgserver.get_server(str(PGDATA), cleanup_mode=None)
    print(f"Running at {server.get_uri().split('@', 1)[1]}")


def cmd_stop(_args: argparse.Namespace) -> None:
    if not (PGDATA / "postmaster.pid").exists():
        print("Not running.")
        return
    server = pgserver.get_server(str(PGDATA), cleanup_mode="stop")
    server.cleanup()
    print("Stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start").set_defaults(func=cmd_start)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
