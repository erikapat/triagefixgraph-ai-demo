# AGENTS.md

## Project mission

This project is TriageFixGraph AI.


The goal is to build a temporal context graph for housing maintenance incidents. The graph connects incidents, properties/buildings, problem types, time sequence, prior similar incidents, and candidate licensed providers.
We use create-context-graph as the technical shell, but the domain is housing maintenance incident triage.

The MVP uses Airtable Incidences as the first real data source.

## Scope

Do not use demo seed data from the scaffolder.

## Real data policy

Do not commit:
- .env
- backend/.env
- Airtable exports
- image files
- processed CSVs
- SQLite databases

Real data should stay local under:
- data/airtable_sample/
- data/evidence_sample/
- data/processed/
- storage/

## MVP ontology labels

Use these labels:
- Incident
- Evidence
- Category
- Subcategory
- Urgency
- Status
- SeveritySignal
- MissingQuestion
- RecommendedAction
- TradeSpecialist
- Renovator
- ProviderCandidate
- CostBand
- ResolutionTimeBand
- PropertyContext
- AreaCluster
- HistoricalCase

## MVP relationships

Use relationships like:
- HAS_EVIDENCE
- HAS_CATEGORY
- HAS_SUBCATEGORY
- HAS_URGENCY
- HAS_STATUS
- HAS_SEVERITY_SIGNAL
- NEEDS_QUESTION
- RECOMMENDED_ACTION
- REQUIRES_TRADE
- HANDLED_BY
- HAS_COST_BAND
- HAS_RESOLUTION_TIME_BAND
- HAS_PROPERTY_CONTEXT
- LOCATED_IN_AREA
- SIMILAR_TO
- OCCURRED_ON
- NEXT_FOR_CONTEXT
- PRIOR_SIMILAR

## Hackathon priority

Prioritize:
1. Local Airtable Incidences sample.
2. Clean TriageFix graph.
3. One or two strong demo incidents.
4. Evidence metadata.
5. Optional image analysis only for selected examples.
6. Assistant answers that explain graph context.

Do not over-engineer.
