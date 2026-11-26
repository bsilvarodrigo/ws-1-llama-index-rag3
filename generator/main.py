"""DataOps Knowledge Hub — continuous data generator.

Simulates a live operation every ``GENERATOR_INTERVAL_SECONDS``:

* PostgreSQL (Ledger): new customers and orders each cycle, new products every 5th cycle.
* MongoDB (Events): ``event_logs`` (pipeline runs) and ``user_activity``.
* SeaweedFS (Data Lake): ``reports/daily-summary-YYYY-MM-DD.csv`` every 10th cycle.

Pipeline and table names match the Neo4j seed graph, so the data correlates across stores.
The drivers are synchronous, so each store's work runs in a thread via ``asyncio.to_thread``
and the stores are written concurrently with ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import random
import secrets
import signal
import sys
import time
import unicodedata
from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, TypeVar

import boto3
import psycopg2
import psycopg2.extensions
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv
from faker import Faker
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

load_dotenv()

T = TypeVar("T")

# ─── Configuration (all from env) ────────────────────────────────────────────

INTERVAL_SECONDS = float(os.getenv("GENERATOR_INTERVAL_SECONDS", "30"))

POSTGRES_DSN = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname": os.getenv("POSTGRES_DB", "dataops"),
    "user": os.getenv("POSTGRES_USER", "dataops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
    "connect_timeout": 5,
    "application_name": "data-generator",
}

MONGO_HOST = os.getenv("MONGO_HOST", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", "27017"))
MONGO_DB = os.getenv("MONGO_DB", "dataops")

S3_ENDPOINT = f"http://{os.getenv('SEAWEEDFS_HOST', 'localhost')}:{os.getenv('SEAWEEDFS_PORT', '8333')}"
S3_BUCKET = os.getenv("SEAWEEDFS_BUCKET", "dataops-lake")

PRODUCTS_EVERY_N_CYCLES = 5
REPORT_EVERY_N_CYCLES = 10
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
EVENT_LOG_TTL_SECONDS = 30 * 24 * 3600  # 30-day log retention (data-retention-policy.md)

# ─── Domain vocabulary (shared with Neo4j and the seed docs) ─────────────────

PIPELINES: dict[str, str] = {  # name -> cadence
    "etl_billing_daily": "daily",
    "etl_orders_hourly": "hourly",
    "etl_customer_sync": "daily",
    "analytics_revenue_agg": "daily",
}
PIPELINE_ERRORS = [
    "Connection timeout to source DB",
    "Schema mismatch on column revenue",
    "Out of memory during aggregation",
    "Deadlock detected while writing to fact_revenue",
    "Duplicate key value violates unique constraint customers_email_key",
    "S3 upload failed: 503 Slow Down from dataops-lake",
    "Upstream dependency etl_billing_daily not finished",
    "Null value in column customer_id violates not-null constraint",
]
PIPELINE_WARNINGS = [
    "Row count 35% below 7-day average",
    "Run exceeded 80% of SLA latency",
    "Retry succeeded after transient connection reset",
    "Late-arriving records skipped for previous partition",
]
TABLES = ["customers", "orders", "products", "fact_revenue", "dim_customers"]
DASHBOARDS = ["Revenue Overview", "Customer Health", "Pipeline Monitor"]
USER_ACTIONS = [
    "query_executed",
    "dashboard_viewed",
    "export_requested",
    "schema_browsed",
    "pipeline_triggered",
]

PLANS = (["free", "pro", "enterprise"], [60, 30, 10])
ORDER_STATUSES = (["completed", "pending", "failed", "refunded"], [70, 15, 10, 5])
EVENT_STATUSES = (["completed", "failed", "warning"], [85, 10, 5])
SEVERITY_BY_STATUS = {"completed": "info", "warning": "warning", "failed": "critical"}

PRODUCT_CATALOG: dict[str, tuple[list[str], list[str]]] = {  # category -> (prefixes, nouns)
    "Analytics": (["Insight", "Metric", "Pulse", "Lens", "Signal"], ["Dashboard Pro", "BI Suite", "Report Builder", "Cohort Explorer"]),
    "Integration": (["Sync", "Bridge", "Flow", "Link", "Relay"], ["Connector", "CDC Stream", "API Gateway", "Webhook Hub"]),
    "Storage": (["Vault", "Lake", "Archive", "Delta", "Stack"], ["Object Store", "Cold Storage", "Lakehouse", "Backup Plan"]),
    "Compute": (["Spark", "Turbo", "Nimbus", "Forge", "Pulse"], ["Worker Pool", "Query Engine", "GPU Node", "Batch Runner"]),
}

fake = Faker("pt_BR")


# ─── Logging (one JSON object per line) ──────────────────────────────────────


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON with any ``extra`` fields included."""

    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}

    def format(self, record: logging.LogRecord) -> str:
        """Render the record as JSON: timestamp, level, message, plus structured extras."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in self._RESERVED})
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging() -> logging.Logger:
    """Configure the root logger to emit JSON lines on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for noisy in ("botocore", "boto3", "urllib3", "pymongo", "faker"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("data-generator")


log = setup_logging()


# ─── Retry helper ────────────────────────────────────────────────────────────


def with_retry(store: str, fn: Callable[[], T], on_error: Callable[[], None] | None = None) -> T:
    """Run ``fn`` up to ``RETRY_ATTEMPTS`` times with exponential backoff (1s, 2s, ...).

    ``on_error`` runs after each failure (e.g. to drop a broken connection so the next
    attempt reconnects). The last exception is re-raised if every attempt fails.
    """
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — any driver error is retryable here
            if on_error:
                on_error()
            if attempt == RETRY_ATTEMPTS:
                raise
            delay = RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1)
            log.warning(
                "store write failed, retrying",
                extra={"store": store, "attempt": attempt, "retry_in_s": delay, "error": repr(exc)},
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


# ─── PostgreSQL (Ledger) ─────────────────────────────────────────────────────


class PostgresWriter:
    """Writes customers, products and orders to the Ledger, reconnecting on failure."""

    def __init__(self) -> None:
        """Create the writer; the connection is opened lazily on first use."""
        self._conn: psycopg2.extensions.connection | None = None

    def _connection(self) -> psycopg2.extensions.connection:
        """Return an open connection, (re)connecting if needed."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(**POSTGRES_DSN)
        return self._conn

    def reset(self) -> None:
        """Drop the current connection so the next call reconnects."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None

    close = reset

    def write_cycle(self, cycle: int) -> dict[str, int]:
        """Insert one cycle of ledger data in a single transaction and return counts."""
        conn = self._connection()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM products WHERE active")
            has_products = cur.fetchone()[0] > 0
            products = 0
            if cycle % PRODUCTS_EVERY_N_CYCLES == 0 or not has_products:
                products = self._insert_products(cur, random.randint(1, 3) if has_products else 10)
            customers = self._insert_customers(cur, random.randint(2, 5))
            orders = self._insert_orders(cur, random.randint(5, 15))
        return {"customers": customers, "products": products, "orders": orders}

    @staticmethod
    def _insert_customers(cur: psycopg2.extensions.cursor, n: int) -> int:
        """Insert ``n`` customers with Brazilian names and a 60/30/10 plan split."""
        inserted = 0
        for _ in range(n):
            first, last = fake.first_name(), fake.last_name()
            email = f"{_ascii(first)}.{_ascii(last)}.{secrets.token_hex(3)}@{fake.free_email_domain()}"
            cur.execute(
                """
                INSERT INTO customers (name, email, plan, company)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                """,
                (f"{first} {last}", email, random.choices(*PLANS)[0], fake.company()),
            )
            inserted += cur.rowcount
        return inserted

    @staticmethod
    def _insert_products(cur: psycopg2.extensions.cursor, n: int) -> int:
        """Insert ``n`` tech/data products priced between R$29 and R$999."""
        inserted = 0
        for _ in range(n):
            category = random.choice(list(PRODUCT_CATALOG))
            prefixes, nouns = PRODUCT_CATALOG[category]
            name = f"{random.choice(prefixes)} {random.choice(nouns)}"
            price = Decimal(f"{random.randint(29, 998)}.90")  # R$29.90 – R$998.90
            sku = f"{category[:3].upper()}-{secrets.token_hex(3).upper()}"
            cur.execute(
                """
                INSERT INTO products (name, category, price, sku)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (sku) DO NOTHING
                """,
                (name, category, price, sku),
            )
            inserted += cur.rowcount
        return inserted

    @staticmethod
    def _insert_orders(cur: psycopg2.extensions.cursor, n: int) -> int:
        """Insert ``n`` orders for random existing customers/products (amount = price * qty)."""
        cur.execute("SELECT id FROM customers ORDER BY random() LIMIT %s", (n,))
        customer_ids = [row[0] for row in cur.fetchall()]
        cur.execute("SELECT id, price FROM products WHERE active ORDER BY random() LIMIT 50")
        products = cur.fetchall()
        if not customer_ids or not products:
            return 0
        for _ in range(n):
            product_id, price = random.choice(products)
            quantity = random.choices([1, 2, 3, 4, 5], weights=[60, 20, 10, 6, 4])[0]
            cur.execute(
                """
                INSERT INTO orders (customer_id, product_id, amount, quantity, status)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    random.choice(customer_ids),
                    product_id,
                    price * quantity,
                    quantity,
                    random.choices(*ORDER_STATUSES)[0],
                ),
            )
        return n


def _ascii(text: str) -> str:
    """Lowercase ``text`` and strip accents/non-alphanumerics for use in an email local part."""
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(ch for ch in normalized.lower() if ch.isalnum())


# ─── MongoDB (Events) ────────────────────────────────────────────────────────


class MongoWriter:
    """Writes pipeline ``event_logs`` and ``user_activity`` documents."""

    def __init__(self) -> None:
        """Create the client (connects lazily; pymongo reconnects on its own)."""
        self._client: MongoClient = MongoClient(
            host=MONGO_HOST, port=MONGO_PORT, serverSelectionTimeoutMS=5000, appname="data-generator"
        )
        self._db: Database = self._client[MONGO_DB]
        self._indexes_ready = False

    def close(self) -> None:
        """Close the client and its connection pool."""
        self._client.close()

    def _ensure_indexes(self) -> None:
        """Create query indexes and the 30-day TTL on ``event_logs`` (idempotent)."""
        if self._indexes_ready:
            return
        self._db.event_logs.create_index(
            [("timestamp", ASCENDING)], expireAfterSeconds=EVENT_LOG_TTL_SECONDS, name="ttl_timestamp"
        )
        self._db.event_logs.create_index([("pipeline_name", ASCENDING), ("status", ASCENDING)])
        self._db.user_activity.create_index([("user_id", ASCENDING), ("timestamp", ASCENDING)])
        self._indexes_ready = True

    def write_cycle(self) -> dict[str, int]:
        """Insert one cycle of events and activity; return counts per collection."""
        self._ensure_indexes()
        now = datetime.now(timezone.utc)
        events = [_event_log(now) for _ in range(random.randint(3, 8))]
        activity = [_user_activity(now) for _ in range(random.randint(5, 10))]
        self._db.event_logs.insert_many(events)
        self._db.user_activity.insert_many(activity)
        return {"event_logs": len(events), "user_activity": len(activity)}

    def pipeline_stats(self, day: date) -> list[dict[str, Any]]:
        """Aggregate today's pipeline runs per pipeline/status for the daily report."""
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        return list(
            self._db.event_logs.aggregate(
                [
                    {"$match": {"timestamp": {"$gte": start}}},
                    {
                        "$group": {
                            "_id": "$pipeline_name",
                            "runs": {"$sum": 1},
                            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
                            "warnings": {"$sum": {"$cond": [{"$eq": ["$status", "warning"]}, 1, 0]}},
                            "avg_duration_seconds": {"$avg": "$duration_seconds"},
                            "records_processed": {"$sum": "$records_processed"},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ]
            )
        )


def _event_log(now: datetime) -> dict[str, Any]:
    """Build one simulated pipeline run event."""
    pipeline = random.choice(list(PIPELINES))
    status = random.choices(*EVENT_STATUSES)[0]
    low, high = (10, 120) if PIPELINES[pipeline] == "hourly" else (30, 600)
    error: str | None = None
    if status == "failed":
        error = random.choice(PIPELINE_ERRORS)
    elif status == "warning":
        error = random.choice(PIPELINE_WARNINGS)
    return {
        "pipeline_name": pipeline,
        "run_id": f"run_{secrets.token_hex(6)}",
        "status": status,
        "error_message": error,
        "severity": SEVERITY_BY_STATUS[status],
        "duration_seconds": random.randint(low, high),
        "records_processed": 0 if status == "failed" else random.randint(1_000, 100_000),
        "timestamp": now,
    }


def _user_activity(now: datetime) -> dict[str, Any]:
    """Build one simulated user action with action-specific metadata."""
    action = random.choice(USER_ACTIONS)
    metadata: dict[str, Any]
    if action == "query_executed":
        metadata = {
            "table": random.choice(TABLES),
            "rows_returned": random.randint(0, 50_000),
            "execution_time_ms": random.randint(5, 8_000),
        }
    elif action == "dashboard_viewed":
        metadata = {"dashboard": random.choice(DASHBOARDS), "view_duration_seconds": random.randint(5, 900)}
    elif action == "export_requested":
        metadata = {
            "table": random.choice(TABLES),
            "format": random.choice(["csv", "parquet", "xlsx"]),
            "row_count": random.randint(100, 200_000),
        }
    elif action == "schema_browsed":
        metadata = {"table": random.choice(TABLES), "columns_viewed": random.randint(1, 12)}
    else:  # pipeline_triggered
        metadata = {"pipeline": random.choice(list(PIPELINES)), "trigger": random.choice(["manual", "backfill"])}
    return {
        "user_id": f"usr_{secrets.token_hex(4)}",
        "action": action,
        "metadata": metadata,
        "session_id": f"sess_{secrets.token_hex(4)}",
        "timestamp": now,
    }


# ─── SeaweedFS (Data Lake) ───────────────────────────────────────────────────


class ReportWriter:
    """Builds the daily summary CSV from Postgres + Mongo and uploads it to SeaweedFS."""

    def __init__(self, mongo: MongoWriter) -> None:
        """Create the S3 client (credentials come from AWS_* env vars)."""
        self._mongo = mongo
        self._s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
        )

    def close(self) -> None:
        """Release the S3 client's HTTP connections."""
        self._s3.close()

    def write_report(self) -> dict[str, Any]:
        """Upload ``reports/daily-summary-<today>.csv`` (overwritten as the day progresses)."""
        today = datetime.now(timezone.utc).date()
        rows = self._ledger_rows(today) + self._pipeline_rows(today)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["report_date", "section", "dimension", "metric", "value"])
        writer.writerows([today.isoformat(), *row] for row in rows)
        key = f"reports/daily-summary-{today.isoformat()}.csv"
        self._s3.put_object(
            Bucket=S3_BUCKET, Key=key, Body=buffer.getvalue().encode("utf-8"), ContentType="text/csv"
        )
        return {"key": key, "rows": len(rows)}

    @staticmethod
    def _ledger_rows(day: date) -> list[tuple[str, str, str, Any]]:
        """Today's order and signup metrics (short-lived read-only connection)."""
        rows: list[tuple[str, str, str, Any]] = []
        conn = psycopg2.connect(**POSTGRES_DSN)
        try:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT status, count(*), coalesce(sum(amount), 0)
                    FROM orders WHERE created_at::date = %s GROUP BY status ORDER BY status
                    """,
                    (day,),
                )
                for status, count, revenue in cur.fetchall():
                    rows.append(("orders", status, "order_count", count))
                    rows.append(("orders", status, "amount_brl", f"{revenue:.2f}"))
                cur.execute(
                    """
                    SELECT plan, count(*) FROM customers
                    WHERE created_at::date = %s GROUP BY plan ORDER BY plan
                    """,
                    (day,),
                )
                rows.extend(("customers", plan, "new_customers", count) for plan, count in cur.fetchall())
        finally:
            conn.close()
        return rows

    def _pipeline_rows(self, day: date) -> list[tuple[str, str, str, Any]]:
        """Today's per-pipeline run metrics from ``event_logs``."""
        rows: list[tuple[str, str, str, Any]] = []
        for stat in self._mongo.pipeline_stats(day):
            name = stat["_id"]
            rows.append(("pipelines", name, "runs", stat["runs"]))
            rows.append(("pipelines", name, "failed_runs", stat["failed"]))
            rows.append(("pipelines", name, "warning_runs", stat["warnings"]))
            rows.append(("pipelines", name, "avg_duration_seconds", round(stat["avg_duration_seconds"], 1)))
            rows.append(("pipelines", name, "records_processed", stat["records_processed"]))
        return rows


# ─── Orchestration ───────────────────────────────────────────────────────────


async def run_store(store: str, cycle: int, fn: Callable[[], Any], on_error: Callable[[], None] | None = None) -> bool:
    """Run one store's blocking writes in a thread with retries; log the outcome."""
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(with_retry, store, fn, on_error)
    except Exception as exc:  # noqa: BLE001 — one store failing must not stop the others
        log.error("store write failed after retries", extra={"store": store, "cycle": cycle, "error": repr(exc)})
        return False
    log.info(
        "records generated",
        extra={
            "store": store,
            "cycle": cycle,
            "records_generated": result,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return True


def install_signal_handlers(stop: asyncio.Event) -> None:
    """Set ``stop`` on SIGTERM/SIGINT (docker stop sends SIGTERM to PID 1)."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows: no loop signal handlers
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))


async def main() -> None:
    """Run generation cycles until a shutdown signal arrives, then close all connections."""
    stop = asyncio.Event()
    install_signal_handlers(stop)

    postgres = PostgresWriter()
    mongo = MongoWriter()
    reports = ReportWriter(mongo)
    log.info(
        "generator started",
        extra={"interval_s": INTERVAL_SECONDS, "postgres": POSTGRES_DSN["host"], "mongo": MONGO_HOST, "s3": S3_ENDPOINT},
    )

    cycle = 0
    try:
        while not stop.is_set():
            cycle += 1
            tasks = [
                run_store("postgres", cycle, lambda c=cycle: postgres.write_cycle(c), postgres.reset),
                run_store("mongodb", cycle, mongo.write_cycle),
            ]
            if cycle % REPORT_EVERY_N_CYCLES == 0:
                tasks.append(run_store("seaweedfs", cycle, reports.write_report))
            results = await asyncio.gather(*tasks)
            log.info("cycle finished", extra={"cycle": cycle, "stores_ok": sum(results), "stores_total": len(results)})

            try:  # sleep, but wake immediately on shutdown
                await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        log.info("shutting down, closing connections", extra={"cycles_completed": cycle})
        postgres.close()
        mongo.close()
        reports.close()
        log.info("generator stopped")


if __name__ == "__main__":
    asyncio.run(main())
