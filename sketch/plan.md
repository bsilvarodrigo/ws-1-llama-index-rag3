# plan.md — Enterprise DataOps Knowledge Hub

Architecture decisions and the execution plan. Rules for the agent are in [CLAUDE.md](../CLAUDE.md), the course content in [docs/w1-rag-content.md](../docs/w1-rag-content.md), and the step-by-step tasks in [prompts/](../prompts/) (`1-…` to `14-…`).

---

## 1. Goal

A production-grade, multi-source RAG system. It answers business questions that span three data domains, and agents consume it through **FastAPI** and an **MCP Server**.

**Demo question (the "WOW moment"):**
> "Summarize the top customers by spend, their data retention requirements, and what systems would be impacted if the orders table went down."

It hits all three domains: Ledger (top customers) + Memory (retention policy) + Brain (impact of `orders`).

---

## 2. Architecture

```
                    ┌──────────────────────────┐
  Claude Code ───►  │  MCP Server (src/mcp)    │  stdio / HTTP client
                    └────────────┬─────────────┘
                                 │ HTTP
                    ┌────────────▼─────────────┐
                    │  FastAPI (src/api)       │  /api/v1/query · /health · /api/v1/ingest
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  RouterEngine            │  classify → decompose → parallel → synthesize
                    │  (src/engines/router.py) │  → SynthesizedResponse (Pydantic)
                    └──┬──────────┬─────────┬──┘
                       │          │         │
              ┌────────▼──┐ ┌─────▼────┐ ┌──▼────────┐
              │  Ledger   │ │  Memory  │ │  Brain    │
              │ Text2SQL  │ │  Vector  │ │  Cypher   │
              └────┬──────┘ └────┬─────┘ └────┬──────┘
                   │             │            │
              PostgreSQL       Qdrant       Neo4j
                                 ▲
                    IngestionPipeline (src/ingestion)
                       ▲                  ▲
                   SeaweedFS           MongoDB
                  (docs, reports)   (event_logs, user_activity)
                       ▲                  ▲
                       └── Data Generator ┘──► also writes PostgreSQL
```

### Domains

| Domain | Store | Retrieval | LlamaIndex component | Output model |
|---|---|---|---|---|
| **Ledger** | PostgreSQL 16 | Text-to-SQL | `NLSQLTableQueryEngine` | `LedgerQueryResult` |
| **Memory** | Qdrant | Vector similarity | `VectorStoreIndex` | `MemoryQueryResult` |
| **Brain** | Neo4j 5 (APOC) | Graph traversal | `PropertyGraphIndex` / Text-to-Cypher | `BrainQueryResult` |

Supporting sources: **MongoDB** (pipeline events and user activity) and **SeaweedFS** (policies, runbooks, dictionaries, reports). Both feed **Memory** through the ingestion pipeline.

---

## 3. Data Model

### Ledger — PostgreSQL (`dataops` DB)

| Table | Key columns |
|---|---|
| `customers` | id, name, email, plan (`free`/`pro`/`enterprise`), company, created_at |
| `products` | id, name, category, price, sku, active, created_at |
| `orders` | id, customer_id → customers, product_id → products, amount, quantity, status (`pending`/`completed`/`failed`/`refunded`), created_at |

### Brain — Neo4j

- **Nodes:** `Team` (3), `Table` (5: `customers`, `orders`, `products`, `fact_revenue`, `dim_customers`), `Pipeline` (4: `etl_billing_daily`, `etl_orders_hourly`, `etl_customer_sync`, `analytics_revenue_agg`), `Dashboard` (3).
- **Relationships:** `READS_FROM`, `WRITES_TO`, `FEEDS`, `USED_BY`, `OWNS`.

### MongoDB (`dataops` DB)

- `event_logs`: pipeline_name (one of the 4 Neo4j pipelines), status, error_message, severity, duration_seconds, records_processed, timestamp.
- `user_activity`: user_id, action, metadata, session_id, timestamp.

### SeaweedFS (bucket `dataops-lake`)

- Seed docs from `infra/docs/`: data retention policy, SLA definitions, incident response runbook, `data-dictionary.csv`.
- `reports/daily-summary-YYYY-MM-DD.csv`, written by the generator.

### Qdrant

- Collection `dataops-memory`: chunks of the SeaweedFS docs and Mongo logs. Metadata: `source_type`, file/collection, title, summary, keywords, questions answered.

**How the domains connect:** table names (`orders`, `customers`) and pipeline names are shared across Postgres, Neo4j and `event_logs`. That shared vocabulary is what makes cross-domain answers possible.

---

