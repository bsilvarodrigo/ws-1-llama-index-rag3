# Data Retention Policy

| Field | Value |
|---|---|
| Document ID | POL-DATA-002 |
| Version | 3.1 |
| Owner | team-platform (Data Governance) |
| Approved by | Data Protection Officer (DPO) |
| Effective date | 2025-03-01 |
| Review cycle | Every 12 months, or after any change in LGPD guidance from the ANPD |

## 1. Purpose

This policy defines how long the company keeps each class of data, how data is deleted when its retention period ends, and which team is accountable for each step. It applies to every data store in the DataOps platform: the PostgreSQL ledger (`customers`, `orders`, `products`), the analytics warehouse (`fact_revenue`, `dim_customers`), the MongoDB event stores (`event_logs`, `user_activity`), the Qdrant vector store, and the SeaweedFS data lake (`dataops-lake`).

Keeping data longer than needed increases legal exposure and storage cost. Deleting it too early breaks financial audits and tax obligations. This policy balances both.

## 2. Data Classification and Retention Periods

| Classification | Examples | Retention period | Starts counting from |
|---|---|---|---|
| **PII (Personal Identifiable Information)** | `customers.name`, `customers.email`, `user_activity.user_id`, session identifiers | **90 days** after the account is closed or the consent is revoked | Account closure or consent revocation date |
| **Transactional / financial** | `orders` (amount, status, quantity), `fact_revenue`, invoices | **7 years** | End of the fiscal year in which the transaction happened |
| **Operational logs** | `event_logs`, application logs, pipeline run logs | **30 days** | Event timestamp |
| **Aggregated analytics (no PII)** | `fact_revenue`, `reports/daily-summary-*.csv` | **7 years** | Report date |
| **Reference data** | `products`, data dictionary, pipeline topology in Neo4j | While active, plus **1 year** after deactivation | Deactivation date (`products.active = false`) |
| **Vector embeddings** | Qdrant collection `dataops-memory` | Same as the source document | Deletion of the source document |

Rules that apply to all classes:

- When a record mixes classifications, the **shortest** retention applies to the PII fields and the **longest** to the rest. For example, an order is kept for 7 years, but its link to an identifiable customer is pseudonymized after the customer's PII retention ends.
- Data under a **legal hold** (litigation, tax audit, ANPD investigation) is exempt from deletion until the hold is lifted by Legal.
- Backups follow the same periods. Backups older than 35 days are rotated out automatically, so deleted data disappears from backups within 35 days of its deletion in production.

## 3. Deletion Procedures

### 3.1 Automated deletion

1. **Operational logs:** a MongoDB TTL index on `event_logs.timestamp` removes documents older than 30 days. SeaweedFS lifecycle rules remove raw log objects older than 30 days.
2. **PII:** the `etl_customer_sync` pipeline runs a daily anonymization step. For customers whose account was closed more than 90 days ago, it replaces `name` with `"ANONYMIZED"` and `email` with a salted SHA-256 hash, keeping `customers.id` so that historical `orders` remain consistent.
3. **Vector store:** when a source document is deleted from SeaweedFS or a MongoDB collection, the ingestion pipeline deletes every Qdrant point whose `source` metadata matches it on the next ingestion run.

### 3.2 Manual deletion (data subject requests)

Under LGPD Art. 18, a data subject can request the deletion of their personal data.

1. Support logs the request in the privacy queue with the customer's email and a ticket ID.
2. team-platform verifies the requester's identity within **2 business days**.
3. team-platform runs the anonymization procedure for that `customers.id` and records the ticket ID in the audit table.
4. team-billing confirms that no open invoice or legal hold blocks the deletion. Financial records are kept but pseudonymized.
5. The DPO confirms completion to the data subject within **15 days** of the original request.

### 3.3 Evidence

Every deletion, automated or manual, writes an audit entry with: timestamp, data class, number of records affected, the pipeline or person that ran it, and the ticket ID when there is one. Audit entries are kept for 7 years.

## 4. Compliance Requirements (LGPD)

The policy implements the following obligations of the Lei Geral de Proteção de Dados (Lei nº 13.709/2018):

- **Purpose limitation and necessity (Art. 6):** personal data is kept only while it serves the purpose it was collected for.
- **End of processing (Art. 15 and 16):** personal data is deleted at the end of processing, except where retention is required by law (for example, tax and accounting records, which justify the 7-year retention of transactional data).
- **Data subject rights (Art. 18):** access, correction, anonymization and deletion requests are handled through the procedure in section 3.2.
- **Security and incident reporting (Art. 46 and 48):** incidents affecting personal data follow the incident response runbook, and the DPO notifies the ANPD and affected data subjects when there is relevant risk or damage.
- **Records of processing (Art. 37):** the data dictionary (`data-dictionary.csv`) flags every PII column (`pii_flag = true`) and is the inventory used in audits.

Non-compliance must be reported to the DPO within 24 hours of discovery.

## 5. Responsible Teams

| Team | Responsibilities | Contact |
|---|---|---|
| **team-platform** | Owns `customers` and the anonymization step in `etl_customer_sync`; runs data subject deletion requests; maintains TTL indexes and lifecycle rules | `#platform-eng` |
| **team-billing** | Owns `orders` and financial data; validates the 7-year retention and legal holds before any deletion that touches transactions | `#billing-eng` |
| **team-analytics** | Owns `fact_revenue` and derived reports; guarantees that aggregated data contains no PII | `#analytics` |
| **Data Protection Officer** | Approves this policy, answers data subjects, reports to the ANPD | dpo@company.example |
| **Legal** | Places and lifts legal holds | legal@company.example |

## 6. Exceptions and Review

Exceptions to this policy need written approval from the DPO and the owning team lead, must have an end date, and are reviewed every quarter. This policy is reviewed every 12 months by team-platform and the DPO.
