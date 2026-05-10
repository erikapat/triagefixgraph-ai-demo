# TriageFix Demo Queries

The queries below are designed for the current TriageFix graph loaded from the enriched Airtable sample.

## 1. Node counts excluding the `TriageFixManaged` label
Business explanation: Helps validate baseline graph coverage by label while excluding the script management label from the label list.

```cypher
MATCH (n)
WHERE n:TriageFixManaged
UNWIND [l IN labels(n) WHERE l <> 'TriageFixManaged'] AS label
RETURN label, count(*) AS node_count
ORDER BY node_count DESC, label ASC;
```

## 2. Relationship counts between `TriageFixManaged` nodes
Business explanation: Confirms that the expected incident-context connections were created and their volume.

```cypher
MATCH (a:TriageFixManaged)-[r]->(b:TriageFixManaged)
RETURN type(r) AS relationship_type, count(r) AS relationship_count
ORDER BY relationship_count DESC, relationship_type ASC;
```

## 3. Top incidents by `severity_average`
Business explanation: Prioritizes incidents with highest aggregate severity for triage attention.

```cypher
MATCH (i:Incident:TriageFixManaged)
RETURN
  i.incident_id AS incident_id,
  i.severity_average AS severity_average,
  i.created_date AS created_date,
  i.similarity_key AS similarity_key,
  left(i.clean_description, 180) AS description_preview
ORDER BY i.severity_average DESC, i.created_date DESC
LIMIT 10;
```

## 4. Full context for one incident
Business explanation: Shows end-to-end triage context for a single incident, including routing and evidence.

```cypher
MATCH (i:Incident:TriageFixManaged {incident_id: $incident_id})
OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
OPTIONAL MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
OPTIONAL MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(ss:SeveritySignal)
RETURN
  i.incident_id AS incident_id,
  i.created_date AS created_date,
  i.resolved_date AS resolved_date,
  i.severity_average AS severity_average,
  i.provider_confidence AS provider_confidence,
  i.clean_description AS clean_description,
  p.property_context_id AS property_context,
  a.name AS area_cluster,
  c.name AS category,
  u.name AS urgency,
  s.name AS status,
  ra.name AS recommended_action,
  r.name AS provider,
  e.evidence_id AS evidence_id,
  e.has_incidence_docs AS has_incidence_docs,
  e.incidence_docs_count AS incidence_docs_count,
  e.lease_contract_count AS lease_contract_count,
  e.furniture_budget_count AS furniture_budget_count,
  e.finance_invoice_count AS finance_invoice_count,
  collect(DISTINCT mq.text) AS missing_questions,
  collect(DISTINCT {dimension: ss.dimension, score: ss.score}) AS severity_signals;
```

Example parameter:

```cypher
:param incident_id => 'recRH51V2eHHlTh9W';
```

## 5. Similar incidents using `SIMILAR_TO`
Business explanation: Surfaces comparable historical incidents to guide faster and more consistent decisions.

```cypher
MATCH (i:Incident:TriageFixManaged {incident_id: $incident_id})-[:SIMILAR_TO]->(j:Incident:TriageFixManaged)
OPTIONAL MATCH (j)-[:HAS_CATEGORY]->(c:Category)
OPTIONAL MATCH (j)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
RETURN
  i.incident_id AS base_incident,
  j.incident_id AS similar_incident,
  c.name AS category,
  p.property_context_id AS property_context,
  j.severity_average AS severity_average,
  j.created_date AS created_date,
  left(j.clean_description, 160) AS similar_description_preview
ORDER BY j.severity_average DESC, j.created_date DESC;
```

## 6. Incidents by category
Business explanation: Gives a quick operational view of where incident volume is concentrated.

```cypher
MATCH (i:Incident:TriageFixManaged)-[:HAS_CATEGORY]->(c:Category)
RETURN c.name AS category, count(i) AS incidents
ORDER BY incidents DESC, category ASC;
```

## 7. Renovator workload by category
Business explanation: Helps evaluate assignment balance and specialist concentration by problem type.

```cypher
MATCH (i:Incident:TriageFixManaged)-[:HAS_CATEGORY]->(c:Category)
MATCH (i)-[:HANDLED_BY]->(r:Renovator)
RETURN
  r.name AS renovator,
  c.name AS category,
  count(i) AS incident_count,
  round(avg(i.severity_average), 2) AS avg_severity
ORDER BY incident_count DESC, avg_severity DESC, renovator ASC, category ASC;
```

## 8. High urgency incidents and recommended trades
Business explanation: Verifies whether urgent incidents are mapped to the right trade specialists.

```cypher
MATCH (i:Incident:TriageFixManaged)-[:HAS_URGENCY]->(u:Urgency)
MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
WHERE toLower(u.name) IN ['high', 'alta']
RETURN
  i.incident_id AS incident_id,
  i.created_date AS created_date,
  c.name AS category,
  u.name AS urgency,
  t.name AS recommended_trade,
  i.severity_average AS severity_average,
  i.provider_confidence AS provider_confidence
ORDER BY i.severity_average DESC, i.created_date DESC;
```

## 9. Evidence coverage
Business explanation: Tracks how much of the incident set has supporting evidence and what kind.

```cypher
MATCH (i:Incident:TriageFixManaged)
OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
RETURN
  count(i) AS total_incidents,
  count(DISTINCT CASE WHEN e IS NOT NULL THEN i END) AS incidents_with_evidence,
  round(100.0 * count(DISTINCT CASE WHEN e IS NOT NULL THEN i END) / count(i), 2) AS evidence_coverage_pct,
  count(DISTINCT CASE WHEN e.has_incidence_docs THEN i END) AS incidents_with_incidence_docs,
  sum(coalesce(e.incidence_docs_count, 0)) AS total_incidence_docs,
  sum(coalesce(e.lease_contract_count, 0)) AS total_lease_contract_docs,
  sum(coalesce(e.furniture_budget_count, 0)) AS total_furniture_budget_docs,
  sum(coalesce(e.finance_invoice_count, 0)) AS total_finance_invoice_docs;
```

## 10. Missing questions grouped by category
Business explanation: Shows what extra information is repeatedly missing per category, useful for intake form improvements.

```cypher
MATCH (i:Incident:TriageFixManaged)-[:HAS_CATEGORY]->(c:Category)
MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
RETURN
  c.name AS category,
  mq.text AS missing_question,
  count(*) AS frequency
ORDER BY category ASC, frequency DESC, missing_question ASC;
```
