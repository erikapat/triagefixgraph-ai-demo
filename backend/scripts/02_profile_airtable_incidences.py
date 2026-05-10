#!/usr/bin/env python3
"""Profile Airtable Incidences sample and select top candidate incidents."""

from __future__ import annotations

import ast
import csv
import json
from collections import Counter
from datetime import datetime, date
from pathlib import Path
from statistics import mean
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
INPUT_CSV = REPO_ROOT / "data" / "airtable_sample" / "incidences_sample.csv"
INPUT_ATTACHMENTS = REPO_ROOT / "data" / "airtable_sample" / "incidence_attachments_metadata.json"
OUTPUT_DIR = REPO_ROOT / "data" / "processed"
OUTPUT_SUMMARY = OUTPUT_DIR / "airtable_profile_summary.json"
OUTPUT_CANDIDATES = OUTPUT_DIR / "demo_candidate_incidents.csv"

ATTACHMENT_COUNT_COLUMNS = [
    "Incidence docs__attachment_count",
    "Lease Contract__attachment_count",
    "Furniture budget doc__attachment_count",
    "Finance Invoice__attachment_count",
]

TOP_VALUE_FIELDS = [
    "Status",
    "Urgency",
    "Type",
    "Type Old",
    "Area cluster",
    "Renovator name",
    "Property manager",
    "Origin",
]

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%d/%m/%Y",
    "%m/%d/%Y",
]


def _is_non_empty(value: str | None) -> bool:
    return bool((value or "").strip())


def _parse_number(value: str | None) -> float | None:
    if not _is_non_empty(value):
        return None
    raw = (value or "").strip().replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_int(value: str | None) -> int:
    num = _parse_number(value)
    if num is None:
        return 0
    return int(num)


def _parse_date(value: str | None) -> date | None:
    if not _is_non_empty(value):
        return None
    raw = (value or "").strip()

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _normalize_value(raw: str | None) -> list[str]:
    if not _is_non_empty(raw):
        return []

    text = (raw or "").strip()

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                values = []
                for item in parsed:
                    s = str(item).strip()
                    if s:
                        values.append(s)
                return values
        except (ValueError, SyntaxError):
            pass

    return [text]


def _value_for_display(raw: str | None) -> str:
    values = _normalize_value(raw)
    if not values:
        return ""
    return " | ".join(values)


