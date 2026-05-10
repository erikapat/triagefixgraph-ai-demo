# Graph Similarity Definitions

## PropertyContext
PropertyContext is the current graph node representing the property context of an incident.
It is currently derived from Airtable `UNIQUE ID` / `property_context_id`.

Same PropertyContext means two incidents belong to the same `property_context_id`.

## PRIOR_SIMILAR
Definition:
Historical relationship from an older incident to a newer/current incident.

Current MVP implementation:
Only immediate temporal sequence in the same property history.

Rule:
For each `PropertyContext`:
- keep incidents with non-null `created_date`
- sort by `created_date` ascending (tie-break by `incident_id` ascending)
- connect each incident only to the immediately next incident in time

Direction:
`older incident -> newer incident`.

Reason:
`same_property`.

Similarity type:
`property_timeline`.

Meaning:
This captures ordered property history. It does not necessarily imply the same technical root cause. It means the newer incident should be interpreted with awareness of prior incidents in the same property.

## SIMILAR_TO
Definition:
Semantic similarity relationship between incidents.

Current MVP implementation:
Embedding similarity over `clean_description`.

Default provider:
`sentence-transformers` local model.

Optional provider:
OpenAI embeddings, only if explicitly configured.

Purpose:
Useful for open, unresolved, pending, follow-up, action-required, or recent 2026 incidents. It finds resolved historical cases with similar descriptions, so we can inspect:
- what action was taken
- which provider handled it
- how long it took
- what category/subcategory was assigned
- what missing questions were relevant

## same_category_area
Status:
Documented only. Disabled for now. Do not create graph relationships from this rule yet.

Why disabled:
The available implementation only used same inferred category + same AreaCluster. This is too broad and creates noisy incident-to-incident relationships.

Future definition:
`same_category_area` should mean same inferred category plus meaningful geographic proximity.

Preferred geographic levels:
1. same address or same building
2. same postal code
3. distance within a defined radius using latitude/longitude
4. municipality or AreaCluster only as a weak fallback

Important:
AreaCluster alone is a broad operational grouping and should not be treated as strong similarity.

## Validation queries

```cypher
MATCH (:Incident:TriageFixManaged)-[r:PRIOR_SIMILAR]->(:Incident:TriageFixManaged)
RETURN r.reason AS reason, r.similarity_type AS similarity_type, count(*) AS count
ORDER BY count DESC;
```

```cypher
MATCH (:Incident:TriageFixManaged)-[r:SIMILAR_TO]->(:Incident:TriageFixManaged)
RETURN r.reason AS reason, r.similarity_method AS method, count(*) AS count
ORDER BY count DESC;
```

```cypher
MATCH (p:PropertyContext)<-[:HAS_PROPERTY_CONTEXT]-(i:Incident:TriageFixManaged)
WITH p, count(i) AS n
WHERE n > 1
WITH p LIMIT 1
MATCH (p)<-[:HAS_PROPERTY_CONTEXT]-(i:Incident:TriageFixManaged)
OPTIONAL MATCH (i)-[r:PRIOR_SIMILAR]->(j:Incident:TriageFixManaged)
RETURN
  p.property_context_id AS property,
  i.incident_id AS incident,
  i.created_date AS date,
  collect(j.incident_id) AS next_incidents
ORDER BY date, incident;
```

```cypher
MATCH (:Incident:TriageFixManaged)-[r]->(:Incident:TriageFixManaged)
WHERE r.reason = 'same_category_area'
RETURN type(r) AS relationship_type, count(*) AS count;
```
