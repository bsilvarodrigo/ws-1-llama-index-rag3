# CLAUDE.md — Enterprise DataOps Knowledge Hub

The contract between the developer and Claude Code for this project. Read it before every task.

## 1. Project Overview

The **DataOps Knowledge Hub** is a production-grade, multi-source RAG system. It answers complex business questions by pulling context from structured, semi-structured and unstructured company data. LlamaIndex routes each question to the right data store, and Pydantic enforces strict contracts on every input and output.

Knowledge is split into three domains:

- **Ledger (PostgreSQL):** the numeric truth (customers, orders, products), queried with Text-to-SQL. No vectors.
- **Memory (Qdrant):** historical context (documents, policies, event logs), queried with semantic vector search.
- **Brain (Neo4j):** connections (lineage, dependencies, ownership, impact), queried with graph traversal.

Supporting sources: **MongoDB** (`event_logs`, `user_activity`) and **SeaweedFS** (S3-compatible data lake with PDFs and CSVs) feed the ingestion.

**End goal:** a RAG system served through a **FastAPI** `/query` endpoint and wrapped as an **MCP Server**, so AI agents (including Claude Code) can consume it in production on a DigitalOcean Droplet.

## 2. Tech Stack

| Component | Version | Role |
|---|---|---|
| Python | 3.11+ | Runtime |
| LlamaIndex | 0.12+ | Orchestration: ingestion, retrieval, routing |
| Pydantic | v2 | Contracts, settings, structured LLM output |
| FastAPI | 0.115+ | API serving |
| PostgreSQL | 16 | Ledger |
| Qdrant | 1.12+ | Memory (vector store) |
| Neo4j | 5.x (with APOC) | Brain (property graph) |
| MongoDB | 7 | Event and activity logs (source) |
| SeaweedFS | latest (S3 gateway) | Object storage / data lake |
| Docker Compose | v2 | Local infrastructure |
| DigitalOcean Droplet + Docker Compose | `doctl` CLI | Production deploy |

## 3. Architecture Rules

1. **All LLM outputs MUST be validated through Pydantic models.** Never pass raw string responses from an LLM downstream. Use LlamaIndex structured output (`output_cls`, structured LLMs, Pydantic programs).
2. **Every query engine returns a typed response:** a Pydantic model, never a bare `str` or an untyped `Response`.
3. **Cross-domain queries go through the custom `RouterEngine`** (`src/engines/router.py`): the LLM classifies and decomposes the question, the sub-questions run in parallel against Ledger/Memory/Brain, and the results are synthesized into a `SynthesizedResponse`. It was chosen over a bare `SubQuestionQueryEngine` for transparency (which engine, which sub-question) and partial-failure control. See `sketch/plan.md` D4.
4. **Ingestion uses `IngestionPipeline`.** No manual node creation or ad-hoc `index.insert` loops.
5. **Semantic chunking over fixed-size chunking:** `SemanticSplitterNodeParser` by default. `SentenceSplitter` is allowed only as an explicit fallback.
6. **Every API endpoint has request and response Pydantic models.** No `dict` or `Any` in route signatures.
7. **Domain boundaries are strict:** Ledger = SQL (read-only, `SELECT` only), Memory = vectors, Brain = Cypher (read-only).
8. **Traceability:** every answer carries its sources, its sub-questions, and the generated SQL/Cypher when there is any.

## 4. Code Standards

- Python **3.11+**.
- **`async/await` throughout:** async FastAPI routes and the LlamaIndex async methods (`aquery`, `arun`, `aretrieve`).
- **Type hints** on all functions, arguments and return values.
- **Docstrings** on all public functions, classes and modules.
- **`pyproject.toml`** for dependency management. No `requirements.txt`. The one exception is the standalone `generator/` container (see `sketch/plan.md` D9).
- **`src/` layout** (see section 5).
- Configuration comes from environment variables loaded from `.env`. **Never hardcode secrets**, and keep `.env.example` up to date.

## 5. Project Structure

