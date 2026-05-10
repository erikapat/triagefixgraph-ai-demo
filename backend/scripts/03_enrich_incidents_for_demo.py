#!/usr/bin/env python3
"""Create deterministic enrichment for Airtable incident candidates."""

from __future__ import annotations

import ast
import csv
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
INPUT_CANDIDATES = REPO_ROOT / "data" / "processed" / "demo_candidate_incidents.csv"
INPUT_ATTACHMENTS = REPO_ROOT / "data" / "airtable_sample" / "incidence_attachments_metadata.json"
OUTPUT_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_CSV = OUTPUT_DIR / "enriched_incidents_demo.csv"
OUTPUT_JSON = OUTPUT_DIR / "enriched_incidents_demo.json"

SOURCE_AIRTABLE = "source_airtable"
SOURCE_RULE = "source_rule_based_inference"
SOURCE_ENRICH = "source_demo_enrichment"

ATTACHMENT_COUNT_FIELDS = [
    "Incidence docs__attachment_count",
    "Lease Contract__attachment_count",
    "Furniture budget doc__attachment_count",
    "Finance Invoice__attachment_count",
]

CATEGORY_RULES: list[tuple[str, list[str], str]] = [
    ("Electrodoméstico", ["termo", "lavadora", "nevera", "horno", "vitro", "lavavajillas", "frigorifico", "frigorífico"], "Aparatos domésticos"),
    ("Fontanería", ["fuga", "retrete", "desagüe", "desague", "sumidero", "grifo", "ducha", "agua", "cisterna", "tuber"], "Agua y saneamiento"),
    ("Electricidad", ["luz", "enchufe", "electricidad", "diferencial", "interruptor", "cortocircuit", "salta"], "Instalación eléctrica"),
    ("Puerta / Ventana", ["persiana", "ventana", "puerta"], "Carpintería exterior/interior"),
    ("Cerradura", ["cerradura", "llave", "bombín", "bombin"], "Acceso y seguridad"),
    ("Humedad / Filtración", ["humedad", "gotera", "filtración", "filtracion", "mancha", "moho", "infiltr"], "Humedad y filtraciones"),
    ("Climatización", ["aire acondicionado", "calefacción", "calefaccion", "caldera", "radiador", "split"], "Clima interior"),
    ("Mobiliario", ["armario", "cama", "sofa", "sofá", "mesa", "silla", "mueble", "colchón", "colchon"], "Mobiliario y equipamiento"),
    ("Limpieza / General", ["limpieza", "suciedad", "olor", "basura", "general"], "Limpieza y mantenimiento general"),
]

TRADE_MAP = {
    "Fontanería": "Plumbing specialist",
    "Electricidad": "Electrician",
    "Electrodoméstico": "Appliance technician",
    "Puerta / Ventana": "Handyman / carpenter",
    "Cerradura": "Locksmith",
    "Humedad / Filtración": "Plumbing specialist / building inspection",
    "Climatización": "HVAC technician",
    "Mobiliario": "Human review or general handyman",
    "Limpieza / General": "Human review or general handyman",
    "Otro": "Human review or general handyman",
}

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")


def _is_non_empty(value: str | None) -> bool:
    return bool((value or "").strip())


def _normalize_airtable_list(raw: str | None) -> str:
    if not _is_non_empty(raw):
        return ""
    text = (raw or "").strip()
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                vals = [str(v).strip() for v in parsed if str(v).strip()]
                return " | ".join(vals)
        except (ValueError, SyntaxError):
            pass
    return text


def _to_int(value: str | None) -> int:
    if not _is_non_empty(value):
        return 0
    text = (value or "").strip().replace(",", ".")
    try:
        return int(float(text))
    except ValueError:
        return 0


def _clean_description(text: str | None) -> str:
    if not _is_non_empty(text):
        return ""
    cleaned = EMAIL_RE.sub("[redacted_email]", text or "")
    cleaned = PHONE_RE.sub("[redacted_phone]", cleaned)
    return "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()


