#!/bin/bash
# DataOps Knowledge Hub — Neo4j seed (runs in the init-neo4j one-shot service).
# Credentials come from the environment (NEO4J_USER, NEO4J_PASSWORD); never hardcode them.
set -euo pipefail

NEO4J_ADDR="${NEO4J_ADDR:-bolt://neo4j:7687}"
: "${NEO4J_USER:?NEO4J_USER is not set}"
: "${NEO4J_PASSWORD:?NEO4J_PASSWORD is not set}"

cypher() {
    cypher-shell -a "$NEO4J_ADDR" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" "$@"
}

# Wait for Neo4j to be ready
until cypher "RETURN 1" > /dev/null 2>&1; do
    echo "Waiting for Neo4j..."
    sleep 3
done

# Skip if already seeded (keeps the script idempotent across restarts/redeploys)
COUNT=$(cypher --format plain "MATCH (p:Pipeline) RETURN count(p)" | tail -1 | tr -d '[:space:]')
if [ "${COUNT:-0}" -gt 0 ]; then
    echo "Neo4j already seeded ($COUNT pipelines). Skipping."
    exit 0
fi

# Run init script
cypher -f /scripts/init-neo4j.cypher
echo "Neo4j seeded successfully."