## 4. Key Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Ledger uses Text-to-SQL only, never vectors | Numbers must be exact, not "similar" |
| D2 | Semantic chunking (`SemanticSplitterNodeParser`), with `SentenceSplitter` as fallback | Chunks follow meaning; the fallback covers very short docs |
| D3 | Ingestion enrichment: Title, Summary, Keyword and QuestionsAnswered extractors | Lets retrieval match on intent, not just keywords |
| D4 | **Custom `RouterEngine`** (LLM classify → decompose → `asyncio.gather` → synthesize) instead of a bare `SubQuestionQueryEngine` | Full transparency (which engine, which sub-question, why) and control over partial failures (prompt 09) |
| D5 | Every engine output is a Pydantic model; the final output is `SynthesizedResponse` → `QueryResponse` | Contracts downstream systems can rely on |
| D6 | `sources` filter on a request skips classification | Lets callers force routing; useful for debugging and tests |
| D7 | One engine failing never fails the whole query: return partial results with a note | Resilience |
| D8 | MCP Server talks to FastAPI over HTTP, never imports the engines | Same contract locally and in production |
| D9 | Generator runs as its own container (with its own `requirements.txt`) | Isolated from the app's dependencies |
| D10 | Production target: **DigitalOcean Droplet** + Docker Compose (`restart: always`) | Defined in prompt 14; replaces the course's Railway option |
| D11 | LLM and embedding: OpenAI (`LLM_MODEL`, `EMBEDDING_MODEL` from env) | Matches the prompts; model names are never hardcoded |

---

## 5. Directory Layout

```
.
├── CLAUDE.md
├── docker-compose.yml               # local stack (P03+)
├── docker-compose.production.yml   # production (P14)
├── Dockerfile                       # API image (P10)
├── mcp-config.json                  # Claude Code MCP config, local + prod (P11/P14)
├── .env.example / .env.production.example
├── pyproject.toml · Makefile · README.md
├── docs/                            # reference docs
├── sketch/plan.md                   # this file
├── prompts/                         # numbered tasks
├── infra/
│   ├── scripts/                     # init-databases.sql, init-neo4j.cypher, init-seaweedfs.sh, seed-neo4j.sh
│   └── docs/                        # seed documents for SeaweedFS
├── generator/                       # main.py, Dockerfile, requirements.txt
├── scripts/                         # deploy.sh, deploy-digitalocean.sh, destroy-digitalocean.sh
├── src/
│   ├── schemas/                     # domain.py, query.py, api.py
│   ├── ingestion/                   # config.py, readers.py, pipeline.py, run.py
│   ├── engines/                     # config.py, ledger.py, memory.py, brain.py, router.py
│   ├── api/                         # app.py, main.py, routes/{query,health,ingest}.py
│   └── mcp/                         # server.py, run.py
└── tests/
    ├── conftest.py
    ├── unit/                        # no infra: schemas, router classification (mocked)
    └── integration/                 # full stack: engines, router, API
```

---

## 6. Execution Plan

Run one prompt at a time. Move on only when its **Validation** checklist passes and the developer confirms.

| # | Prompt | Phase | Main deliverables | Exit criterion |
|---|---|---|---|---|
| 01 | `1-build-claude-memory` | Contract | `CLAUDE.md` | 10 sections present ✅ |
| — | — | Plan | `sketch/plan.md` | This file ✅ |
| 02 | `2-build-scaffold` | Scaffold | Directories, `pyproject.toml`, `.env.example`, `Makefile`, `README.md` | Structure exists, Makefile syntax valid |
| 03 | `3-build-docker-compose` | Infra | `docker-compose.yml` with 5 services | All services healthy within 60s |
| 04 | `4-build-init-scripts` | Infra | Postgres DDL, Neo4j seed graph, SeaweedFS bucket and seed docs | 3 tables, graph nodes, bucket with docs |
| 05 | `5-build-data-generator` | Data | `generator/` container (30s cycles) | Counts grow in Postgres and Mongo; reports in S3 |
| 06 | `6-build-pydantic-schemas` | Contracts | `src/schemas/{domain,query,api}.py` | Imports clean, enums used, no circular imports |
| 07 | `7-build-ingestion-pipeline` | Memory | Readers + `IngestionPipeline` → Qdrant | `dataops-memory` has points with enriched metadata |
| 08 | `8-build-query-engines` | Engines | `LedgerEngine`, `MemoryEngine`, `BrainEngine` | Each returns its typed result on its own |
| 09 | `9-build-router-engine` | Routing | `RouterEngine` | Single, multi and forced routing work; the WOW question uses all 3 engines in under 60s |
| 10 | `10-build-fastapi` | Serving | API app, routes, `Dockerfile`, compose service | `/health`, `/api/v1/query`, `/api/v1/ingest` (202) and `/docs` work |
| 11 | `11-build-mcp-server` | Agents | MCP server with 3 tools + `mcp-config.json` | Claude Code discovers and uses the tools |
| 12 | `12-build-tests` | Quality | Unit + integration suites | `make test` (no infra) and `make test-integration` both green |
| 13 | `13-run-dev` | Validation | Full local run-through | All success criteria; cross-domain query under 30s |
| 14 | `14-build-deploy` | Production | DigitalOcean scripts, prod compose, prod MCP entry | Public API healthy; MCP points to production |