def _infer_category(clean_description: str, type_value: str, type_old_value: str) -> tuple[str, str, float, list[str]]:
    haystack = " ".join([clean_description.lower(), type_value.lower(), type_old_value.lower()])

    best_category = "Otro"
    best_subcategory = "General"
    best_hits = 0
    matched_terms: list[str] = []

    for category, terms, subcategory in CATEGORY_RULES:
        hits = [term for term in terms if term in haystack]
        if len(hits) > best_hits:
            best_hits = len(hits)
            best_category = category
            best_subcategory = subcategory
            matched_terms = hits

    confidence = 0.3
    if best_hits >= 3:
        confidence = 0.95
    elif best_hits == 2:
        confidence = 0.8
    elif best_hits == 1:
        confidence = 0.65
    elif _is_non_empty(type_value) or _is_non_empty(type_old_value):
        confidence = 0.5

    return best_category, best_subcategory, confidence, matched_terms


def _urgency_boost(urgency: str) -> int:
    u = urgency.lower()
    if "high" in u or "alta" in u:
        return 2
    if "medium" in u or "media" in u:
        return 1
    return 0


def _bounded(value: int) -> int:
    return max(1, min(5, value))


def _infer_severity(clean_description: str, urgency: str, has_incidence_docs: bool, has_any_attachment: bool) -> dict[str, int]:
    text = clean_description.lower()
    boost = _urgency_boost(urgency)

    no_electricity = any(k in text for k in ["sin luz", "no electricidad", "no tiene electricidad", "corte eléctrico"])
    no_water = any(k in text for k in ["sin agua", "no tiene agua"])
    lockout = any(k in text for k in ["cerradura", "no abre", "llave rota", "no puede entrar"])
    active_leak = any(k in text for k in ["fuga activa", "pierde agua", "sale agua", "agua en el suelo", "gotera"])
    humidity = any(k in text for k in ["humedad", "moho", "mancha", "filtración", "filtracion"])

    severity_people_risk = _bounded(1 + (1 if no_electricity else 0) + (1 if active_leak else 0) + boost)
    severity_habitability = _bounded(2 + (2 if (no_electricity or no_water or lockout) else 0) + boost)
    severity_material_damage = _bounded(1 + (2 if (active_leak or humidity) else 0) + (1 if "agua" in text else 0))
    severity_worsening_probability = _bounded(2 + (2 if (active_leak or humidity) else 0) + (1 if "empeor" in text else 0))
    severity_extent = _bounded(1 + (1 if "varios" in text or "varias" in text else 0) + (1 if "y" in text and len(text) > 120 else 0))
    severity_temporal_urgency = _bounded(1 + boost + (1 if active_leak else 0) + (1 if no_electricity or no_water else 0))

    evidence_confidence = 2
    if has_incidence_docs:
        evidence_confidence = 4
    elif has_any_attachment:
        evidence_confidence = 3

    if not _is_non_empty(clean_description):
        evidence_confidence = 1

    return {
        "severity_people_risk": severity_people_risk,
        "severity_habitability": severity_habitability,
        "severity_material_damage": severity_material_damage,
        "severity_worsening_probability": severity_worsening_probability,
        "severity_extent": severity_extent,
        "severity_temporal_urgency": severity_temporal_urgency,
        "evidence_confidence": evidence_confidence,
    }


