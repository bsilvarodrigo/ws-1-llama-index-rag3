# Data Pipeline SLA Definitions

| Field | Value |
|---|---|
| Document ID | OPS-SLA-001 |
| Version | 2.4 |
| Owner | team-platform (Data Reliability) |
| Last reviewed | 2025-09-15 |

## 1. Scope

This document defines the Service Level Agreements (SLAs) for the production data pipelines, how they are measured, and how breaches are escalated. Pipeline schedules and ownership match the lineage graph in Neo4j and the events written to `event_logs`.

## 2. Definitions

- **Uptime:** the percentage of scheduled runs in a calendar month that finish with status `success`. A run that fails and is successfully retried inside its latency window counts as a success.
- **Latency:** the time from the scheduled start to the moment the output table is fully written and readable.
- **Breach:** a run that fails without a successful retry, or that exceeds its maximum latency.
- **Error budget:** the number of failed or late runs allowed per month before the uptime target is missed.

## 3. Pipeline SLAs

| Pipeline | Owner | Schedule (cron, UTC-3) | Uptime target | Max latency | Reads from | Writes to | Downstream impact |
|---|---|---|---|---|---|---|---|
| `etl_billing_daily` | team-billing | `0 3 * * *` (daily 03:00) | **99.9%** | **45 min** | `orders` | `fact_revenue` | `analytics_revenue_agg`, Revenue Overview dashboard |
| `etl_orders_hourly` | team-billing | `0 * * * *` (hourly) | **99.5%** | **15 min** | `orders` | — | Pipeline Monitor dashboard |
| `etl_customer_sync` | team-platform | `0 6 * * *` (daily 06:00) | **99.0%** | **30 min** | `customers` | `dim_customers` | `analytics_revenue_agg`, Customer Health dashboard |
| `analytics_revenue_agg` | team-analytics | `0 5 * * *` (daily 05:00) | 99.0% | 60 min | `fact_revenue` | — | Revenue Overview dashboard |

### 3.1 etl_billing_daily

- **Uptime:** 99.9%. Being a daily job, any single missed run consumes most of the monthly error budget, so every failure is treated as at least P2.
- **Max latency:** 45 minutes. Output must be in `fact_revenue` by 03:45 so that `analytics_revenue_agg` and the finance morning report have fresh data.
- **Data quality checks:** row count in `fact_revenue` for the day must be within ±20% of the trailing 7-day average; `total_revenue` must not be negative.

### 3.2 etl_orders_hourly

- **Uptime:** 99.5% (about 3 failed runs allowed per month out of ~720).
- **Max latency:** 15 minutes after the top of the hour.
- **Data quality checks:** no order with `status` outside `pending`, `completed`, `failed`, `refunded`; no `amount` less than or equal to zero.
- Three consecutive failures escalate to P2 regardless of business impact.

### 3.3 etl_customer_sync

- **Uptime:** 99.0%.
- **Max latency:** 30 minutes.
- **Data quality checks:** `email` unique and not null in `dim_customers`; `plan` only `free`, `pro` or `enterprise`. This pipeline also runs the PII anonymization step required by the Data Retention Policy (POL-DATA-002), so a failure longer than 3 days must be reported to the DPO.

## 4. Severity Levels

| Severity | Definition | Examples |
|---|---|---|
| **P1 — Critical** | Revenue or customer-facing data is wrong or unavailable; multiple pipelines down | `etl_billing_daily` writing wrong values to `fact_revenue`; database outage |
| **P2 — High** | SLA breached on a critical pipeline; downstream dashboards stale | `etl_billing_daily` failed with no retry; 3 consecutive `etl_orders_hourly` failures |
| **P3 — Medium** | SLA at risk, or breach on a non-critical pipeline | `etl_customer_sync` late by 20 min; error budget more than 50% consumed |
| **P4 — Low** | No SLA impact; cosmetic or single transient failure recovered by retry | One retried run; warning-level data quality check |

## 5. Escalation Procedures

| Severity | Acknowledge within | First responder | Escalate to (if unresolved) | Escalate after | Status updates |
|---|---|---|---|---|---|
| **P1** | 5 min | On-call engineer of the owning team (paged) | Engineering Manager, then Head of Data | 30 min | Every 30 min in `#incidents` |
| **P2** | 15 min | On-call engineer of the owning team (paged) | Team lead of the owning team | 2 h | Every 2 h in the team channel |
| **P3** | 4 business hours | Owning team (ticket) | Team lead | 1 business day | Daily in the ticket |
| **P4** | 2 business days | Owning team (backlog) | — | — | In the ticket |

Owning team channels: team-billing `#billing-eng`, team-platform `#platform-eng`, team-analytics `#analytics`.

When a breach on one pipeline affects another team's pipeline or dashboard (see the lineage graph: `FEEDS` and `USED_BY` relationships), the owning team must notify the downstream team in its channel within the acknowledge window.

## 6. Reporting

- A monthly SLA report per pipeline (uptime, p95 latency, error budget consumed) is published by team-platform on the Pipeline Monitor dashboard.
- Every P1 and P2 requires a post-mortem within 5 business days (see the Incident Response Runbook).
- A pipeline that misses its uptime target two months in a row freezes new feature work for its owning team until a reliability plan is approved.
