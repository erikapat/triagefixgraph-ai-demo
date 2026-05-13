# Data Directory Notes

- `data/fixtures.json` contains legacy Agent Memory scaffold fixture data.
- The current TriageFix graph does not use `data/fixtures.json` in the active demo workflow.
- The current TriageFix graph is built from the enriched incident pipeline/scripts, primarily:
  - `backend/scripts/04_load_triagefix_graph.py`
  - `backend/scripts/05_create_semantic_similarity_edges.py`
- `data/ontology.yaml` is currently being realigned to become runtime source of truth.
