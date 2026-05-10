#!/usr/bin/env python3
"""Load enriched Airtable incidents into Neo4j as a clean TriageFix graph."""

from __future__ import annotations

import ast
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_INPUT_CSV = REPO_ROOT / "data" / "processed" / "enriched_incidents_demo.csv"

SOURCE_VALUE = "airtable_enriched_sample"
MANAGED_LABEL = "TriageFixManaged"


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / "backend" / ".env", override=False)


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _parse_bool(value: str | None) -> bool:
    text = (value or "").strip().lower()
    return text in {"1", "true", "yes"}


def _parse_float(value: str | None) -> float | None:
    if not (value or "").strip():
        return None
    text = (value or "").strip().replace(",", ".")
    if "specialvalue" in text.lower():
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_date(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _parse_missing_questions(value: str | None) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x).strip() for x in parsed if str(x).strip()]


def _resolution_time_band(days_value: str | None) -> str:
    days = _parse_float(days_value)
    if days is None:
        return "unresolved_or_unknown"
    if days < 0:
        return "invalid_negative_days"
    if days == 0:
        return "same_day"
    if 1 <= days <= 3:
        return "1_3_days"
    if 4 <= days <= 7:
        return "4_7_days"
    if 8 <= days <= 30:
        return "8_30_days"
    return "over_30_days"


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def _create_constraints_and_indexes(session: Any) -> None:
    statements = [
        "CREATE CONSTRAINT incident_incident_id_unique IF NOT EXISTS FOR (n:Incident) REQUIRE n.incident_id IS UNIQUE",
        "CREATE CONSTRAINT propertycontext_property_context_id_unique IF NOT EXISTS FOR (n:PropertyContext) REQUIRE n.property_context_id IS UNIQUE",
        "CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (n:Category) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT subcategory_name_unique IF NOT EXISTS FOR (n:Subcategory) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT urgency_name_unique IF NOT EXISTS FOR (n:Urgency) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT status_name_unique IF NOT EXISTS FOR (n:Status) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT tradespecialist_name_unique IF NOT EXISTS FOR (n:TradeSpecialist) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT renovator_name_unique IF NOT EXISTS FOR (n:Renovator) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT areacluster_name_unique IF NOT EXISTS FOR (n:AreaCluster) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT recommendedaction_name_unique IF NOT EXISTS FOR (n:RecommendedAction) REQUIRE n.name IS UNIQUE",
        "CREATE CONSTRAINT evidence_evidence_id_unique IF NOT EXISTS FOR (n:Evidence) REQUIRE n.evidence_id IS UNIQUE",
        "CREATE CONSTRAINT missingquestion_text_unique IF NOT EXISTS FOR (n:MissingQuestion) REQUIRE n.text IS UNIQUE",
        "CREATE CONSTRAINT resolutiontimeband_name_unique IF NOT EXISTS FOR (n:ResolutionTimeBand) REQUIRE n.name IS UNIQUE",
        "CREATE INDEX historicalcase_similarity_key_idx IF NOT EXISTS FOR (n:HistoricalCase) ON (n.similarity_key)",
    ]
    for stmt in statements:
        session.run(stmt).consume()


def _cleanup_previous_load(session: Any) -> None:
    # Delete only nodes/relationships previously managed by this script.
    session.run(
        f"MATCH (n:{MANAGED_LABEL} {{source: $source}}) DETACH DELETE n",
        source=SOURCE_VALUE,
    ).consume()