def _description_preview(text: str | None, max_len: int = 90) -> str:
    if not _is_non_empty(text):
        return ""
    clean = " ".join((text or "").split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def _compute_candidate_score(row: dict[str, str], any_attachment: bool) -> tuple[int, list[str]]:
    checks: list[tuple[str, bool]] = [
        ("has_description", _is_non_empty(row.get("Description", ""))),
        ("has_urgency", _is_non_empty(row.get("Urgency", ""))),
        ("has_status", _is_non_empty(row.get("Status", ""))),
        ("has_unique_id", _is_non_empty(row.get("UNIQUE ID", ""))),
        ("has_attachment", any_attachment),
        ("has_renovator_name", _is_non_empty(row.get("Renovator name", ""))),
        (
            "has_resolution_time_or_resolved_date",
            _is_non_empty(row.get("Days to resolve", ""))
            or _is_non_empty(row.get("Resolved date", "")),
        ),
        (
            "has_red_flags_or_solution",
            _is_non_empty(row.get("Incidence red flags", ""))
            or _is_non_empty(row.get("Solution description", "")),
        ),
    ]
    passed = [name for name, ok in checks if ok]
    return len(passed), passed


def main() -> int:
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Input CSV not found: {INPUT_CSV}")
    if not INPUT_ATTACHMENTS.exists():
        raise FileNotFoundError(f"Input attachments JSON not found: {INPUT_ATTACHMENTS}")

    with INPUT_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = reader.fieldnames or []

    with INPUT_ATTACHMENTS.open("r", encoding="utf-8") as f:
        attachments_payload = json.load(f)

    row_count = len(rows)
    column_count = len(columns)

    print(f"Row count: {row_count}")
    print(f"Column count: {column_count}")

    completeness: dict[str, dict[str, Any]] = {}
    for col in columns:
        non_empty = sum(1 for r in rows if _is_non_empty(r.get(col, "")))
        pct = (non_empty / row_count * 100.0) if row_count else 0.0
        completeness[col] = {
            "non_empty": non_empty,
            "total": row_count,
            "completeness_pct": round(pct, 2),
        }

    top_values: dict[str, list[dict[str, Any]]] = {}
    for field in TOP_VALUE_FIELDS:
        counter = Counter()
        for row in rows:
            values = _normalize_value(row.get(field, ""))
            for val in values:
                counter[val] += 1
        top_values[field] = [
            {"value": value, "count": count}
            for value, count in counter.most_common(10)
        ]

    created_dates: list[date] = []
    resolved_dates: list[date] = []
    created_parse_failures = 0
    resolved_parse_failures = 0

    days_to_resolve_values: list[float] = []

    records_with_any_attachment = 0
    attachment_column_coverage = {col: 0 for col in ATTACHMENT_COUNT_COLUMNS}

    candidates: list[dict[str, Any]] = []

    for row in rows:
        created = _parse_date(row.get("Created date", ""))
        resolved = _parse_date(row.get("Resolved date", ""))

        if _is_non_empty(row.get("Created date", "")):
            if created is None:
                created_parse_failures += 1
            else:
                created_dates.append(created)

        if _is_non_empty(row.get("Resolved date", "")):
            if resolved is None:
                resolved_parse_failures += 1
            else:
                resolved_dates.append(resolved)

        days_to_resolve = _parse_number(row.get("Days to resolve", ""))
        if days_to_resolve is not None:
            days_to_resolve_values.append(days_to_resolve)

        attachment_counts = {col: _parse_int(row.get(col, "0")) for col in ATTACHMENT_COUNT_COLUMNS}
        any_attachment = any(v > 0 for v in attachment_counts.values())
        if any_attachment:
            records_with_any_attachment += 1

        for col, count in attachment_counts.items():
            if count > 0:
                attachment_column_coverage[col] += 1

        score, passed_flags = _compute_candidate_score(row, any_attachment)
        candidates.append(
            {
                "row": row,
                "score": score,
                "passed_flags": passed_flags,
                "attachment_counts": attachment_counts,
                "created_date": created,
            }
        )

    if created_dates:
        min_created_date = min(created_dates).isoformat()
        max_created_date = max(created_dates).isoformat()
    else:
        min_created_date = None
        max_created_date = None

    resolution_stats: dict[str, Any] = {
        "count": len(days_to_resolve_values),
        "min": min(days_to_resolve_values) if days_to_resolve_values else None,
        "max": max(days_to_resolve_values) if days_to_resolve_values else None,
        "mean": round(mean(days_to_resolve_values), 2) if days_to_resolve_values else None,
        "median": None,
    }

    if days_to_resolve_values:
        sorted_vals = sorted(days_to_resolve_values)
        n = len(sorted_vals)
        mid = n // 2
        if n % 2 == 0:
            median = (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
        else:
            median = sorted_vals[mid]
        resolution_stats["median"] = round(median, 2)

    sorted_candidates = sorted(
        candidates,
        key=lambda c: (
            c["score"],
            1 if _is_non_empty(c["row"].get("Urgency", "")) else 0,
            1 if _is_non_empty(c["row"].get("Incidence red flags", "")) else 0,
            1 if _is_non_empty(c["row"].get("Description", "")) else 0,
            c["created_date"] or date.min,
        ),
        reverse=True,
    )

    top_30 = sorted_candidates[:30]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    candidate_columns = [
        "candidate_score",
        "candidate_passed_flags",
        "any_attachment",
        *ATTACHMENT_COUNT_COLUMNS,
        "airtable_record_id",
        "airtable_created_time",
        "Created date",
        "Resolved date",
        "Days to resolve",
        "UNIQUE ID",
        "Urgency",
        "Status",
        "Type",
        "Type Old",
        "Renovator name",
        "Property manager",
        "Area cluster",
        "Origin",
        "Description",
        "Incidence red flags",
        "Solution description",
    ]

    with OUTPUT_CANDIDATES.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=candidate_columns)
        writer.writeheader()
        for item in top_30:
            row = item["row"]
            out = {col: row.get(col, "") for col in candidate_columns}
            out["candidate_score"] = str(item["score"])
            out["candidate_passed_flags"] = "|".join(item["passed_flags"])
            out["any_attachment"] = "1" if any(
                item["attachment_counts"][c] > 0 for c in ATTACHMENT_COUNT_COLUMNS
            ) else "0"
            for col in ATTACHMENT_COUNT_COLUMNS:
                out[col] = str(item["attachment_counts"][col])
            writer.writerow(out)

    attachment_payload_record_count = len(attachments_payload.get("records", {}))

    profile_summary = {
        "inputs": {
            "incidences_csv": str(INPUT_CSV),
            "attachments_json": str(INPUT_ATTACHMENTS),
        },
        "overview": {
            "row_count": row_count,
            "column_count": column_count,
            "columns": columns,
        },
        "completeness": completeness,
        "top_values": top_values,
        "date_profile": {
            "created_date_min": min_created_date,
            "created_date_max": max_created_date,
            "created_date_parsed_count": len(created_dates),
            "resolved_date_parsed_count": len(resolved_dates),
            "created_date_parse_failures": created_parse_failures,
            "resolved_date_parse_failures": resolved_parse_failures,
        },
        "resolution_stats_days_to_resolve": resolution_stats,
        "attachment_coverage": {
            "records_with_any_attachment": records_with_any_attachment,
            "records_with_any_attachment_pct": round(
                (records_with_any_attachment / row_count * 100.0) if row_count else 0.0, 2
            ),
            "fields": {
                col: {
                    "records_with_attachments": count,
                    "coverage_pct": round((count / row_count * 100.0) if row_count else 0.0, 2),
                }
                for col, count in attachment_column_coverage.items()
            },
            "attachment_metadata_records": attachment_payload_record_count,
        },
        "candidate_selection": {
            "criteria": [
                "has Description",
                "has Urgency",
                "has Status",
                "has UNIQUE ID",
                "has at least one attachment",
                "has Renovator name",
                "has Days to resolve or Resolved date",
                "has Incidence red flags or Solution description",
            ],
            "selected_count": len(top_30),
            "max_possible_score": 8,
        },
        "outputs": {
            "profile_summary_json": str(OUTPUT_SUMMARY),
            "demo_candidate_incidents_csv": str(OUTPUT_CANDIDATES),
        },
    }

    with OUTPUT_SUMMARY.open("w", encoding="utf-8") as f:
        json.dump(profile_summary, f, indent=2, ensure_ascii=False)

    print("Completeness by column:")
    for col in columns:
        c = completeness[col]
        print(f"  - {col}: {c['non_empty']}/{c['total']} ({c['completeness_pct']:.2f}%)")

    print("Top values:")
    for field in TOP_VALUE_FIELDS:
        print(f"  - {field}:")
        entries = top_values[field]
        if not entries:
            print("    (no values)")
            continue
        for entry in entries[:10]:
            print(f"    {entry['value']}: {entry['count']}")

    print(f"Created date min: {min_created_date}")
    print(f"Created date max: {max_created_date}")

    print("Resolution statistics (Days to resolve):")
    print(
        "  "
        f"count={resolution_stats['count']}, "
        f"min={resolution_stats['min']}, "
        f"max={resolution_stats['max']}, "
        f"mean={resolution_stats['mean']}, "
        f"median={resolution_stats['median']}"
    )

    print("Attachment coverage:")
    print(f"  - any attachment: {records_with_any_attachment}/{row_count}")
    for col in ATTACHMENT_COUNT_COLUMNS:
        count = attachment_column_coverage[col]
        print(f"  - {col}: {count}/{row_count}")

    print("Top 10 candidate incidents:")
    for item in sorted_candidates[:10]:
        row = item["row"]
        counts = item["attachment_counts"]
        counts_text = ", ".join(f"{k}={v}" for k, v in counts.items())
        print(
            "  - "
            f"airtable_record_id={row.get('airtable_record_id', '')} | "
            f"Created date={row.get('Created date', '')} | "
            f"UNIQUE ID={_value_for_display(row.get('UNIQUE ID', ''))} | "
            f"Urgency={_value_for_display(row.get('Urgency', ''))} | "
            f"Status={_value_for_display(row.get('Status', ''))} | "
            f"Type={_value_for_display(row.get('Type', ''))} | "
            f"Type Old={_value_for_display(row.get('Type Old', ''))} | "
            f"Renovator name={_value_for_display(row.get('Renovator name', ''))} | "
            f"attachments=({counts_text}) | "
            f"Description preview={_description_preview(row.get('Description', ''))}"
        )

    print(f"Saved: {OUTPUT_SUMMARY}")
    print(f"Saved: {OUTPUT_CANDIDATES}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
