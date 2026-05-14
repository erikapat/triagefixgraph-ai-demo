#!/usr/bin/env python3
"""Load Decision Graph (same source, same dataset) for TriageFix incidents."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DEFAULT_INPUT_CSV = REPO_ROOT / "data" / "processed" / "enriched_incidents_full.csv"
SOURCE_VALUE = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"
MANAGED_LABEL = "TriageFixManaged"
DECISION_LABEL = "DecisionGraph"


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


def _normalize_text(value: str | None) -> str:
    return (value or "").strip()


def _cleanup_previous_decision_graph(session: Any) -> None:
    session.run(
        f"MATCH (n:{DECISION_LABEL}:{MANAGED_LABEL} {{source: $source}}) DETACH DELETE n",
        source=SOURCE_VALUE,
    ).consume()


def _create_constraints(session: Any) -> None:
    # Drop legacy constraints from previous versions (name-based shared nodes).
    legacy_drops = [
        "DROP CONSTRAINT decision_uniqueid_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_urgency_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_reforma_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_ready_band_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_budget_match_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_seguro_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_property_manager_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_technical_construction_name_unique IF EXISTS",
        "DROP CONSTRAINT decision_renovator_name_unique IF EXISTS",
    ]
    for stmt in legacy_drops:
        session.run(stmt).consume()

    statements = [
        "CREATE CONSTRAINT decision_urgency_node_id_unique IF NOT EXISTS FOR (n:UrgencyDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_reforma_node_id_unique IF NOT EXISTS FOR (n:ReformaDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_ready_band_node_id_unique IF NOT EXISTS FOR (n:PropertyReadyBandDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_budget_match_node_id_unique IF NOT EXISTS FOR (n:BudgetAttachmentMatchDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_seguro_node_id_unique IF NOT EXISTS FOR (n:SeguroDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_property_manager_node_id_unique IF NOT EXISTS FOR (n:PropertyManagerDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_technical_construction_node_id_unique IF NOT EXISTS FOR (n:TechnicalConstructionDecision) REQUIRE n.node_id IS UNIQUE",
        "CREATE CONSTRAINT decision_renovator_node_id_unique IF NOT EXISTS FOR (n:RenovatorDecision) REQUIRE n.node_id IS UNIQUE",
    ]
    for stmt in statements:
        session.run(stmt).consume()


def _load_row(session: Any, row: dict[str, str]) -> None:
    incident_id = _normalize_text(row.get("airtable_record_id")) or _normalize_text(row.get("incident_id"))
    if not incident_id:
        return

    unique_id = (
        _normalize_text(row.get("UNIQUE ID"))
        or _normalize_text(row.get("UNIQUE_ID"))
        or _normalize_text(row.get("property_context_id"))
    )
    property_context_id = _normalize_text(row.get("property_context_id")) or unique_id or f"unknown_property::{incident_id}"

    urgency = _normalize_text(row.get("Urgency")) or "unknown"
    reforma = _normalize_text(row.get("reforma")).lower() or "no"
    property_ready_band = _normalize_text(row.get("property_ready_band")) or "unknown"
    budget_match = _normalize_text(row.get("budget_attachment_match")) or "unknown"
    seguro = _normalize_text(row.get("seguro")).lower() or "no"
    property_manager = _normalize_text(row.get("Property manager")) or "unknown"
    technical_construction = _normalize_text(row.get("Technical construction")) or "unknown"
    renovator = _normalize_text(row.get("Renovator name")) or _normalize_text(row.get("provider_candidate"))
    has_renovator = renovator != ""

    params = {
        "source": SOURCE_VALUE,
        "incident_id": incident_id,
        "property_context_id": property_context_id,
        "unique_id": unique_id,
        "urgency": urgency,
        "reforma": reforma,
        "property_ready_band": property_ready_band,
        "budget_match": budget_match,
        "seguro": seguro,
        "property_manager": property_manager,
        "technical_construction": technical_construction,
        "renovator": renovator,
        "has_renovator": has_renovator,
        "urgency_node_id": f"{incident_id}::urgency::{urgency}",
        "reforma_node_id": f"{incident_id}::reforma::{reforma}",
        "ready_band_node_id": f"{incident_id}::ready::{property_ready_band}",
        "budget_match_node_id": f"{incident_id}::budget_match::{budget_match}",
        "property_manager_node_id": f"{incident_id}::pm::{property_manager}",
        "seguro_node_id": f"{incident_id}::seguro::{seguro}",
        "technical_construction_node_id": f"{incident_id}::tc::{technical_construction}",
        "renovator_node_id": f"{incident_id}::renovator::{renovator or 'unknown'}",
    }

    session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source, incident_id: $incident_id}})
        MATCH (p:PropertyContext:{MANAGED_LABEL} {{source: $source, property_context_id: $property_context_id}})

        MERGE (u:UrgencyDecision {{node_id: $urgency_node_id}})
        SET u.name = $urgency,
            u:{MANAGED_LABEL}, u:{DECISION_LABEL}, u.source = $source
        MERGE (i)-[:DECISION_HAS_URGENCY]->(u)

        MERGE (rf:ReformaDecision {{node_id: $reforma_node_id}})
        SET rf.name = $reforma,
            rf:{MANAGED_LABEL}, rf:{DECISION_LABEL}, rf.source = $source
        MERGE (u)-[:DECISION_URGENCY_TO_REFORMA]->(rf)
        MERGE (rf)-[:DECISION_REFORMA_ON_PROPERTY]->(p)

        MERGE (prb:PropertyReadyBandDecision {{node_id: $ready_band_node_id}})
        SET prb.name = $property_ready_band,
            prb:{MANAGED_LABEL}, prb:{DECISION_LABEL}, prb.source = $source
        MERGE (rf)-[r_ready:DECISION_HAS_PROPERTY_READY_BAND]->(prb)
        SET r_ready.active = ($reforma = 'si')

        MERGE (bam:BudgetAttachmentMatchDecision {{node_id: $budget_match_node_id}})
        SET bam.name = $budget_match,
            bam:{MANAGED_LABEL}, bam:{DECISION_LABEL}, bam.source = $source
        MERGE (prb)-[r_budget:DECISION_HAS_BUDGET_ATTACHMENT_MATCH]->(bam)
        SET r_budget.active = ($reforma = 'si')

        MERGE (pm:PropertyManagerDecision {{node_id: $property_manager_node_id}})
        SET pm.name = $property_manager,
            pm:{MANAGED_LABEL}, pm:{DECISION_LABEL}, pm.source = $source
        MERGE (rf)-[r_pm_direct:DECISION_ROUTES_TO_PROPERTY_MANAGER]->(pm)
        SET r_pm_direct.active = ($reforma = 'no')
        MERGE (bam)-[r_pm_budget:DECISION_INFORMS_PROPERTY_MANAGER]->(pm)
        SET r_pm_budget.active = ($reforma = 'si' AND $budget_match = 'no')

        MERGE (seg:SeguroDecision {{node_id: $seguro_node_id}})
        SET seg.name = $seguro,
            seg:{MANAGED_LABEL}, seg:{DECISION_LABEL}, seg.source = $source
        MERGE (pm)-[:DECISION_HAS_SEGURO]->(seg)

        MERGE (tc:TechnicalConstructionDecision {{node_id: $technical_construction_node_id}})
        SET tc.name = $technical_construction,
            tc:{MANAGED_LABEL}, tc:{DECISION_LABEL}, tc.source = $source
        MERGE (bam)-[r_tc:DECISION_USES_TECHNICAL_CONSTRUCTION]->(tc)
        SET r_tc.active = ($reforma = 'si' AND $budget_match = 'si')

        FOREACH (_ IN CASE WHEN $has_renovator THEN [1] ELSE [] END |
          MERGE (r:RenovatorDecision {{node_id: $renovator_node_id}})
          SET r.name = $renovator,
              r:{MANAGED_LABEL}, r:{DECISION_LABEL}, r.source = $source
          MERGE (bam)-[r_ren:DECISION_SUGGESTS_RENOVATOR]->(r)
          SET r_ren.active = ($reforma = 'si' AND $budget_match = 'si')
        )
        """,
        params,
    ).consume()


