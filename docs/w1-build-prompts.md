# W01 — Build Prompts (Enterprise DataOps Knowledge Hub)

Step-by-step prompts for building the system described in [w1-rag-content.md](w1-rag-content.md) with Claude Code.
Run them in order, one per session or one after another. Each one ends with a **Done when** checklist. Check it before moving on.

> **Tip:** Every prompt asks Claude Code to look up the current LlamaIndex, Pydantic, FastAPI and MCP APIs with the context7 MCP before writing code. These APIs change often, and doing this follows the course's "Knowledge Bases" advice (§5.2).

---

## Step 0 — Project Contract (`CLAUDE.md`, `plan.md`, `tasks.md`)

```text
Read docs/w1-rag-content.md completely. We are going to build the "Enterprise DataOps Knowledge Hub" it describes.

Create three control files at the repo root:

1. CLAUDE.md — the project contract:
   - Stack: Python 3.12, uv for dependencies, LlamaIndex (latest), Pydantic v2, FastAPI,
     PostgreSQL (Ledger), Qdrant (Memory), Neo4j (Brain), MongoDB (event logs), SeaweedFS (S3 data lake),
     Docker Compose locally, Railway for production.
   - Architecture rules: Ledger = Text-to-SQL only (no vectors); Memory = vector search; Brain = graph traversal.
     All config through pydantic-settings and a .env file. Never hardcode secrets.
     LLM and embedding providers must be configurable through env vars.
     Every API input/output is a Pydantic model. Type hints everywhere. Use ruff for lint and format, and pytest for tests.
   - Layout: src/knowledge_hub/{config,generator,ingestion,engines,api,mcp}/, tests/, infra/.
   - Workflow rule: before using any LlamaIndex, Pydantic, FastAPI or MCP API, check the current docs with the context7 MCP.
2. plan.md — the phases: Infra → Data Generator → Ingestion (Ledger/Memory/Brain) → Query Engine →
   Pydantic contracts → FastAPI → Validation → MCP Server → Deploy.
3. tasks.md — a numbered checklist of concrete tasks for each phase, with a short acceptance criterion for each task.

Do not write application code yet. Show me the three files when they're done.
```

**Done when:** you have reviewed the three files and edited them to match your preferences (for example, which LLM provider to use).

---

## Step 1 — Infrastructure (`docker-compose.yml`)

```text
Follow CLAUDE.md and tasks.md, phase "Infra".

Create infra/docker-compose.yml with these services. Each one needs a named volume, a healthcheck, and ports set from .env:
- postgres (16) — database "ledger"
- mongodb (7) — database "events"
- qdrant (latest) — HTTP 6333
- neo4j (5, community) with the APOC plugin enabled (PropertyGraphIndex needs it)
- seaweedfs running master + volume + filer + s3 gateway (S3 API on 8333), with an S3 config file holding an access key and secret key

Also create:
- .env.example with every variable (hosts, ports, users, passwords, S3 keys, bucket names, LLM and embedding keys and model names)
- .gitignore entries for .env and the data volumes
- a Makefile with: up, down, logs, ps, reset (this one deletes the volumes and asks for confirmation first)
- scripts/healthcheck.py, which connects to all 5 services and prints OK or FAIL for each one

Start the stack and run the healthcheck.
```

**Done when:** `make up` works and the healthcheck prints 5× OK.

---

## Step 2 — Python Skeleton and Config

```text
Follow CLAUDE.md, phase "Infra" (application part).

- Initialize the project with uv (pyproject.toml). Add the dependencies we need: llama-index-core and the integrations
  (vector-stores-qdrant, graph-stores-neo4j, readers for S3 and MongoDB, llms and embeddings for the configured provider),
  pydantic, pydantic-settings, fastapi, uvicorn, sqlalchemy, psycopg, pymongo, boto3, faker, reportlab, mcp, pytest, ruff.
  Look up the current package names with context7.
- src/knowledge_hub/config/settings.py: a single Settings(BaseSettings) class with nested groups
  (postgres, mongo, qdrant, neo4j, s3, llm). Add a get_settings() function cached with lru_cache.
- src/knowledge_hub/config/llm.py: sets the global LlamaIndex Settings.llm and Settings.embed_model
  based on the provider chosen in config.
- tests/test_settings.py: checks that Settings loads from a temporary env.
```

**Done when:** `uv run pytest` passes and `uv run ruff check .` is clean.

---

## Step 3 — Data Generator (simulates a live company)

