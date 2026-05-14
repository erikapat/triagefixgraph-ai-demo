#!/usr/bin/env python3
"""Create demo-focused enrichment by reusing full enrichment output."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
FULL_SCRIPT = SCRIPT_DIR / "03_enrich_all_incidences_for_graph.py"
FULL_CSV = REPO_ROOT / "data" / "processed" / "enriched_incidents_full.csv"
FULL_JSON = REPO_ROOT / "data" / "processed" / "enriched_incidents_full.json"
OUTPUT_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_CSV = OUTPUT_DIR / "enriched_incidents_demo.csv"
OUTPUT_JSON = OUTPUT_DIR / "enriched_incidents_demo.json"
TOP_N = 30


def _rank_key(item: dict[str, Any]) -> tuple[int, int, int, float]:
    has_docs = str(item.get("has_incidence_docs", "")).strip().lower() in {"1", "true", "yes"}
    clean_desc = str(item.get("clean_description", ""))
    inferred_category = str(item.get("inferred_category", ""))
    try:
        severity_average = float(item.get("severity_average", 0) or 0)
    except (TypeError, ValueError):
        severity_average = 0.0

    return (
        1 if has_docs else 0,
        1 if len(clean_desc) > 40 else 0,
        1 if inferred_category != "Otro" else 0,
        severity_average,
    )


def main() -> int:
    # Single source of truth: run full enrichment first.
    subprocess.run([sys.executable, str(FULL_SCRIPT)], check=True)

    if not FULL_CSV.exists():
        raise FileNotFoundError(f"Expected full CSV not found: {FULL_CSV}")

    with FULL_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    selected = sorted(rows, key=_rank_key, reverse=True)[:TOP_N]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(selected)

    full_json_payload: dict[str, Any] = {}
    if FULL_JSON.exists():
        with FULL_JSON.open("r", encoding="utf-8") as f:
            full_json_payload = json.load(f)

    full_records = full_json_payload.get("records", []) if isinstance(full_json_payload, dict) else []
    selected_ids = {str(r.get("airtable_record_id", "")) for r in selected}
    selected_records = [
        r for r in full_records
        if isinstance(r, dict) and str(r.get("airtable_record_id", "")) in selected_ids
    ]

    with OUTPUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "inputs": {
                    "full_csv": str(FULL_CSV),
                    "full_json": str(FULL_JSON),
                    "full_script": str(FULL_SCRIPT),
                },
                "records_read": len(rows),
                "records_enriched": len(selected),
                "records": selected_records,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"records read from full: {len(rows)}")
    print(f"records selected for demo: {len(selected)}")
    print(f"saved: {OUTPUT_CSV}")
    print(f"saved: {OUTPUT_JSON}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
