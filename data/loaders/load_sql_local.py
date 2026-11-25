#!/usr/bin/env python
"""Stage 3: load the registry into local SQL Server 2022 (docker).

1. Wait for the server, (re)create database MSSQL_DB.
2. Apply docs/schema.sql unchanged (split on lines that are exactly GO).
3. Bulk-insert the registry CSVs in FK order.
4. Verify: SQL row counts == CSV row counts; every registry.v_* view
   returns rows; email is masked for a user without UNMASK.

Connection settings come from .env (MSSQL_SA_PASSWORD, MSSQL_HOST,
MSSQL_PORT, MSSQL_DB). The password is never printed.
Exercise content: fictional data.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyodbc
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "schema.sql"
LOAD_ORDER = ["applicants", "protected_areas", "sites", "licences", "prior_decisions", "open_cases"]
DATE_COLS = {"created_at", "issue_date", "expiry_date", "decision_date", "opened_date"}
DECIMAL_COLS = {"latitude", "longitude", "centre_latitude", "centre_longitude"}
INT_COLS = {"radius_m", "has_precedent_summary"}


def conn_str(db: str = "master") -> str:
    host, port = os.getenv("MSSQL_HOST", "localhost"), os.getenv("MSSQL_PORT", "1433")
    pw = os.environ["MSSQL_SA_PASSWORD"]
    return (f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={host},{port};DATABASE={db};"
            f"UID=sa;PWD={{{pw}}};Encrypt=yes;TrustServerCertificate=yes")


def wait_for_server(timeout_s: int = 240) -> pyodbc.Connection:
    start, last = time.time(), None
    while time.time() - start < timeout_s:
        try:
            return pyodbc.connect(conn_str(), autocommit=True, timeout=5)
        except pyodbc.Error as e:  # server still starting
            last = e
            time.sleep(3)
    raise SystemExit(f"SQL Server not reachable after {timeout_s}s: {last}")


def recreate_db(master: pyodbc.Connection, db: str):
    if not re.fullmatch(r"\w+", db):
        raise SystemExit(f"bad database name {db!r}")
    cur = master.cursor()
    cur.execute(f"IF DB_ID('{db}') IS NOT NULL BEGIN "
                f"ALTER DATABASE [{db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [{db}]; END")
    cur.execute(f"CREATE DATABASE [{db}]")


def apply_schema(conn: pyodbc.Connection) -> int:
    batches, buf = [], []
    for line in SCHEMA.read_text(encoding="utf-8").splitlines():
        if line.strip() == "GO":
            batches.append("\n".join(buf))
            buf = []
        else:
            buf.append(line)
    batches.append("\n".join(buf))
    cur = conn.cursor()
    n = 0
    for b in batches:
        if b.strip():
            cur.execute(b)
            n += 1
    return n


def typed(col: str, v: str):
    if v == "":
        return None
    if col in DATE_COLS:
        return date.fromisoformat(v)
    if col in DECIMAL_COLS:
        return Decimal(v)
    if col in INT_COLS:
        return int(v)
    return v


def load_table(conn: pyodbc.Connection, reg_dir: Path, table: str) -> int:
    df = pd.read_csv(reg_dir / f"{table}.csv", dtype=str, keep_default_na=False)
    cols = list(df.columns)
    rows = [tuple(typed(c, v) for c, v in zip(cols, rec)) for rec in df.itertuples(index=False)]
    sql = f"INSERT INTO registry.{table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    cur = conn.cursor()
    cur.fast_executemany = True
    cur.executemany(sql, rows)
    conn.commit()
    return len(df)


def verify(conn: pyodbc.Connection, csv_counts: dict[str, int]) -> list[tuple[str, str, str, bool]]:
    cur = conn.cursor()
    out = []
    for t, n in csv_counts.items():
        got = cur.execute(f"SELECT COUNT(*) FROM registry.{t}").fetchval()
        out.append((f"rows registry.{t}", str(n), str(got), got == n))
    views = [r[0] for r in cur.execute(
        "SELECT name FROM sys.views WHERE schema_id = SCHEMA_ID('registry') ORDER BY name").fetchall()]
    out.append(("registry views found", "4", str(len(views)), len(views) == 4))
    for v in views:
        got = cur.execute(f"SELECT COUNT(*) FROM registry.{v}").fetchval()
        sample = cur.execute(f"SELECT TOP 1 * FROM registry.{v}").fetchone()
        out.append((f"view registry.{v} rows", ">0", str(got), got > 0 and sample is not None))
    # spot-check view logic against the CSV-derived seed counts
    n_ref = cur.execute("SELECT COUNT(*) FROM registry.v_applicant_history WHERE prior_refusals > 0").fetchval()
    n_enf = cur.execute("SELECT COUNT(*) FROM registry.v_applicant_history WHERE open_enforcement_cases > 0").fetchval()
    out.append(("v_applicant_history: applicants with refusal", "30", str(n_ref), n_ref == 30))
    out.append(("v_applicant_history: open enforcement", "12", str(n_enf), n_enf == 12))
    # dynamic data masking: a user without UNMASK sees masked email / national_id
    cur.execute("IF USER_ID('mask_probe') IS NULL CREATE USER mask_probe WITHOUT LOGIN")
    cur.execute("GRANT SELECT ON registry.applicants TO mask_probe")
    cur.execute("EXECUTE AS USER = 'mask_probe'")
    masked = cur.execute("SELECT TOP 5 email, national_id, phone FROM registry.applicants "
                         "WHERE national_id IS NOT NULL ORDER BY applicant_id").fetchall()
    cur.execute("REVERT")
    real = cur.execute("SELECT TOP 5 email, national_id, phone FROM registry.applicants "
                       "WHERE national_id IS NOT NULL ORDER BY applicant_id").fetchall()
    cur.execute("DROP USER mask_probe")
    email_masked = all(m[0] != r[0] and re.fullmatch(r"\wXXX@XXXX\.com", m[0]) for m, r in zip(masked, real))
    nid_masked = all(m[1] != r[1] and m[1].startswith("XXXX") for m, r in zip(masked, real))
    phone_masked = all(m[2] == "xxxx" for m in masked)
    out.append(("email masked without UNMASK", "e.g. aXXX@XXXX.com", masked[0][0], email_masked))
    out.append(("national_id masked without UNMASK", "XXXX + last 4", masked[0][1], nid_masked))
    out.append(("phone masked without UNMASK", "xxxx", masked[0][2], phone_masked))
    out.append(("sa (UNMASK) sees real email", "@example.test", real[0][0].split("@")[1],
                real[0][0].endswith("@example.test")))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--registry", type=Path, default=ROOT / "data" / "output" / "registry")
    p.add_argument("--config-seed", type=Path, default=ROOT / "data" / "output" / "rules" / "config_seed.sql")
    args = p.parse_args()
    load_dotenv(ROOT / ".env")
    if not os.getenv("MSSQL_SA_PASSWORD"):
        raise SystemExit("MSSQL_SA_PASSWORD missing: copy .env.example to .env and set it")
    db = os.getenv("MSSQL_DB", "uc1")

    master = wait_for_server()
    print(f"server up: {master.getinfo(pyodbc.SQL_DBMS_VER)}")
    recreate_db(master, db)
    conn = pyodbc.connect(conn_str(db), autocommit=True)
    print(f"schema batches applied: {apply_schema(conn)}")
    conn.autocommit = False
    counts = {}
    for t in LOAD_ORDER:
        counts[t] = load_table(conn, args.registry, t)
        print(f"loaded {t:16s} {counts[t]}")
    conn.autocommit = True
    config_rows = None
    if args.config_seed.exists():  # Stage 4 output
        conn.cursor().execute(args.config_seed.read_text(encoding="utf-8"))
        config_rows = {t: conn.cursor().execute(f"SELECT COUNT(*) FROM config.{t}").fetchval()
                       for t in ("checklist_items", "risk_factors", "risk_tiers", "thresholds")}
        print(f"config seeded: {config_rows}")
    else:
        print(f"config_seed.sql not found ({args.config_seed}); run `make rules` to seed config tables")

    rows = verify(conn, counts)
    if config_rows is not None:
        sql = args.config_seed.read_text(encoding="utf-8")
        for t, n in config_rows.items():
            want = len(re.findall(rf"INSERT INTO config\.{t} ", sql))
            rows.append((f"rows config.{t}", str(want), str(n), n == want))
    w = max(len(r[0]) for r in rows)
    print(f"\n{'check':{w}}  {'target':18}  actual")
    for name, t, a, ok in rows:
        print(f"{name:{w}}  {t:18}  {a}  {'PASS' if ok else 'FAIL'}")
    failed = sum(not r[3] for r in rows)
    print(f"\n{len(rows) - failed}/{len(rows)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
