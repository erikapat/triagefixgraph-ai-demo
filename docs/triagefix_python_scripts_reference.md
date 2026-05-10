# TriageFixGraph AI — Python Scripts Reference

Este documento resume los scripts Python del proyecto y su propósito dentro del flujo de datos y carga de grafo.

## Ubicación principal
- `backend/scripts/`

## Scripts del pipeline TriageFix

### `01_export_airtable_incidences_sample.py`
Objetivo:
- Exportar incidencias desde Airtable (tabla `Incidences`) a archivos locales.

Entradas:
- Airtable vía API REST (credenciales en `.env`).

Salidas:
- `data/airtable_sample/incidences_sample.csv`
- `data/airtable_sample/incidence_attachments_metadata.json`

Notas:
- No descarga imágenes.
- Guarda conteos de adjuntos en CSV y metadatos completos en JSON.

---

### `02_profile_airtable_incidences.py`
Objetivo:
- Perfilar calidad y cobertura del export de incidencias antes del enriquecimiento.

Entradas:
- `data/airtable_sample/incidences_sample.csv`
- `data/airtable_sample/incidence_attachments_metadata.json`

Salidas:
- `data/processed/airtable_profile_summary.json`
- `data/processed/demo_candidate_incidents.csv`

Qué calcula:
- Completitud por columna
- Distribuciones de campos clave
- Cobertura de adjuntos
- Estadísticas de resolución
- Ranking de candidatos para demo

---

### `03_enrich_incidents_for_demo.py`
Objetivo:
- Enriquecer de forma determinística los candidatos de demo (subset), con reglas.

Entradas:
- `data/processed/demo_candidate_incidents.csv`
- `data/airtable_sample/incidence_attachments_metadata.json`

Salidas:
- `data/processed/enriched_incidents_demo.csv`
- `data/processed/enriched_incidents_demo.json`

Incluye:
- Limpieza/redacción de datos de contacto
- Inferencia de categoría/subcategoría/trade
- Severidad multidimensional
- Preguntas faltantes
- Acción recomendada
- Campos de enrutamiento de proveedor
- Campos para grafo (IDs, similaridad, flags)

---

### `03_enrich_all_incidences_for_graph.py`
Objetivo:
- Enriquecer todas las incidencias del export (no solo candidatos) para exploración de grafo.

Entradas:
- `data/airtable_sample/incidences_sample.csv`

Salidas:
- `data/processed/enriched_incidents_full.csv`
- `data/processed/enriched_incidents_full.json`

Notas:
- Reutiliza la lógica determinística de enriquecimiento.
- Mantiene campos de trazabilidad (`source_airtable`, `source_rule_based_inference`, `source_demo_enrichment`).

---

### `04_load_triagefix_graph.py`
Objetivo:
- Cargar incidencias enriquecidas en Neo4j como grafo TriageFix limpio.

Entrada por defecto:
- `data/processed/enriched_incidents_demo.csv`

Entrada opcional vía variable de entorno:
- `TRIAGEFIX_GRAPH_INPUT_CSV`
- Ejemplo: `data/processed/enriched_incidents_full.csv`

Salida:
- No genera archivo local; escribe nodos/relaciones en Neo4j.

Qué hace:
- Crea constraints/indexes
- Limpia solo nodos gestionados por el script (`TriageFixManaged`, `source=airtable_enriched_sample`)
- Crea nodos y relaciones de dominio
- Construye `PRIOR_SIMILAR` y `SIMILAR_TO`
- Imprime métricas de carga

## Script heredado / scaffolder

### `generate_data.py`
Objetivo:
- Script generado por scaffolder para cargar fixtures genéricos (entities/documents/traces).

Estado en TriageFix:
- No es parte del pipeline principal de datos reales Airtable.
- Se mantiene en repo por compatibilidad, pero el flujo recomendado usa los scripts `01` → `04`.

## Orden recomendado de ejecución

### Modo muestra rápida
1. `01_export_airtable_incidences_sample.py`
2. `02_profile_airtable_incidences.py`
3. `03_enrich_incidents_for_demo.py`
4. `04_load_triagefix_graph.py`

### Modo histórico completo
1. `01_export_airtable_incidences_sample.py` (con `AIRTABLE_MAX_RECORDS` alto)
2. `02_profile_airtable_incidences.py`
3. `03_enrich_all_incidences_for_graph.py`
4. `04_load_triagefix_graph.py` con `TRIAGEFIX_GRAPH_INPUT_CSV=data/processed/enriched_incidents_full.csv`
