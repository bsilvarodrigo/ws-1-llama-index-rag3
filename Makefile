ifeq ($(OS),Windows_NT)
# Use Git Bash on Windows: from PowerShell, GNU make would otherwise fall back to cmd.exe,
# and a bare `bash` can resolve to WSL. The 8.3 path avoids the space in "Program Files".
SHELL := C:/PROGRA~1/Git/bin/bash.exe
else
SHELL := bash
endif

.PHONY: up down logs restart status ingest query test

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f

restart:
	docker compose down && docker compose up -d

status:
	docker compose ps

ingest:
	python -m src.ingestion.run

query:
	@read -p "Question: " q; \
	curl -s -X POST http://localhost:8000/query \
		-H "Content-Type: application/json" \
		-d "{\"question\": \"$$q\"}" | python -m json.tool

test:
	pytest tests/ -v
