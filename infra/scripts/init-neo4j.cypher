// DataOps Knowledge Hub — Neo4j seed graph (Brain)
// Run by seed-neo4j.sh (init-neo4j service). MERGE keeps every statement idempotent,
// so a partial or repeated run never duplicates nodes or relationships.

// Constraints (uniqueness)
CREATE CONSTRAINT pipeline_name IF NOT EXISTS FOR (p:Pipeline) REQUIRE p.name IS UNIQUE;
CREATE CONSTRAINT table_name IF NOT EXISTS FOR (t:Table) REQUIRE t.name IS UNIQUE;
CREATE CONSTRAINT dashboard_name IF NOT EXISTS FOR (d:Dashboard) REQUIRE d.name IS UNIQUE;
CREATE CONSTRAINT team_name IF NOT EXISTS FOR (tm:Team) REQUIRE tm.name IS UNIQUE;

// Teams
MERGE (t:Team {name: 'team-billing'}) SET t.slack_channel = '#billing-eng';
MERGE (t:Team {name: 'team-analytics'}) SET t.slack_channel = '#analytics';
MERGE (t:Team {name: 'team-platform'}) SET t.slack_channel = '#platform-eng';

// Tables
MERGE (t:Table {name: 'customers'}) SET t.schema = 'public', t.database = 'dataops', t.row_count = 5000;
MERGE (t:Table {name: 'orders'}) SET t.schema = 'public', t.database = 'dataops', t.row_count = 50000;
MERGE (t:Table {name: 'products'}) SET t.schema = 'public', t.database = 'dataops', t.row_count = 200;
MERGE (t:Table {name: 'fact_revenue'}) SET t.schema = 'analytics', t.database = 'warehouse', t.row_count = 120000;
MERGE (t:Table {name: 'dim_customers'}) SET t.schema = 'analytics', t.database = 'warehouse', t.row_count = 5000;

// Pipelines
MERGE (p:Pipeline {name: 'etl_billing_daily'}) SET p.schedule = '0 3 * * *', p.owner = 'team-billing', p.sla_minutes = 45;
MERGE (p:Pipeline {name: 'etl_orders_hourly'}) SET p.schedule = '0 * * * *', p.owner = 'team-billing', p.sla_minutes = 15;
MERGE (p:Pipeline {name: 'etl_customer_sync'}) SET p.schedule = '0 6 * * *', p.owner = 'team-platform', p.sla_minutes = 30;
MERGE (p:Pipeline {name: 'analytics_revenue_agg'}) SET p.schedule = '0 5 * * *', p.owner = 'team-analytics', p.sla_minutes = 60;

// Dashboards
MERGE (d:Dashboard {name: 'Revenue Overview'}) SET d.tool = 'Metabase', d.owner = 'team-analytics', d.refresh_frequency = 'hourly';
MERGE (d:Dashboard {name: 'Customer Health'}) SET d.tool = 'Metabase', d.owner = 'team-billing', d.refresh_frequency = 'daily';
MERGE (d:Dashboard {name: 'Pipeline Monitor'}) SET d.tool = 'Grafana', d.owner = 'team-platform', d.refresh_frequency = 'realtime';

// Relationships — Pipeline reads/writes
MATCH (p:Pipeline {name: 'etl_billing_daily'}), (t:Table {name: 'orders'}) MERGE (p)-[:READS_FROM]->(t);
MATCH (p:Pipeline {name: 'etl_billing_daily'}), (t:Table {name: 'fact_revenue'}) MERGE (p)-[:WRITES_TO]->(t);
MATCH (p:Pipeline {name: 'etl_orders_hourly'}), (t:Table {name: 'orders'}) MERGE (p)-[:READS_FROM]->(t);
MATCH (p:Pipeline {name: 'etl_customer_sync'}), (t:Table {name: 'customers'}) MERGE (p)-[:READS_FROM]->(t);
MATCH (p:Pipeline {name: 'etl_customer_sync'}), (t:Table {name: 'dim_customers'}) MERGE (p)-[:WRITES_TO]->(t);
MATCH (p:Pipeline {name: 'analytics_revenue_agg'}), (t:Table {name: 'fact_revenue'}) MERGE (p)-[:READS_FROM]->(t);

// Relationships — Pipeline feeds pipeline
MATCH (p1:Pipeline {name: 'etl_billing_daily'}), (p2:Pipeline {name: 'analytics_revenue_agg'}) MERGE (p1)-[:FEEDS]->(p2);
MATCH (p1:Pipeline {name: 'etl_customer_sync'}), (p2:Pipeline {name: 'analytics_revenue_agg'}) MERGE (p1)-[:FEEDS]->(p2);

// Relationships — Table used by dashboard
MATCH (t:Table {name: 'fact_revenue'}), (d:Dashboard {name: 'Revenue Overview'}) MERGE (t)-[:USED_BY]->(d);
MATCH (t:Table {name: 'dim_customers'}), (d:Dashboard {name: 'Customer Health'}) MERGE (t)-[:USED_BY]->(d);
MATCH (t:Table {name: 'orders'}), (d:Dashboard {name: 'Pipeline Monitor'}) MERGE (t)-[:USED_BY]->(d);

// Relationships — Team owns
MATCH (tm:Team {name: 'team-billing'}), (p:Pipeline {name: 'etl_billing_daily'}) MERGE (tm)-[:OWNS]->(p);
MATCH (tm:Team {name: 'team-billing'}), (p:Pipeline {name: 'etl_orders_hourly'}) MERGE (tm)-[:OWNS]->(p);
MATCH (tm:Team {name: 'team-platform'}), (p:Pipeline {name: 'etl_customer_sync'}) MERGE (tm)-[:OWNS]->(p);
MATCH (tm:Team {name: 'team-analytics'}), (p:Pipeline {name: 'analytics_revenue_agg'}) MERGE (tm)-[:OWNS]->(p);
MATCH (tm:Team {name: 'team-billing'}), (t:Table {name: 'orders'}) MERGE (tm)-[:OWNS]->(t);
MATCH (tm:Team {name: 'team-platform'}), (t:Table {name: 'customers'}) MERGE (tm)-[:OWNS]->(t);
MATCH (tm:Team {name: 'team-analytics'}), (t:Table {name: 'fact_revenue'}) MERGE (tm)-[:OWNS]->(t);