def _load_row(session: Any, row: dict[str, str]) -> None:
    incident_id = _normalize_text(row.get("airtable_record_id"))
    if not incident_id:
        return

    incident_node_id = _normalize_text(row.get("incident_node_id")) or f"incident::{incident_id}"
    property_context_id = _normalize_text(row.get("property_context_id")) or f"unknown_property::{incident_id}"
    area_cluster = _normalize_text(row.get("area_cluster")) or "unknown"
    category_name = _normalize_text(row.get("inferred_category")) or "Otro"
    subcategory_name = _normalize_text(row.get("inferred_subcategory")) or "General"
    urgency_name = _normalize_text(row.get("Urgency")) or "unknown"
    status_name = _normalize_text(row.get("Status")) or "unknown"
    trade_name = _normalize_text(row.get("recommended_trade")) or "Human review or general handyman"
    renovator_name = _normalize_text(row.get("provider_candidate"))
    action_name = _normalize_text(row.get("recommended_action")) or "Ask missing questions before assigning provider"
    similarity_key = _normalize_text(row.get("similarity_key")) or f"{property_context_id}::{category_name}"

    created_date = _parse_date(row.get("Created date"))
    resolved_date = _parse_date(row.get("Resolved date"))

    has_incidence_docs = _parse_bool(row.get("has_incidence_docs"))
    has_any_attachment = _parse_bool(row.get("has_any_attachment"))

    incidence_docs_count = int(_parse_float(row.get("Incidence docs__attachment_count")) or 0)
    lease_contract_count = int(_parse_float(row.get("Lease Contract__attachment_count")) or 0)
    furniture_budget_count = int(_parse_float(row.get("Furniture budget doc__attachment_count")) or 0)
    finance_invoice_count = int(_parse_float(row.get("Finance Invoice__attachment_count")) or 0)

    resolution_band = _resolution_time_band(row.get("Days to resolve"))
    missing_questions = _parse_missing_questions(row.get("missing_questions_json"))

    params = {
        "source": SOURCE_VALUE,
        "incident_id": incident_id,
        "incident_node_id": incident_node_id,
        "created_date": created_date,
        "resolved_date": resolved_date,
        "clean_description": _normalize_text(row.get("clean_description")),
        "original_unique_id": _normalize_text(row.get("UNIQUE ID")),
        "category_confidence": _parse_float(row.get("category_confidence")),
        "severity_average": _parse_float(row.get("severity_average")),
        "provider_confidence": _normalize_text(row.get("provider_confidence")),
        "has_incidence_docs": has_incidence_docs,
        "has_any_attachment": has_any_attachment,
        "similarity_key": similarity_key,
        "property_context_id": property_context_id,
        "area_cluster": area_cluster,
        "category_name": category_name,
        "subcategory_name": subcategory_name,
        "urgency_name": urgency_name,
        "status_name": status_name,
        "trade_name": trade_name,
        "renovator_name": renovator_name,
        "action_name": action_name,
        "resolution_band": resolution_band,
        "evidence_id": f"evidence::{incident_id}",
        "incidence_docs_count": incidence_docs_count,
        "lease_contract_count": lease_contract_count,
        "furniture_budget_count": furniture_budget_count,
        "finance_invoice_count": finance_invoice_count,
        "missing_questions": missing_questions,
        "severity_people_risk": int(_parse_float(row.get("severity_people_risk")) or 1),
        "severity_habitability": int(_parse_float(row.get("severity_habitability")) or 1),
        "severity_material_damage": int(_parse_float(row.get("severity_material_damage")) or 1),
        "severity_worsening_probability": int(_parse_float(row.get("severity_worsening_probability")) or 1),
        "severity_extent": int(_parse_float(row.get("severity_extent")) or 1),
        "severity_temporal_urgency": int(_parse_float(row.get("severity_temporal_urgency")) or 1),
        "evidence_confidence": int(_parse_float(row.get("evidence_confidence")) or 1),
    }

    session.run(
        f"""
        MERGE (i:Incident {{incident_id: $incident_id}})
        SET i:{MANAGED_LABEL},
            i.source = $source,
            i.incident_node_id = $incident_node_id,
            i.created_date = $created_date,
            i.resolved_date = $resolved_date,
            i.clean_description = $clean_description,
            i.original_unique_id = $original_unique_id,
            i.category_confidence = $category_confidence,
            i.severity_average = $severity_average,
            i.provider_confidence = $provider_confidence,
            i.has_incidence_docs = $has_incidence_docs,
            i.has_any_attachment = $has_any_attachment,
            i.similarity_key = $similarity_key

        MERGE (p:PropertyContext {{property_context_id: $property_context_id}})
        SET p:{MANAGED_LABEL}, p.source = $source

        MERGE (a:AreaCluster {{name: $area_cluster}})
        SET a:{MANAGED_LABEL}, a.source = $source

        MERGE (c:Category {{name: $category_name}})
        SET c:{MANAGED_LABEL}, c.source = $source

        MERGE (s:Subcategory {{name: $subcategory_name}})
        SET s:{MANAGED_LABEL}, s.source = $source

        MERGE (u:Urgency {{name: $urgency_name}})
        SET u:{MANAGED_LABEL}, u.source = $source

        MERGE (st:Status {{name: $status_name}})
        SET st:{MANAGED_LABEL}, st.source = $source

        MERGE (t:TradeSpecialist {{name: $trade_name}})
        SET t:{MANAGED_LABEL}, t.source = $source

        MERGE (ra:RecommendedAction {{name: $action_name}})
        SET ra:{MANAGED_LABEL}, ra.source = $source

        MERGE (rtb:ResolutionTimeBand {{name: $resolution_band}})
        SET rtb:{MANAGED_LABEL}, rtb.source = $source

        MERGE (i)-[:HAS_PROPERTY_CONTEXT]->(p)
        MERGE (p)-[:LOCATED_IN_AREA]->(a)
        MERGE (i)-[:HAS_CATEGORY]->(c)
        MERGE (i)-[:HAS_SUBCATEGORY]->(s)
        MERGE (i)-[:HAS_URGENCY]->(u)
        MERGE (i)-[:HAS_STATUS]->(st)
        MERGE (i)-[:REQUIRES_TRADE]->(t)
        MERGE (i)-[:RECOMMENDED_ACTION]->(ra)
        MERGE (i)-[:HAS_RESOLUTION_TIME_BAND]->(rtb)

        FOREACH (_ IN CASE WHEN $renovator_name <> '' THEN [1] ELSE [] END |
          MERGE (r:Renovator {{name: $renovator_name}})
          SET r:{MANAGED_LABEL}, r.source = $source
          MERGE (i)-[:HANDLED_BY]->(r)
        )

        FOREACH (_ IN CASE WHEN $has_any_attachment THEN [1] ELSE [] END |
          MERGE (e:Evidence {{evidence_id: $evidence_id}})
          SET e:{MANAGED_LABEL},
              e.source = $source,
              e.has_incidence_docs = $has_incidence_docs,
              e.incidence_docs_count = $incidence_docs_count,
              e.lease_contract_count = $lease_contract_count,
              e.furniture_budget_count = $furniture_budget_count,
              e.finance_invoice_count = $finance_invoice_count
          MERGE (i)-[:HAS_EVIDENCE]->(e)
        )

        FOREACH (q IN $missing_questions |
          MERGE (mq:MissingQuestion {{text: q}})
          SET mq:{MANAGED_LABEL}, mq.source = $source
          MERGE (i)-[:NEEDS_QUESTION]->(mq)
        )

        WITH i
        UNWIND [
          {{dimension: 'people_risk', score: $severity_people_risk}},
          {{dimension: 'habitability', score: $severity_habitability}},
          {{dimension: 'material_damage', score: $severity_material_damage}},
          {{dimension: 'worsening_probability', score: $severity_worsening_probability}},
          {{dimension: 'extent', score: $severity_extent}},
          {{dimension: 'temporal_urgency', score: $severity_temporal_urgency}},
          {{dimension: 'evidence_confidence', score: $evidence_confidence}}
        ] AS sev
        MERGE (ss:SeveritySignal {{dimension: sev.dimension, score: sev.score, incident_id: $incident_id}})
        SET ss:{MANAGED_LABEL}, ss.source = $source
        MERGE (i)-[:HAS_SEVERITY_SIGNAL]->(ss)
        """,
        params,
    ).consume()


