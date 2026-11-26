# Incident Response Runbook — Data Platform

| Field | Value |
|---|---|
| Document ID | OPS-RUN-003 |
| Version | 1.8 |
| Owner | team-platform |
| Applies to | All production pipelines, databases and dashboards of the data platform |

## 1. Severity Classification (P1–P4)

Classify the incident as soon as it is detected. When in doubt, pick the higher severity; downgrading later is cheap, under-reacting is not.

| Severity | Criteria | Response | Examples |
|---|---|---|---|
| **P1 — Critical** | Wrong or missing financial data visible to customers or leadership; data loss; personal data exposed; more than one critical pipeline down | Page on-call immediately, 24/7. Incident commander assigned. | `fact_revenue` overwritten with wrong totals; PostgreSQL ledger unavailable; leak of `customers.email` |
| **P2 — High** | SLA breach on `etl_billing_daily` or `etl_orders_hourly`; a dashboard used by leadership is stale | Page on-call, business hours and nights. | `etl_billing_daily` failed and retry failed; Revenue Overview not updated |
| **P3 — Medium** | SLA at risk or breach on a non-critical pipeline; degraded performance | Ticket, handled in business hours. | `etl_customer_sync` 20 min late; Qdrant search latency doubled |
| **P4 — Low** | No user impact | Backlog ticket. | One transient failure recovered by automatic retry |

Any incident that involves personal data (PII) is at least **P2** and must include the DPO, because LGPD may require notifying the ANPD.

## 2. Communication Channels

| Channel | Use |
|---|---|
| `#incidents` | Main channel for every P1/P2. One thread per incident, opened by the incident commander. |
| `#billing-eng` | team-billing: `etl_billing_daily`, `etl_orders_hourly`, `orders` |
| `#platform-eng` | team-platform: `etl_customer_sync`, `customers`, infrastructure |
| `#analytics` | team-analytics: `analytics_revenue_agg`, `fact_revenue`, dashboards |
| PagerDuty | Paging the on-call engineer for P1/P2 |
| Status page / email to stakeholders | P1 only, sent by the incident commander every 30 minutes |

Rules:

- The first message in `#incidents` states: severity, affected pipeline(s) and tables, start time, impact, and who is the incident commander.
- Use the lineage graph (Neo4j) to find every downstream pipeline (`FEEDS`) and dashboard (`USED_BY`) and notify their owning teams (`OWNS`).
- Never paste customer data or credentials in a channel.

## 3. Response Steps

1. **Detect and acknowledge.** Acknowledge the page or alert within the SLA window (P1: 5 min, P2: 15 min).
2. **Classify.** Assign a severity using section 1.
3. **Assemble.** For P1/P2, name an incident commander (IC) and a communications lead. The IC coordinates; they do not debug.
4. **Assess impact.** Check `event_logs` for the failing pipeline (`pipeline_name`, `status`, `error_message`, `severity`), the last successful run, and the affected tables and dashboards in the lineage graph.
5. **Contain.** Pause the failing pipeline and every downstream pipeline that reads its output, so bad data does not spread. Mark affected dashboards as stale.
6. **Mitigate.** Apply the fastest safe fix: retry, roll back (section 4), or switch to the last good snapshot.
7. **Verify.** Run the pipeline's data quality checks (see SLA Definitions, OPS-SLA-001) and compare row counts with the trailing 7-day average.
8. **Resolve and communicate.** Announce resolution in `#incidents`, re-enable downstream pipelines in lineage order, and open the post-mortem ticket.

## 4. Rollback Procedures

### 4.1 Pipeline code or configuration

1. Identify the last deployment before the incident start time.
2. Revert to the previous release tag and redeploy the pipeline.
3. Re-run the failed window(s) with the backfill command, one window at a time, oldest first.

### 4.2 Bad data written to a table

1. Pause the writer pipeline and all its consumers (for `fact_revenue`: `etl_billing_daily` and `analytics_revenue_agg`).
2. Identify the affected rows by load date or batch ID.
3. Delete the affected partition or rows inside a transaction, then re-run the writer pipeline for that window.
4. If the damage is wider than one partition, restore the table from the latest point-in-time backup into a staging schema, validate it, then swap it in.
5. Re-run downstream pipelines in lineage order (upstream first) and refresh the dashboards.

### 4.3 Schema change

1. Apply the down migration for the change.
2. If the down migration would lose data, restore the table from backup instead and replay transactions since the backup.
3. Changes to `customers` or `orders` schema always need approval from both team-platform and team-billing before rollback.

### 4.4 Vector store (Qdrant)

Rebuild the affected documents with the ingestion pipeline. Embeddings are derived data and can always be regenerated from SeaweedFS and MongoDB.

## 5. Post-Mortem Template

Required for every P1 and P2, published within **5 business days**. Post-mortems are blameless: they focus on systems and processes, not people.

```markdown
# Post-Mortem: <short title>

- **Incident ID:** INC-YYYY-NNN
- **Severity:** P1 / P2
- **Date / duration:** <start> – <end> (<total minutes>)
- **Incident commander:** <name>
- **Owning team:** team-billing / team-platform / team-analytics
- **Affected pipelines / tables / dashboards:** <list>

## Summary
Two or three sentences: what happened and what the impact was.

## Impact
- Users / customers affected:
- Data affected (tables, rows, time window):
- SLA impact (error budget consumed):
- Personal data involved? (yes/no; if yes, DPO notified at <time>)

## Timeline (UTC-3)
| Time | Event |
|---|---|
| HH:MM | Alert fired |
| HH:MM | Acknowledged |
| HH:MM | Mitigation applied |
| HH:MM | Resolved |

## Root Cause
What failed and why. Use "5 whys".

## Detection
How was it detected? Could it have been detected earlier?

## Resolution and Recovery
What was done to fix it (including rollbacks and backfills).

## What Went Well / What Went Wrong

## Action Items
| Action | Owner | Due date | Ticket |
|---|---|---|---|
```

## 6. On-Call Handover

At the end of each on-call shift, the outgoing engineer posts in the team channel: open incidents, paused pipelines, pending backfills, and any SLA at risk.
