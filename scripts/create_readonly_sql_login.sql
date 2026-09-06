/*
 * Creates a dedicated, read-only SQL Server login for Excelsis-Data-Agent.
 *
 * SQL Server has no session-level read-only mode (unlike Postgres) — the
 * read-only guarantee this app relies on for `run_sql_query` (ad-hoc,
 * LLM-generated SQL) has to come from the login/role itself. The app's SQL
 * guard (`_assert_select_only` + the identifier guard in src/sql_store.py)
 * is defence-in-depth on top of this, not a substitute for it.
 *
 * This script grants ONLY db_datareader (SELECT) in each configured
 * database — no INSERT/UPDATE/DELETE/DDL/EXECUTE rights at all, so even a
 * bypass of the app-level SQL guard cannot mutate data or run stored
 * procedures.
 *
 * WARNING: never write the real password into this file or commit it to
 * git. Fill in :setvar NewLoginPassword below with a real value only in a
 * local, untracked copy, or pass it on the command line instead:
 *
 *   sqlcmd -S <server> -U <admin_login> -P <admin_password> \
 *       -v NewLoginPassword="<paste-generated-password-here>" \
 *       -i create_readonly_sql_login.sql
 *
 * Run this once per SQL Server instance, connected as an account with
 * sysadmin or securityadmin + db_owner rights (the app's own runtime login
 * does NOT need these rights and should not be used to run this script).
 *
 * After running, update .env:
 *   SQL_USERNAME=excelsis_readonly
 *   SQL_PASSWORD=<the same password you passed as NewLoginPassword>
 */

:setvar NewLoginPassword "REPLACE_WITH_GENERATED_PASSWORD_NEVER_COMMIT"

USE [master];
GO

IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'excelsis_readonly')
BEGIN
    CREATE LOGIN [excelsis_readonly]
        WITH PASSWORD = N'$(NewLoginPassword)',
             CHECK_POLICY = ON,
             CHECK_EXPIRATION = OFF;
END
GO

-- Repeat this block (USE <db>; ... GRANT ...) for every database listed in
-- SQL_DATABASES. Only SISDemo is configured as of this writing.
USE [SISDemo];
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'excelsis_readonly')
BEGIN
    CREATE USER [excelsis_readonly] FOR LOGIN [excelsis_readonly];
END
GO

ALTER ROLE [db_datareader] ADD MEMBER [excelsis_readonly];
GO

-- Belt-and-suspenders: db_datareader already excludes these, but an
-- explicit DENY survives even a future accidental role change and costs
-- nothing to keep.
DENY INSERT, UPDATE, DELETE, EXECUTE, ALTER, CONTROL TO [excelsis_readonly];
GO

PRINT 'excelsis_readonly login created/verified with db_datareader-only access to SISDemo.';
GO