```text
Follow CLAUDE.md, phase "Data Generator". Create src/knowledge_hub/generator/.

Design one coherent fake company (a SaaS data platform). Every source must share the same IDs, so that
questions spanning several domains have real answers.

1. Postgres (Ledger), using SQLAlchemy models + migrations or create_all:
   - customers(id, name, email, plan [free|pro|enterprise], region, created_at)
   - products(id, name, category, price)
   - orders(id, customer_id FK, product_id FK, quantity, total, status, created_at)
   Add column comments, because NLSQLTableQueryEngine uses them as context.
2. MongoDB:
   - event_logs: pipeline/system events (service, severity, message, customer_id, pipeline_id, timestamp),
     including realistic incidents
   - user_activity: logins and feature usage per customer_id
3. SeaweedFS (S3 bucket "docs"):
   - 5–10 PDF manuals and policies made with reportlab (SLA policy per plan, incident runbook, data retention policy, ...)
   - CSV data dictionaries that describe each Postgres table and Mongo collection
4. Neo4j (Brain) seed data: a lineage graph. Nodes: Source, Pipeline, Table, Dashboard, Team, Customer.
   Relationships: READS_FROM, WRITES_TO, FEEDS, OWNS, DEPENDS_ON, SERVES. Use the same pipeline_ids as event_logs.

Entry points:
- `python -m knowledge_hub.generator seed`: one-time bulk load (idempotent)
- `python -m knowledge_hub.generator run --interval 5`: continuous loop that inserts new orders,
  events and incidents (this is the live operation)
Add a generator service to docker-compose that runs the loop.
Use Pydantic models for every generated record, so we validate the data before writing it.
```

**Done when:** after seeding, every store has data, the loop keeps adding rows, and one customer_id can be traced through Postgres, Mongo and Neo4j.

---

## Step 4a — Ingestion: Ledger (Postgres)

```text
Follow CLAUDE.md, phase "Ingestion — Ledger". Check the current LlamaIndex APIs with context7.

src/knowledge_hub/ingestion/ledger.py:
- Build a LlamaIndex SQLDatabase over customers, orders and products (include_tables restricted to these tables).
- Add rich table descriptions using SQLTableSchema / table context strings, taken from the column comments
  and the CSV data dictionaries in S3.
- Expose build_ledger_engine() -> NLSQLTableQueryEngine.
- Safety: connect with a read-only Postgres role (add it to the init SQL in infra/), and reject non-SELECT SQL.
- tests: a numeric question ("how many enterprise customers are there?") must return the same number as a direct SQL count.
```

**Done when:** the test passes, and the generated SQL can be seen in the response metadata.

---

## Step 4b — Ingestion: Memory (Qdrant)

```text
Follow CLAUDE.md, phase "Ingestion — Memory". Check the current LlamaIndex APIs with context7.

src/knowledge_hub/ingestion/memory.py:
- Load the PDFs and CSVs from SeaweedFS with the LlamaIndex S3 reader (custom endpoint_url pointing to SeaweedFS).
- Load Mongo event_logs with the Mongo reader. Infer a Pydantic schema for the event documents and validate
  them before converting them to Documents. Keep customer_id, pipeline_id, severity and timestamp as metadata.
- IngestionPipeline with: SentenceSplitter (or SemanticSplitterNodeParser, configurable) → TitleExtractor →
  QuestionsAnsweredExtractor → embedding model. Use QdrantVectorStore as the vector_store
  and a docstore for deduplication (re-running must not duplicate chunks).
- Two collections: "docs" (PDF/CSV) and "events" (logs).
- CLI: `python -m knowledge_hub.ingestion memory [--incremental]`
- Expose build_memory_engine() -> query engine over the VectorStoreIndex, with metadata filters supported.
- tests: a policy question returns chunks from the SLA PDF, and source_nodes include the file name.
```

**Done when:** Qdrant has both collections, and running the ingestion twice keeps the same point count.

---

## Step 4c — Ingestion: Brain (Neo4j)

```text
Follow CLAUDE.md, phase "Ingestion — Brain". Check the current LlamaIndex APIs with context7.

src/knowledge_hub/ingestion/brain.py:
- Connect PropertyGraphIndex to Neo4jPropertyGraphStore over the lineage graph made by the seed.
- Use the existing graph, so don't re-extract what's already structured. Also add a SchemaLLMPathExtractor
  limited to our entity and relationship types, so that relationships mentioned in the runbook PDFs get added too.
- Retrievers: TextToCypherRetriever (read-only Cypher, validated against the schema) + a vector/synonym retriever.
- Expose build_brain_engine().
- tests: "which dashboards are impacted if pipeline X fails?" returns the same nodes as a hand-written Cypher query.
```

**Done when:** the impact-analysis test passes.

---

## Step 5 — Cross-Domain Query Engine

```text
Follow CLAUDE.md, phase "Query Engine". Check the current LlamaIndex APIs with context7.

src/knowledge_hub/engines/hub.py:
- Wrap the three engines as QueryEngineTools with precise descriptions:
  ledger = numbers, counts, revenue, plans, orders (SQL facts)
  memory = policies, manuals, logs, incident descriptions (semantic text)
  brain  = dependencies, lineage, ownership, impact (graph)
- Build a SubQuestionQueryEngine over these tools for cross-domain questions, and a RouterQueryEngine
  (LLMSingleSelector) for single-domain questions. Then build a top-level dispatcher.
  Implement the dispatcher as a LlamaIndex Workflow with @step:
  classify → route or decompose → retrieve → synthesize (tree_summarize) → validate.
- Every response must keep: the sub-questions, which tool answered each one, the source nodes, and the generated SQL/Cypher.
- CLI: `python -m knowledge_hub.engines ask "<question>"` prints the answer and the reasoning trace.
```