def main() -> int:
    _load_dotenv_if_available()
    neo4j_uri = _require_env("NEO4J_URI")
    neo4j_user = _require_env("NEO4J_USERNAME")
    neo4j_pass = _require_env("NEO4J_PASSWORD")

    input_csv_env = os.getenv("TRIAGEFIX_GRAPH_INPUT_CSV", "").strip()
    input_csv_path = Path(input_csv_env) if input_csv_env else DEFAULT_INPUT_CSV
    input_csv_path = input_csv_path if input_csv_path.is_absolute() else (REPO_ROOT / input_csv_path)

    if not input_csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_csv_path}")

    with input_csv_path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    print(f"input CSV: {input_csv_path}")
    print(f"number of rows read: {len(rows)}")
    print(f"source: {SOURCE_VALUE}")

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session() as session:
            _create_constraints(session)
            _cleanup_previous_decision_graph(session)
            for idx, row in enumerate(rows, start=1):
                _load_row(session, row)
                if idx % 500 == 0:
                    print(f"loaded decision rows: {idx}/{len(rows)}")

            node_counts = session.run(
                f"""
                MATCH (n:{DECISION_LABEL}:{MANAGED_LABEL} {{source: $source}})
                RETURN labels(n) AS labels, count(n) AS c
                ORDER BY c DESC
                """,
                source=SOURCE_VALUE,
            ).data()
            print("decision node counts:")
            for rec in node_counts:
                labels = [x for x in rec["labels"] if x not in {MANAGED_LABEL, DECISION_LABEL}]
                print(f"  - {','.join(labels) or 'DecisionNode'}: {int(rec['c'])}")
    finally:
        driver.close()

    print("done: decision graph loaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