def _create_historical_cases_and_similarity(session: Any) -> None:
    session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})
        MERGE (h:HistoricalCase {{case_id: 'historical::' + i.incident_id}})
        SET h:{MANAGED_LABEL},
            h.source = $source,
            h.similarity_key = i.similarity_key,
            h.created_date = i.created_date
        MERGE (h)-[:SIMILAR_TO]->(i)
        """,
        source=SOURCE_VALUE,
    ).consume()

    # PRIOR_SIMILAR: older incidents -> newer incidents based on same property history only.
    session.run(
        f"""
        MATCH (older:Incident:{MANAGED_LABEL} {{source: $source}})
        MATCH (newer:Incident:{MANAGED_LABEL} {{source: $source}})
        MATCH (older)-[:HAS_PROPERTY_CONTEXT]->(op:PropertyContext)
        MATCH (newer)-[:HAS_PROPERTY_CONTEXT]->(np:PropertyContext)
        WHERE older.incident_id <> newer.incident_id
          AND older.created_date IS NOT NULL
          AND newer.created_date IS NOT NULL
          AND date(older.created_date) < date(newer.created_date)
          AND op.property_context_id = np.property_context_id
        MERGE (older)-[r:PRIOR_SIMILAR]->(newer)
        SET r.reason = 'same_property',
            r.similarity_type = 'property_history',
            r.older_created_date = older.created_date,
            r.newer_created_date = newer.created_date
        """,
        source=SOURCE_VALUE,
    ).consume()


def _fetch_node_counts(session: Any) -> dict[str, int]:
    labels = [
        "Incident",
        "PropertyContext",
        "AreaCluster",
        "Category",
        "Subcategory",
        "Urgency",
        "Status",
        "TradeSpecialist",
        "Renovator",
        "RecommendedAction",
        "Evidence",
        "MissingQuestion",
        "SeveritySignal",
        "ResolutionTimeBand",
        "HistoricalCase",
    ]
    counts: dict[str, int] = {}
    for label in labels:
        rec = session.run(
            f"MATCH (n:{label}:{MANAGED_LABEL} {{source: $source}}) RETURN count(n) AS c",
            source=SOURCE_VALUE,
        ).single()
        counts[label] = int(rec["c"] if rec else 0)
    return counts


def _fetch_relationship_counts(session: Any) -> dict[str, int]:
    rels = [
        "HAS_PROPERTY_CONTEXT",
        "LOCATED_IN_AREA",
        "HAS_CATEGORY",
        "HAS_SUBCATEGORY",
        "HAS_URGENCY",
        "HAS_STATUS",
        "REQUIRES_TRADE",
        "HANDLED_BY",
        "RECOMMENDED_ACTION",
        "HAS_EVIDENCE",
        "NEEDS_QUESTION",
        "HAS_SEVERITY_SIGNAL",
        "HAS_RESOLUTION_TIME_BAND",
        "PRIOR_SIMILAR",
        "SIMILAR_TO",
    ]
    counts: dict[str, int] = {}
    for rel in rels:
        rec = session.run(
            f"""
            MATCH (a:{MANAGED_LABEL} {{source: $source}})-[r:{rel}]->(b:{MANAGED_LABEL} {{source: $source}})
            RETURN count(r) AS c
            """,
            source=SOURCE_VALUE,
        ).single()
        counts[rel] = int(rec["c"] if rec else 0)
    return counts


def _print_top_lists(session: Any) -> None:
    print("top categories:")
    for rec in session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HAS_CATEGORY]->(c:Category)
        RETURN c.name AS name, count(i) AS c
        ORDER BY c DESC, name ASC
        LIMIT 10
        """,
        source=SOURCE_VALUE,
    ):
        print(f"  - {rec['name']}: {rec['c']}")

    print("top renovators:")
    for rec in session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HANDLED_BY]->(r:Renovator)
        RETURN r.name AS name, count(i) AS c
        ORDER BY c DESC, name ASC
        LIMIT 10
        """,
        source=SOURCE_VALUE,
    ):
        print(f"  - {rec['name']}: {rec['c']}")

    print("top property contexts:")
    for rec in session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
        RETURN p.property_context_id AS name, count(i) AS c
        ORDER BY c DESC, name ASC
        LIMIT 10
        """,
        source=SOURCE_VALUE,
    ):
        print(f"  - {rec['name']}: {rec['c']}")