**Grand finale:** Claude Code → MCP (production) → FastAPI → RouterEngine → Ledger + Memory + Brain → validated `QueryResponse`.

---

## 7. Environment Variables

| Group | Variables |
|---|---|
| LLM | `OPENAI_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL` |
| PostgreSQL | `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` |
| MongoDB | `MONGO_HOST`, `MONGO_PORT`, `MONGO_DB` |
| Qdrant | `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_COLLECTION` |
| Neo4j | `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` |
| SeaweedFS | `SEAWEEDFS_HOST`, `SEAWEEDFS_PORT`, `SEAWEEDFS_BUCKET` |
| Generator | `GENERATOR_INTERVAL_SECONDS` |
| MCP | `API_BASE_URL` |

---

## 8. Inconsistencies Found Between Prompts

| # | Where | Conflict | Status |
|---|---|---|---|
| I1 | P01/CLAUDE.md vs P09 | `SubQuestionQueryEngine` required vs custom router | ✅ Fixed: P01 and CLAUDE.md now follow D4 |
| I2 | P01/P11/CLAUDE.md vs P14 | Railway vs DigitalOcean | ✅ Fixed: everything points to DigitalOcean (D10) |
| I3 | P13 | Bucket `dataops-docs` | ✅ Fixed: `dataops-lake` everywhere |
| I4 | P13 | Collection `dataops_memory` | ✅ Fixed: `dataops-memory` everywhere |
| I5 | P13 | Seed doc names differ from P04 | ✅ Fixed: P13 uses the P04 names |
| I6 | P02 `.env.example` | Missing `EMBEDDING_MODEL`, `QDRANT_COLLECTION`, S3 credentials, `GENERATOR_INTERVAL_SECONDS`, `API_BASE_URL` | ✅ Fixed: added in P02 |
| I7 | P06 `HealthResponse` | `services` didn't list MongoDB | ✅ Fixed |
| I8 | P10 vs CLAUDE.md naming | Routes are `/api/v1/query` (versioned) | Accepted: still kebab-case; `/health` stays unversioned |
| I9 | P05 vs CLAUDE.md | Generator uses `requirements.txt` | Accepted exception (D9): separate container |
| I10 | P07/P08 configs | Model names as code defaults | Accepted: defaults only; real values come from env |
| I11 | P04 vs P14 | One `init` service vs `init-neo4j` + `init-seaweedfs` | ✅ Fixed: P04 defines two services (no single image has both `cypher-shell` and the AWS CLI) |
| I12 | P04 | SeaweedFS init didn't upload the seed docs; Neo4j seed wasn't idempotent | ✅ Fixed: `s3 sync` upload; seed skips if already seeded |
| I13 | P03/P04 | Neo4j password hardcoded (`dataops123`) | ✅ Fixed: read from `.env` (production uses a different password) |
| I14 | P05/P10 | Containers used `.env` hosts (`localhost`), unreachable inside Docker | ✅ Fixed: service-name overrides in `environment:` |
| I15 | P14 | "Updated" files no prompt creates; `docker-compose.production.yml` never specified; hardcoded third-party repo URL; missing `seed-and-ingest.sh` | ✅ Fixed: files are created in P14, prod compose spec added, `REPO_URL` from git origin, `prod-ingest` inlined |

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| LlamaIndex API drift (e.g. `KnowledgeGraphQueryEngine` deprecated) | Check current docs before coding; prefer `PropertyGraphIndex`; pin versions |
| LLM generates destructive SQL/Cypher | Read-only DB users; allow only `SELECT` and read-only Cypher |
| Ingestion cost and latency (4 LLM extractors per chunk) | Ingest only the last 24h of logs; small model for extraction; dedup with docstore |
| Misrouting between domains | Rich router prompt with examples; routing tests in P12 |
| Cross-domain latency over 30s | Run engines in parallel with `asyncio.gather`; per-engine timeouts |
| Flaky tests because of the LLM | Unit tests mock the LLM; integration tests assert types and shape, not exact text |

## 10. Out of Scope (W01)

Frontend/UI · LangChain · ChromaDB · proactive data-quality agent (W02) · multi-agent metadata docs (W03) · no-code interface (W06).
