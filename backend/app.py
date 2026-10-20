from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data.sqlite3"
app = FastAPI(title="SQL Query Verifier", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    allowed_actions = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    if hasattr(sqlite3, "SQLITE_RECURSIVE"):
        allowed_actions.add(sqlite3.SQLITE_RECURSIVE)
    denied_functions = {"load_extension", "readfile", "writefile"}

    def authorize(action: int, arg1: str | None, arg2: str | None, database: str | None, _source: str | None) -> int:
        if action not in allowed_actions:
            return sqlite3.SQLITE_DENY
        # SQLite reports a null database name for optimized COUNT(*) reads.
        if action == sqlite3.SQLITE_READ and (database not in {None, "main"} or arg1 not in SCHEMA):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower() in denied_functions:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    db.set_authorizer(authorize)
    db.set_progress_handler(lambda: 1, 100_000)
    return db


def seed() -> None:
    if DB_PATH.exists():
        return
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, segment TEXT NOT NULL, email TEXT);
    CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, unit_price REAL NOT NULL);
    CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), order_date TEXT NOT NULL, status TEXT NOT NULL);
    CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL, unit_price REAL NOT NULL);
    """)
    customers = [(1,"Avery Chen","Enterprise","avery@example.test"),(2,"Jordan Patel","SMB","jordan@example.test"),(3,"Morgan Lee","Consumer","morgan@example.test"),(4,"Riley Kim","Enterprise","riley@example.test"),(5,"Casey Diaz","SMB","casey@example.test")]
    products = [(1,"Cloud Desk","Software",120.0),(2,"Analytics Pack","Software",80.0),(3,"Support Plan","Services",50.0),(4,"Starter Kit","Hardware",40.0)]
    db.executemany("INSERT INTO customers VALUES(?,?,?,?)", customers)
    db.executemany("INSERT INTO products VALUES(?,?,?,?)", products)
    orders = [(1,1,"2026-07-05","paid"),(2,1,"2026-08-12","paid"),(3,2,"2026-07-18","paid"),(4,2,"2026-09-03","paid"),(5,3,"2026-08-22","paid"),(6,4,"2026-09-16","paid"),(7,5,"2026-09-25","cancelled")]
    db.executemany("INSERT INTO orders VALUES(?,?,?,?)", orders)
    items = [(1,1,1,2,120),(2,1,3,1,50),(3,2,2,3,80),(4,2,4,2,40),(5,3,1,1,120),(6,3,2,2,80),(7,4,3,4,50),(8,5,4,3,40),(9,6,1,1,120),(10,6,2,1,80),(11,6,3,2,50),(12,7,4,2,40)]
    db.executemany("INSERT INTO order_items VALUES(?,?,?,?,?)", items)
    db.commit(); db.close()


seed()

SCHEMA = {
    "customers": {"columns": ["id", "name", "segment", "email"], "primary_key": "id"},
    "orders": {"columns": ["id", "customer_id", "order_date", "status"], "primary_key": "id", "foreign_keys": ["customer_id → customers.id"]},
    "order_items": {"columns": ["id", "order_id", "product_id", "quantity", "unit_price"], "primary_key": "id", "foreign_keys": ["order_id → orders.id", "product_id → products.id"]},
    "products": {"columns": ["id", "name", "category", "unit_price"], "primary_key": "id"},
}
GOOD_SQL = """SELECT c.segment, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS revenue
FROM customers c JOIN orders o ON o.customer_id = c.id
JOIN order_items oi ON oi.order_id = o.id
WHERE o.status = 'paid'
GROUP BY c.segment ORDER BY c.segment"""
BAD_SQL = """SELECT c.segment, ROUND(SUM(o_revenue.revenue), 2) AS revenue
FROM customers c JOIN (SELECT o.id, o.customer_id, SUM(oi.quantity * oi.unit_price) revenue FROM orders o JOIN order_items oi ON oi.order_id=o.id WHERE o.status='paid' GROUP BY o.id) o_revenue ON o_revenue.customer_id=c.id
JOIN orders o2 ON o2.customer_id=c.id
GROUP BY c.segment ORDER BY c.segment"""


class GenerateRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)

class RunRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=10000)
    question: str = ""


def generation(question: str) -> dict[str, Any]:
    if os.getenv("LLM_PROVIDER", "mock") == "openai" and os.getenv("OPENAI_API_KEY"):
        try:
            from openai import OpenAI
            schema = json.dumps(SCHEMA)
            response = OpenAI().chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), response_format={"type":"json_object"}, messages=[{"role":"system","content":f"Generate one safe read-only SQLite SELECT. Return JSON with sql, assumptions, tables_used, joins, grain. Schema: {schema}. Never request or infer data records."},{"role":"user","content":question}])
            result = json.loads(response.choices[0].message.content or "{}")
            if not isinstance(result, dict) or not isinstance(result.get("sql"), str): raise ValueError("Missing SQL")
            for field in ("assumptions", "tables_used", "joins"):
                if field not in result:
                    result[field] = []
                if not isinstance(result[field], list) or not all(isinstance(item, str) for item in result[field]):
                    raise ValueError(f"Invalid {field}")
            if "grain" not in result:
                result["grain"] = "as requested"
            if not isinstance(result["grain"], str):
                raise ValueError("Invalid grain")
            return result
        except Exception as exc:
            raise HTTPException(502, detail=f"SQL provider failed: {type(exc).__name__}") from exc
    q = question.lower()
    if "segment" in q and "revenue" in q:
        sql = GOOD_SQL
    elif "month" in q and "revenue" in q:
        sql = "SELECT strftime('%Y-%m', o.order_date) AS month, ROUND(SUM(oi.quantity * oi.unit_price),2) AS revenue FROM orders o JOIN order_items oi ON oi.order_id=o.id WHERE o.status='paid' GROUP BY month ORDER BY month DESC LIMIT 3"
    else:
        sql = GOOD_SQL
    return {"sql": sql, "assumptions": ["Revenue means quantity × order item unit price.", "Cancelled orders are excluded."], "tables_used": ["customers", "orders", "order_items"], "joins": ["orders.customer_id → customers.id", "order_items.order_id → orders.id"], "grain": "one row per customer segment"}


def validate_sql(sql: str) -> str:
    cleaned = sql.strip()
    if not cleaned or ";" in cleaned.rstrip(";") or cleaned.count(";") > 1:
        raise HTTPException(400, detail="Only one SQL statement is allowed.")
    cleaned = cleaned.rstrip(";").strip()
    if not re.match(r"^(SELECT|WITH\b)", cleaned, re.I):
        raise HTTPException(400, detail="Only read-only SELECT or WITH…SELECT statements are allowed.")
    forbidden = r"\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|ANALYZE|LOAD_EXTENSION)\b|\b(readfile|writefile|load_extension)\s*\(|\bINTO\s+(OUTFILE|DUMPFILE)\b"
    if re.search(forbidden, cleaned, re.I):
        raise HTTPException(400, detail="Unsafe SQL operation blocked.")
    if re.search(r"\bFROM\s+['\"]|\bJOIN\s+['\"]", cleaned, re.I):
        raise HTTPException(400, detail="External data sources are not allowed.")
    return cleaned


def run(sql: str) -> tuple[list[dict[str, Any]], int]:
    sql = validate_sql(sql)
    db = connect(); started = time.perf_counter()
    try:
        cur = db.execute(sql)
        if cur.description is None: raise HTTPException(400, detail="Statement did not return rows.")
        rows = [dict(r) for r in cur.fetchmany(501)]
        if len(rows) > 500: raise HTTPException(400, detail="Result exceeds the 500 row limit.")
        return rows, round((time.perf_counter()-started)*1000)
    except sqlite3.Error as exc:
        msg = str(exc)
        raise HTTPException(400, detail=msg[:180]) from exc
    finally:
        db.close()


def verify(sql: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    def add(name: str, status: str, explanation: str, evidence: str, applicable: bool = True):
        checks.append({"name":name,"status":status,"explanation":explanation,"evidence":evidence,"applicable":applicable})
    add("Read-only SQL safety", "PASS", "Single statement passed the SQL allowlist and SQLite query-only boundary.", "SELECT-only; sandbox database opened read-only")
    add("Result shape", "PASS" if rows else "WARNING", "Rows were returned with named columns." if rows else "The query returned no rows.", f"{len(rows)} rows; columns: {', '.join(rows[0].keys()) if rows else 'none'}")
    add("Row-count sanity", "PASS" if len(rows)<=100 else "WARNING", "Output stays within the expected compact analytical range." if len(rows)<=100 else "More than 100 rows returned; review expected grain.", f"{len(rows)} rows")
    null_cols = [k for k in (rows[0] if rows else {}) if any(r.get(k) is None for r in rows)]
    add("Important output nulls", "WARNING" if null_cols else "PASS", "Nulls occur in output columns." if null_cols else "No nulls found in output columns.", ", ".join(null_cols) if null_cols else "0 null values")
    key_cols = [k for k in (rows[0] if rows else {}) if k.lower() in {"id", "order_id", "customer_id", "product_id", "segment"}]
    if key_cols:
        duplicates = {k: len(rows) - len({r.get(k) for r in rows}) for k in key_cols}
        duplicates = {k: n for k, n in duplicates.items() if n > 0}
        add("Duplicate output keys", "WARNING" if duplicates else "PASS", "A key-shaped output column repeats; this may violate the declared grain." if duplicates else "Key-shaped output columns are unique.", json.dumps(duplicates or {k: "unique" for k in key_cols}, sort_keys=True))
    else:
        add("Duplicate output keys", "WARNING", "Inconclusive: no conventional key or segment grain column is exposed in the output.", "No id/order_id/customer_id/product_id/segment column", False)
    revenue = "revenue" in (rows[0] if rows else {})
    db=connect()
    expected={r["segment"]:r["revenue"] for r in db.execute("SELECT c.segment, ROUND(SUM(oi.quantity*oi.unit_price),2) revenue FROM customers c JOIN orders o ON o.customer_id=c.id JOIN order_items oi ON oi.order_id=o.id WHERE o.status='paid' GROUP BY c.segment")}
    db.close()
    if revenue and any("segment" in r for r in rows):
        actual: dict[str, float] = {}
        duplicates: dict[str, int] = {}
        invalid_values: list[str] = []
        for row in rows:
            key = str(row["segment"])
            if key in actual:
                duplicates[key] = duplicates.get(key, 1) + 1
                continue
            try:
                actual[key] = float(row["revenue"])
            except (TypeError, ValueError):
                invalid_values.append(key)
        mismatches={k:{"expected":v,"actual":actual.get(k)} for k,v in expected.items() if k not in actual or abs(actual[k]-float(v))>0.01}
        extra={k:v for k,v in actual.items() if k not in expected}
        ok=not mismatches and not extra and not duplicates and not invalid_values
        evidence = {"expected":expected,"actual":actual,"mismatches":mismatches,"extra_keys":extra,"duplicate_grain_keys":duplicates,"invalid_numeric_values":invalid_values}
        add("Seeded revenue reconciliation", "PASS" if ok else "FAIL", "Segment totals reconcile to an independent calculation over paid order items." if ok else "Output differs from independently aggregated paid order item revenue or violates one-row-per-segment grain.", json.dumps(evidence, sort_keys=True))
    else:
        add("Seeded revenue reconciliation", "WARNING", "Not applicable: output lacks both segment and revenue columns.", "Expected grain: one row per customer segment", False)
    fanout = bool(re.search(r"\bJOIN\s+orders\s+o2\b", sql, re.I))
    if fanout:
        db=connect(); base=db.execute("SELECT COUNT(*) FROM orders WHERE status='paid'").fetchone()[0]; joined=db.execute("SELECT COUNT(*) FROM orders a JOIN orders b ON b.customer_id=a.customer_id WHERE a.status='paid'").fetchone()[0]; db.close()
        add("Join fan-out and grain", "FAIL", "An extra customer-to-orders join repeats each order-level amount for every matching order.", f"Paid orders before join: {base}; order pairs after join: {joined}; multiplier: {joined/base:.2f}×")
    else:
        add("Join fan-out and grain", "WARNING", "Static fan-out heuristic did not find the demo's repeated order join; arbitrary SQL grain cannot be inferred reliably.", "Review declared output grain and reconciliation checks.", False)
    statuses=[c["status"] for c in checks if c["applicable"]]
    overall="FAIL" if "FAIL" in statuses else ("WARNING" if "WARNING" in statuses else "PASS")
    return {"status":overall,"checks":checks,"summary":"Deterministic checks provide evidence and safeguards, not proof of arbitrary SQL correctness."}


@app.get("/api/health")
def health(): return {"status":"ok","provider":os.getenv("LLM_PROVIDER","mock"),"synthetic_data":True}

@app.get("/api/schema")
def schema(): return {"tables":SCHEMA,"relationships":["orders.customer_id → customers.id (many-to-one)","order_items.order_id → orders.id (many-to-one)","order_items.product_id → products.id (many-to-one)"]}

@app.post("/api/generate")
def generate(req: GenerateRequest): return generation(req.question)

@app.post("/api/run")
def execute(req: RunRequest):
    start=time.perf_counter()
    rows, elapsed=run(req.sql)
    report=verify(req.sql,rows)
    return {"rows":rows,"row_count":len(rows),"execution_ms":elapsed,"report":report,"sql":req.sql,"trace":[{"step":"SQL safety","status":"PASS"},{"step":"SQLite execution","status":"PASS","detail":f"{len(rows)} rows in {elapsed} ms"},{"step":"Deterministic verification","status":report["status"]}],"repair":("Use the reference query: aggregate order items once, joined by order_id, then group by customer segment." if report["status"]=="FAIL" else None)}

@app.post("/api/demo")
def demo():
    bad_rows, bad_ms=run(BAD_SQL); bad_report=verify(BAD_SQL,bad_rows)
    good_rows, good_ms=run(GOOD_SQL); good_report=verify(GOOD_SQL,good_rows)
    return {"question":"What was total revenue by customer segment?","flawed":{"sql":BAD_SQL,"rows":bad_rows,"execution_ms":bad_ms,"report":bad_report},"corrected":{"sql":GOOD_SQL,"rows":good_rows,"execution_ms":good_ms,"report":good_report}}

@app.post("/api/export/csv")
def export_csv(payload: dict[str, Any]):
    import csv, io
    rows=payload.get("rows",[]); out=io.StringIO()
    if rows:
        writer=csv.DictWriter(out,fieldnames=list(rows[0].keys())); writer.writeheader(); writer.writerows(rows)
    return Response(out.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=results.csv"})

@app.post("/api/export/json")
def export_json(payload: dict[str, Any]):
    return Response(json.dumps(payload,indent=2),media_type="application/json",headers={"Content-Disposition":"attachment; filename=verification-report.json"})
