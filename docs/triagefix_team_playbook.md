# TriageFixGraph AI — Team Playbook

## 1. Project purpose
TriageFixGraph AI is a graph-based incident triage demo for housing maintenance incidents.

The graph connects:
- incidents
- properties / property context
- areas / clusters
- categories and subcategories
- urgency and status
- providers / renovators
- evidence
- missing questions
- severity signals
- prior similar incidents

Core idea:
A single incident is not treated as an isolated row. It is connected to property history, similar cases, provider assignments, evidence availability, missing information, and recommended actions.

## 2. Working modes
Use two operating modes depending on speed vs. depth.

### Quick sample mode
- Airtable export size: around 200 records
- Used for fast validation of:
  - export scripts
  - enrichment scripts
  - Neo4j loading
  - backend/frontend flow
  - notebook connectivity

### Full historical mode
- Airtable export size: around 1,100+ records in the current project context
- Used for real exploratory analysis:
  - recurrence patterns by property/category/area
  - provider workload and assignment behavior
  - similarity graph analysis
  - stronger demo case selection

Recommended sequence:
1. Start with quick sample mode.
2. Confirm end-to-end pipeline works.
3. Switch to full historical mode for analysis and demo preparation.

Important operational rule:
- The app only shows what is currently loaded in Neo4j.
- Switching between `demo` and `full` requires running the graph loader again.

## 3. Prerequisites
Required locally:
- Docker and Docker Compose
- Python 3.11 + `uv`
- Node.js + npm
- Airtable credentials (if generating data yourself)
- Local Neo4j via `docker-compose`

Main repo folders:
- `backend/`
- `frontend/`
- `data/`
- `docs/`
- `notebooks/`
- `cypher/`

### Backend dependency compatibility (important)
For semantic similarity (`05_create_semantic_similarity_edges.py`) the Python ML stack must be compatible.

Recommended first step:
```bash
cd backend
uv sync
```

If you hit runtime errors around `torch`, `numpy`, `transformers`, or `sentence-transformers`, use this recovery sequence:
```bash
cd backend

uv pip uninstall -y torch numpy sentence-transformers transformers

uv pip install \
  "numpy==1.26.4" \
  "torch>=2.4,<2.6" \
  "transformers>=4.41,<5" \
  "sentence-transformers>=3,<4"
```

Then retry:
```bash
cd backend
EMBEDDING_PROVIDER=sentence_transformers \
SIMILARITY_TOP_K=5 \
SIMILARITY_THRESHOLD=0.55 \
uv run python scripts/05_create_semantic_similarity_edges.py
```

### Quick dependency verification
Before running the similarity script, verify your environment on the new machine:
```bash
cd backend
uv run python -c "import sys; print(sys.version)"
uv run python -c "import numpy, torch, transformers, sentence_transformers; print('ok')"
```

## 4. Environment variables
A local `.env` file is required, but must not be committed.

```bash
# Neo4j local Docker
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j

# Airtable
AIRTABLE_API_KEY=...
AIRTABLE_BASE_ID=...
AIRTABLE_TABLE_NAME=Incidences
AIRTABLE_VIEW_NAME=
AIRTABLE_MAX_RECORDS=200
```

Also supported by export script:
- `AIRTABLE_TOKEN`
- `AIRTABLE_PAT`

Graph loader CSV override:
- `TRIAGEFIX_GRAPH_INPUT_CSV` (optional)

## 5. Data availability and sharing model
Important for onboarding:
- Scripts are versioned in the repo.
- Generated data under `data/` is local runtime output.
- Real/sensitive data should generally not be committed.

A teammate can run the demo in one of two ways:
1. Generate data directly from Airtable using credentials.
2. Receive a prepared `data/` folder through a secure channel.

Key clarification:
The app does not read CSV files directly at runtime. The app reads Neo4j. CSV/JSON files are inputs to enrichment/loading scripts.

## 6. End-to-end pipeline (from clean clone)

### Step 1: Clone and install
```bash
git clone <repo-url>
cd triagefixgraph-ai-demo

# Backend deps
cd backend
uv sync
cd ..

# Frontend deps
cd frontend
npm install
cd ..
```

### Step 2: Start Neo4j
```bash
docker compose up -d
```

Check Neo4j:
- Browser: `http://localhost:7474`
- Bolt: `bolt://localhost:7687`

### Step 3: Generate Airtable export
Quick sample (default 200):
```bash
python3 backend/scripts/01_export_airtable_incidences_sample.py
```

Outputs:
- `data/airtable_sample/incidences_sample.csv`
- `data/airtable_sample/incidence_attachments_metadata.json`

### Step 4: Profile exported incidences
```bash
python3 backend/scripts/02_profile_airtable_incidences.py
```

Outputs:
- `data/processed/airtable_profile_summary.json`
- `data/processed/demo_candidate_incidents.csv`

### Step 5A: Enrich selected candidates (demo-focused)
```bash
python3 backend/scripts/03_enrich_incidents_for_demo.py
```

Outputs:
- `data/processed/enriched_incidents_demo.csv`
- `data/processed/enriched_incidents_demo.json`

### Step 5B: Enrich full sample (graph exploration)
```bash
python3 backend/scripts/03_enrich_all_incidences_for_graph.py
```

Outputs:
- `data/processed/enriched_incidents_full.csv`
- `data/processed/enriched_incidents_full.json`