def main() -> int:
    _load_dotenv_if_available()

    input_csv_env = os.getenv("TRIAGEFIX_GRAPH_INPUT_CSV", "").strip()
    input_csv_path = Path(input_csv_env) if input_csv_env else DEFAULT_INPUT_CSV
    if not input_csv_path.is_absolute():
        input_csv_path = REPO_ROOT / input_csv_path
    input_csv_path = input_csv_path.resolve()

    if not input_csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv_path}")

    uri = _require_env("NEO4J_URI")
    username = _require_env("NEO4J_USERNAME")
    password = _require_env("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"

    print(f"input CSV: {input_csv_path}")

    with input_csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        with driver.session(database=database) as session:
            _create_constraints_and_indexes(session)
            _cleanup_previous_load(session)

            for row in rows:
                _load_row(session, row)

            _create_historical_cases_and_similarity(session)

            node_counts = _fetch_node_counts(session)
            rel_counts = _fetch_relationship_counts(session)

            rec_evidence = session.run(
                f"MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HAS_EVIDENCE]->(:Evidence) RETURN count(DISTINCT i) AS c",
                source=SOURCE_VALUE,
            ).single()
            incidents_with_evidence = int(rec_evidence["c"] if rec_evidence else 0)

            rec_questions = session.run(
                f"MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:NEEDS_QUESTION]->(:MissingQuestion) RETURN count(DISTINCT i) AS c",
                source=SOURCE_VALUE,
            ).single()
            incidents_with_questions = int(rec_questions["c"] if rec_questions else 0)
            prior_similar_count = rel_counts.get("PRIOR_SIMILAR", 0)

            print(f"number of rows read: {len(rows)}")
            print("node counts by label:")
            for label, count in node_counts.items():
                print(f"  - {label}: {count}")

            print("relationship counts by type:")
            for rel, count in rel_counts.items():
                print(f"  - {rel}: {count}")

            _print_top_lists(session)

            print(f"number of incidents with evidence: {incidents_with_evidence}")
            print(f"number of incidents with missing questions: {incidents_with_questions}")
            print(f"PRIOR_SIMILAR count: {prior_similar_count}")
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