def _missing_questions(category: str) -> list[str]:
    base = [
        "¿El problema impide usar baño/cocina/entrada principal?",
        "¿Ha ocurrido antes en la misma vivienda?",
    ]

    by_category = {
        "Fontanería": [
            "¿La fuga está activa ahora mismo?",
            "¿Hay agua acumulada en el suelo?",
            "¿Afecta a vecinos o zonas comunes?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Electricidad": [
            "¿Hay zonas de la vivienda sin suministro eléctrico?",
            "¿Saltan diferencial o magnetotérmicos al encender algo?",
            "¿Se detecta olor a quemado o chispas?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Electrodoméstico": [
            "¿El electrodoméstico enciende y muestra algún error?",
            "¿El problema impide cocinar, conservar alimentos o lavar ropa?",
            "¿Hay fuga de agua asociada al aparato?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Puerta / Ventana": [
            "¿La puerta o ventana queda bloqueada o abierta permanentemente?",
            "¿Compromete la seguridad o aislamiento de la vivienda?",
            "¿Afecta a la entrada principal?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Cerradura": [
            "¿La vivienda está inaccesible ahora mismo?",
            "¿Hay personas dentro sin posibilidad de abrir?",
            "¿Existe llave de respaldo disponible?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Humedad / Filtración": [
            "¿La humedad o gotera sigue activa?",
            "¿La mancha está creciendo o aparecieron nuevas zonas?",
            "¿Afecta instalaciones eléctricas cercanas?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
        "Climatización": [
            "¿El equipo no enfría/calienta o no enciende?",
            "¿Hay personas vulnerables afectadas por temperatura extrema?",
            "¿Aparece error o goteo en la unidad?",
            "¿Puedes enviar una foto o vídeo actualizado?",
        ],
    }

    specific = by_category.get(category, ["¿Puedes enviar una foto o vídeo actualizado?"])
    return specific + base


def _recommended_action(category: str, severity: dict[str, int], category_confidence: float) -> str:
    high_severity = mean([
        severity["severity_people_risk"],
        severity["severity_habitability"],
        severity["severity_material_damage"],
        severity["severity_worsening_probability"],
        severity["severity_temporal_urgency"],
    ]) >= 4.0

    if high_severity:
        return "Emergency escalation if high severity"

    if category == "Fontanería":
        return "Assign plumbing specialist"
    if category == "Electricidad":
        return "Assign electrician"
    if category == "Electrodoméstico":
        return "Assign appliance technician"
    if category_confidence < 0.6 or category == "Otro":
        return "Human review due to mixed incident"

    return "Ask missing questions before assigning provider"


def _provider_confidence(provider_candidate: str, category_confidence: float, clean_description: str) -> str:
    if _is_non_empty(provider_candidate) and category_confidence >= 0.75:
        return "high"
    if _is_non_empty(provider_candidate) and category_confidence >= 0.5:
        return "medium"
    if _is_non_empty(clean_description) and len(clean_description) > 40:
        return "medium"
    return "low"


def _preview(text: str, size: int = 90) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= size:
        return one_line
    return one_line[: size - 3] + "..."


def main() -> int:
    if not INPUT_CANDIDATES.exists():
        raise FileNotFoundError(f"Input CSV not found: {INPUT_CANDIDATES}")
    if not INPUT_ATTACHMENTS.exists():
        raise FileNotFoundError(f"Input JSON not found: {INPUT_ATTACHMENTS}")

    with INPUT_CANDIDATES.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    with INPUT_ATTACHMENTS.open("r", encoding="utf-8") as f:
        attachments_payload = json.load(f)

    attachment_records = attachments_payload.get("records", {})

    enriched_records: list[dict[str, Any]] = []

    for row in rows:
        record_id = row.get("airtable_record_id", "")
        raw_description = row.get("Description", "")
        clean_description = _clean_description(raw_description)

        unique_id = _normalize_airtable_list(row.get("UNIQUE ID", ""))
        renovator_name = _normalize_airtable_list(row.get("Renovator name", ""))
        property_manager = _normalize_airtable_list(row.get("Property manager", ""))
        area_cluster = _normalize_airtable_list(row.get("Area cluster", ""))
        urgency = _normalize_airtable_list(row.get("Urgency", ""))
        status = _normalize_airtable_list(row.get("Status", ""))
        type_value = _normalize_airtable_list(row.get("Type", ""))
        type_old_value = _normalize_airtable_list(row.get("Type Old", ""))

        incidence_docs_count = _to_int(row.get("Incidence docs__attachment_count", "0"))
        lease_contract_count = _to_int(row.get("Lease Contract__attachment_count", "0"))
        furniture_budget_count = _to_int(row.get("Furniture budget doc__attachment_count", "0"))
        finance_invoice_count = _to_int(row.get("Finance Invoice__attachment_count", "0"))

        has_incidence_docs = incidence_docs_count > 0
        has_any_attachment = any([
            incidence_docs_count > 0,
            lease_contract_count > 0,
            furniture_budget_count > 0,
            finance_invoice_count > 0,
        ])

        inferred_category, inferred_subcategory, category_confidence, matched_terms = _infer_category(
            clean_description, type_value, type_old_value
        )
        recommended_trade = TRADE_MAP.get(inferred_category, TRADE_MAP["Otro"])

        severity = _infer_severity(clean_description, urgency, has_incidence_docs, has_any_attachment)
        severity_average = round(mean(list(severity.values())), 2)

        questions = _missing_questions(inferred_category)
        action = _recommended_action(inferred_category, severity, category_confidence)

        provider_candidate = renovator_name
        provider_confidence = _provider_confidence(provider_candidate, category_confidence, clean_description)
        provider_routing_reason = (
            f"Category={inferred_category}; area={area_cluster or 'unknown'}; "
            f"urgency={urgency or 'unknown'}; historical assignment={provider_candidate or 'none'}"
        )

        incident_node_id = f"incident::{record_id}"
        property_context_id = unique_id or f"unknown_property::{record_id}"
        similarity_key = f"{property_context_id}::{inferred_category}"

        attachment_detail = attachment_records.get(record_id, {}).get("attachments", {})

        enriched = {
            "airtable_record_id": record_id,
            "airtable_created_time": row.get("airtable_created_time", ""),
            "Created date": row.get("Created date", ""),
            "Resolved date": row.get("Resolved date", ""),
            "Days to resolve": row.get("Days to resolve", ""),
            "Description": raw_description,
            "clean_description": clean_description,
            "clean_description_source": SOURCE_ENRICH,
            "Urgency": urgency,
            "Status": status,
            "Type": type_value,
            "Type Old": type_old_value,
            "UNIQUE ID": unique_id,
            "Renovator name": renovator_name,
            "Property manager": property_manager,
            "Area cluster": area_cluster,
            "Incidence red flags": row.get("Incidence red flags", ""),
            "Solution description": row.get("Solution description", ""),
            "inferred_category": inferred_category,
            "inferred_category_source": SOURCE_RULE,
            "inferred_subcategory": inferred_subcategory,
            "inferred_subcategory_source": SOURCE_RULE,
            "category_confidence": round(category_confidence, 2),
            "matched_terms": matched_terms,
            "recommended_trade": recommended_trade,
            "recommended_trade_source": SOURCE_RULE,
            "severity_people_risk": severity["severity_people_risk"],
            "severity_habitability": severity["severity_habitability"],
            "severity_material_damage": severity["severity_material_damage"],
            "severity_worsening_probability": severity["severity_worsening_probability"],
            "severity_extent": severity["severity_extent"],
            "severity_temporal_urgency": severity["severity_temporal_urgency"],
            "evidence_confidence": severity["evidence_confidence"],
            "severity_source": SOURCE_RULE,
            "severity_average": severity_average,
            "missing_questions": questions,
            "missing_questions_source": SOURCE_ENRICH,
            "recommended_action": action,
            "recommended_action_source": SOURCE_ENRICH,
            "provider_candidate": provider_candidate,
            "provider_candidate_source": SOURCE_AIRTABLE,
            "provider_confidence": provider_confidence,
            "provider_confidence_source": SOURCE_ENRICH,
            "provider_routing_reason": provider_routing_reason,
            "provider_routing_reason_source": SOURCE_ENRICH,
            "incident_node_id": incident_node_id,
            "property_context_id": property_context_id,
            "area_cluster": area_cluster,
            "similarity_key": similarity_key,
            "has_incidence_docs": has_incidence_docs,
            "has_any_attachment": has_any_attachment,
            "Incidence docs__attachment_count": incidence_docs_count,
            "Lease Contract__attachment_count": lease_contract_count,
            "Furniture budget doc__attachment_count": furniture_budget_count,
            "Finance Invoice__attachment_count": finance_invoice_count,
            "attachment_metadata": attachment_detail,
            "traceability": {
                "source_labels": [SOURCE_AIRTABLE, SOURCE_RULE, SOURCE_ENRICH],
                "airtable_fields": {
                    "Description": "Description",
                    "Urgency": "Urgency",
                    "Status": "Status",
                    "Type": "Type",
                    "Type Old": "Type Old",
                    "UNIQUE ID": "UNIQUE ID",
                    "Renovator name": "Renovator name",
                    "Property manager": "Property manager",
                    "Area cluster": "Area cluster",
                },
            },
        }

        enriched_records.append(enriched)

    def rank_key(item: dict[str, Any]) -> tuple[int, int, int, float]:
        return (
            1 if item["has_incidence_docs"] else 0,
            1 if len(item["clean_description"]) > 40 else 0,
            1 if item["inferred_category"] != "Otro" else 0,
            item["severity_average"],
        )

    selected = sorted(enriched_records, key=rank_key, reverse=True)[:30]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_columns = [
        "airtable_record_id",
        "airtable_created_time",
        "Created date",
        "Resolved date",
        "UNIQUE ID",
        "property_context_id",
        "area_cluster",
        "Urgency",
        "Status",
        "Type",
        "Type Old",
        "Renovator name",
        "clean_description",
        "inferred_category",
        "inferred_subcategory",
        "category_confidence",
        "recommended_trade",
        "severity_people_risk",
        "severity_habitability",
        "severity_material_damage",
        "severity_worsening_probability",
        "severity_extent",
        "severity_temporal_urgency",
        "evidence_confidence",
        "severity_average",
        "recommended_action",
        "provider_candidate",
        "provider_confidence",
        "provider_routing_reason",
        "incident_node_id",
        "similarity_key",
        "has_incidence_docs",
        "has_any_attachment",
        *ATTACHMENT_COUNT_FIELDS,
        "clean_description_source",
        "inferred_category_source",
        "inferred_subcategory_source",
        "recommended_trade_source",
        "severity_source",
        "recommended_action_source",
        "provider_candidate_source",
        "provider_confidence_source",
        "provider_routing_reason_source",
        "missing_questions_json",
    ]

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        for item in selected:
            row = {k: item.get(k, "") for k in csv_columns}
            row["has_incidence_docs"] = "1" if item["has_incidence_docs"] else "0"
            row["has_any_attachment"] = "1" if item["has_any_attachment"] else "0"
            row["missing_questions_json"] = json.dumps(item["missing_questions"], ensure_ascii=False)
            writer.writerow(row)

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "inputs": {
                    "candidates_csv": str(INPUT_CANDIDATES),
                    "attachments_json": str(INPUT_ATTACHMENTS),
                },
                "records_read": len(rows),
                "records_enriched": len(selected),
                "records": selected,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    category_dist = Counter(item["inferred_category"] for item in selected)
    trade_dist = Counter(item["recommended_trade"] for item in selected)

    print(f"records read: {len(rows)}")
    print(f"records enriched: {len(selected)}")
    print("category distribution:")
    for k, v in category_dist.most_common():
        print(f"  - {k}: {v}")

    print("recommended_trade distribution:")
    for k, v in trade_dist.most_common():
        print(f"  - {k}: {v}")

    print("top 10 demo records:")
    for item in selected[:10]:
        print(
            "  - "
            f"airtable_record_id={item['airtable_record_id']} | "
            f"property_context_id={item['property_context_id']} | "
            f"inferred_category={item['inferred_category']} | "
            f"recommended_trade={item['recommended_trade']} | "
            f"provider_candidate={item['provider_candidate']} | "
            f"severity average={item['severity_average']} | "
            f"has_incidence_docs={item['has_incidence_docs']} | "
            f"clean_description preview={_preview(item['clean_description'])}"
        )

    print(f"saved: {OUTPUT_CSV}")
    print(f"saved: {OUTPUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