**Done when:** "Which enterprise customers had critical incidents last week, and which dashboards did those incidents affect?" produces sub-questions that hit all three tools.

---

## Step 6 — Pydantic Contracts (Structured Output)

```text
Follow CLAUDE.md, phase "Pydantic contracts".

src/knowledge_hub/api/schemas.py (Pydantic v2):
- QueryRequest: question (str, 3–2000 chars), mode (auto|ledger|memory|brain|cross), top_k (1–20, default 5),
  filters (optional dict), include_trace (bool)
- SourceRef: domain (Literal ledger|memory|brain), source_id, title, snippet, score (optional), metadata
- SubQuestion: question, tool, answer
- QueryResponse: answer, confidence (0–1), domains_used, sub_questions, sources, sql (optional), cypher (optional),
  latency_ms, request_id
- HealthResponse: status plus the status of each dependency

Make the final synthesis step return a typed model using LlamaIndex structured output
(output_cls / structured LLM), so the answer is valid JSON every time. If validation fails, retry once.
Then return an error response with a clear message.
tests: schema tests, plus a test with a malformed LLM output.
```

**Done when:** every engine path returns a valid `QueryResponse`.

---

## Step 7 — FastAPI Serving

```text
Follow CLAUDE.md, phase "FastAPI".

src/knowledge_hub/api/main.py:
- Lifespan: build the engines once at startup and keep them on app.state.
- POST /query (QueryRequest → QueryResponse), GET /health (checks all 5 stores), GET /schema (lists the tools and their descriptions).
- Optional API-key auth through the X-API-Key header (key set in Settings).
- Structured JSON logging that includes request_id and latency. Global exception handlers return Pydantic error models.
- Add a timeout on /query.
- Dockerfile (multi-stage, uv) and an "api" service in docker-compose.
- tests with httpx/TestClient that mock the hub.
```

**Done when:** `curl -X POST localhost:8000/query ...` returns a valid response, and `/docs` shows the contracts.

---

## Step 8 — Validation (Cross Tests)

```text
Follow CLAUDE.md, phase "Validation".

- tests/e2e/: questions with known answers, computed directly from the databases after a seed with a fixed Faker seed:
  ledger-only, memory-only, brain-only, and 3 cross-domain questions.
- scripts/eval.py: runs a golden set (eval/questions.yaml), records which domains were used, latency and correctness,
  and prints a table. If the LlamaIndex evaluators fit, use them (FaithfulnessEvaluator, RelevancyEvaluator).
- Fix any routing mistakes you find by improving the tool descriptions and prompts, not by hardcoding.
```

**Done when:** the e2e tests are green and the eval table shows correct routing for every question.

---

## Step 9 — MCP Server

```text
Follow CLAUDE.md, phase "MCP Server". Check the current MCP Python SDK (FastMCP) with context7.

src/knowledge_hub/mcp/server.py:
- An MCP server that calls the FastAPI API over HTTP (it does not import the engines directly).
  Base URL and API key come from env.
- Tools: query_knowledge_hub(question, mode="auto"), with a rich description of the 3 domains;
  get_hub_health(); list_domains().
- Return the QueryResponse as structured content, plus a readable text summary.
- Support stdio (local) and streamable HTTP (remote) transports.
- Add a .mcp.json example for connecting Claude Code to it locally, and a README section on how to use it.
```

**Done when:** in Claude Code, `/mcp` lists the server and calling `query_knowledge_hub` returns an answer.

---

## Step 10 — Production Deploy (Railway)

```text
Follow CLAUDE.md, phase "Deploy".

- Prepare the project for Railway: one service each for api, mcp and generator (all from the Dockerfile with different start commands).
  Use managed Postgres and MongoDB plugins where they make sense, and Docker images for Qdrant, Neo4j and SeaweedFS, with volumes.
- railway.json / railway.toml as needed. Every connection string comes from Railway variables mapped to our Settings.
- An ingestion job command to run after deploy: seed → ingest ledger/memory/brain.
- Make the API key required in production, and add CORS settings.
- docs/deploy.md: step-by-step deploy, the env var table, and the post-deploy smoke test (health + one cross-domain query).
Don't run any deploy command without asking me first.
```

**Done when:** the public `/health` endpoint is OK and the smoke-test query passes against production.

---

## Step 11 — Grand Finale

```text
Connect this Claude Code session to the production MCP server (streamable HTTP, using the production URL and API key).
Then use the query_knowledge_hub tool to answer:

"Which enterprise customers placed orders above $10k this month, had critical pipeline incidents,
and which downstream dashboards and owning teams were affected according to our SLA policy?"

Show the sub-questions, which domain answered each one, the sources, and the final validated answer.
```

**Done when:** the agent answers by calling the system it built.