Source of truth: `sketch/plan.md` (section 5). The `(P0X)` tags show which prompt creates each item.

```
.
├── CLAUDE.md
├── docker-compose.yml               # local stack (P03+)
├── docker-compose.production.yml   # production (P14)
├── Dockerfile                       # API image (P10)
├── mcp-config.json                  # Claude Code MCP config, local + prod (P11/P14)
├── .env.example / .env.production.example
├── pyproject.toml · Makefile · README.md
├── docs/                            # Reference documentation (course content)
├── sketch/plan.md                   # Architecture decisions + execution plan
├── prompts/                         # Numbered task prompts (1-… to 14-…)
├── infra/
│   ├── scripts/                     # init-databases.sql, init-neo4j.cypher, init-seaweedfs.sh, seed-neo4j.sh
│   └── docs/                        # Seed documents uploaded to SeaweedFS
├── generator/                       # Faker data generator: main.py, Dockerfile, requirements.txt
├── scripts/                         # deploy.sh, deploy-digitalocean.sh, destroy-digitalocean.sh
├── src/
│   ├── schemas/                     # domain.py, query.py, api.py (Pydantic contracts)
│   ├── ingestion/                   # config.py, readers.py, pipeline.py, run.py
│   ├── engines/                     # config.py, ledger.py, memory.py, brain.py, router.py
│   ├── api/                         # app.py, main.py, routes/{query,health,ingest}.py
│   └── mcp/                         # server.py, run.py
└── tests/
    ├── conftest.py
    ├── unit/                        # No infra needed (LLM and DBs mocked)
    └── integration/                 # Full stack: engines, router, API
```

## 6. Naming Conventions

| Item | Convention | Example |
|---|---|---|
| Files and modules | `snake_case.py` | `ledger_engine.py` |
| Classes | `PascalCase` | `LedgerEngine` |
| Pydantic models | Descriptive, ending in `Request`, `Response`, `Config` or a domain name | `QueryRequest`, `QueryResponse`, `Neo4jConfig`, `Customer` |
| API routes | `/kebab-case` | `/query`, `/health`, `/sub-questions` |
| Docker services | `kebab-case` | `postgres`, `seaweedfs-s3`, `knowledge-api` |
| Env vars | `UPPER_SNAKE_CASE` | `NEO4J_URI`, `LLM_MODEL` |

## 7. Testing

- Use **`pytest`** with **`pytest-asyncio`** for async code.
- **Test each engine on its own before integration:** Ledger, Memory and Brain each get their own test module.
- Unit tests use mocked LLMs and embeddings. In-memory vector stores are allowed **only in tests**.
- **End-to-end test:** a cross-domain query against the running stack must return a valid `QueryResponse` (it validates through Pydantic, with sources from more than one domain).
- Run the tests with `make test`.

## 8. Key References

- **`sketch/plan.md`:** architecture decisions and the execution plan.
- **`docs/`:** reference documentation (`w1-rag-content.md` is the course content).
- **LlamaIndex docs:** https://docs.llamaindex.ai. Check the current API, for example through the context7 MCP, before using any LlamaIndex, Pydantic, FastAPI or MCP API. These APIs change often, so don't rely on memory.

## 9. Workflow

1. Read the current task from the **`prompts/`** folder. Tasks are numbered in order (`1-…`, `2-…`, …).
2. Run **one task at a time**, following its Objective and Requirements.
3. Check the task's **Validation** checklist before calling it done.
4. When a task is done, **summarize what was done**: files created or changed, decisions made, and anything left open.
5. **Do NOT move to the next task without the developer's confirmation.**

## 10. Constraints

- **Do NOT use LangChain.** This project uses LlamaIndex only.
- **Do NOT use ChromaDB.** Use Qdrant.
- **Do NOT use in-memory vector stores in production code.** They are allowed in tests only.
- **Do NOT hardcode model names.** Read them from env vars (`LLM_MODEL`, `EMBEDDING_MODEL`).
- **Do NOT create a frontend or UI.** The project is API-only for now.