### Step 6: Load graph into Neo4j
Default load input:
- `data/processed/enriched_incidents_demo.csv`

```bash
cd backend
uv run python scripts/04_load_triagefix_graph.py
```

Use full enriched input:
```bash
cd backend
TRIAGEFIX_GRAPH_INPUT_CSV=data/processed/enriched_incidents_full.csv \
uv run python scripts/04_load_triagefix_graph.py
```

Mandatory note:
- After any enrich/export change, you must run Step 6 again.
- Backend/frontend startup does not reload Neo4j data automatically.
- If you do not rerun Step 6, the UI will keep showing the previous graph state.

The loader:
- creates/ensures constraints
- replaces previous `TriageFixManaged` data for source `airtable_enriched_sample`
- prints node/relationship summaries

Dataset behavior (must be clear):
- `demo` load: smaller set (fast iteration).
- `full` load: larger historical set (analysis and exploration).
- Every run replaces the previous managed graph for the same source.
- It does not merge multiple previous loads.

### Step 7: Create semantic `SIMILAR_TO` relationships
Run semantic similarity after loading incidents into Neo4j.

Default (local sentence-transformers):
```bash
cd backend
EMBEDDING_PROVIDER=sentence_transformers \
SIMILARITY_TOP_K=5 \
SIMILARITY_THRESHOLD=0.55 \
uv run python scripts/05_create_semantic_similarity_edges.py
```

Optional (OpenAI embeddings):
```bash
cd backend
EMBEDDING_PROVIDER=openai \
EMBEDDING_MODEL=text-embedding-3-small \
OPENAI_API_KEY=... \
SIMILARITY_TOP_K=5 \
SIMILARITY_THRESHOLD=0.55 \
uv run python scripts/05_create_semantic_similarity_edges.py
```

What this step does:
- deletes existing `SIMILAR_TO` between managed incidents for source `airtable_enriched_sample`
- creates new embedding-based `SIMILAR_TO` edges
- prints provider/model and creation summary

## 8. Run backend and frontend

### Backend
```bash
cd backend
uv run uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm run dev
```

URLs:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- Health: `http://localhost:8000/health`

## 9. App usage notes
Incident-centric flow:
1. Open Incident view.
2. Select an incident from `/api/triagefix/incidents`.
3. App loads local context from `/api/triagefix/incidents/{incident_id}/context`.
4. Chat prompts become incident-specific when an incident is selected.

Default incident selection:
- Frontend tries to auto-select `rec7i2cq4iVkb0eTy` if it exists in the currently loaded Neo4j dataset.
- If it does not exist, frontend selects the first incident returned by `/api/triagefix/incidents`.

Schema view remains available as secondary/debug mode.

## 10. Notebook exploration
Notebook available:
- `notebooks/01_explore_triagefix_graph_cases.ipynb`

Suggested usage:
1. Load graph into Neo4j and create semantic similarity first (Steps 6 and 7).
2. Open notebook in your preferred environment (Jupyter or VS Code).
3. Use read-only exploration queries for:
   - top severity incidents
   - recurrence by property/context/category
   - provider workload
   - evidence coverage and missing questions

Tip:
Use `docs/triagefix_demo_queries.md` as a query companion.

## 11. Troubleshooting

### A) Export script fails
Symptoms:
- Airtable auth/base/table errors

Checks:
- Verify `AIRTABLE_API_KEY` (or `AIRTABLE_TOKEN` / `AIRTABLE_PAT`)
- Verify `AIRTABLE_BASE_ID`
- Verify `AIRTABLE_TABLE_NAME` (`Incidences` expected)

### B) Enrichment outputs missing
Checks:
- Confirm Step 3 produced `incidences_sample.csv`
- Confirm file paths under `data/airtable_sample/` and `data/processed/`

### C) Neo4j load fails
Checks:
- Neo4j container is running
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` are correct
- If loading full dataset, verify `TRIAGEFIX_GRAPH_INPUT_CSV` path

### D) Similarity edge creation fails
Checks:
- Confirm Step 6 completed successfully
- If using OpenAI provider, verify `OPENAI_API_KEY`
- Validate `EMBEDDING_PROVIDER`, `SIMILARITY_TOP_K`, `SIMILARITY_THRESHOLD`
- Ensure backend dependencies are installed (`uv sync`)

### E) Frontend shows empty/placeholder graph
Checks:
- Confirm graph loaded via Step 6
- Check backend logs for `/api/triagefix/incidents` and `/api/triagefix/incidents/{id}/context`
- Ensure selected incident exists in loaded source

### F) App starts but data looks stale
Cause:
- Neo4j still has the previous loaded dataset (or a different CSV was loaded last)

Fix:
- rerun loader with desired CSV input; loader replaces prior `TriageFixManaged` nodes for current source
- verify with:
```cypher
MATCH (i:Incident:TriageFixManaged) RETURN count(i) AS total_incidents;
```
- verify a specific incident exists:
```cypher
MATCH (i:Incident:TriageFixManaged {incident_id:'rec7i2cq4iVkb0eTy'}) RETURN count(i) AS exists;
```

## 12. Team operational rules
- Do not commit `.env` files.
- Do not commit real Airtable exports or processed real data unless explicitly approved and sanitized.
- Prefer secure channel sharing for prepared `data/` folders.
- Keep scripts versioned and reproducible; keep data local.
