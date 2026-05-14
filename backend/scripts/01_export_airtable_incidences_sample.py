#!/usr/bin/env python3
"""Export Airtable Incidences to CSV and attachment metadata JSON."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OUTPUT_DIR = REPO_ROOT / "data" / "airtable_full"
CSV_PATH = OUTPUT_DIR / "incidences_full.csv"
ATTACHMENTS_PATH = OUTPUT_DIR / "incidence_attachments_metadata_full.json"


NON_ATTACHMENT_FIELDS = [
    "Description",
    "Status",
    "Urgency",
    "Type",
    "Type Old",
    "Created date",
    "Resolved date",
    "Days to resolve",
    "Days to evaluate",
    "Cost",
    "Cost Responsibility",
    "Origin",
    "Internal notes",
    "Incidence red flags",
    "Technical construction",
    "Solution description",
    "Renovator name",
    "Property manager",
    "Area cluster",
    "UNIQUE ID",
    "Transaction Name",
    "Transaction Name Resolved",
    "Property Ready Date",
    "TECH - Budget Attachment (URLs)",
    "Furniture budget doc",
]

ATTACHMENT_FIELDS = [
    "Incidence docs",
    "Lease Contract",
    "Furniture budget doc",
    "Finance Invoice",
]


def _load_dotenv_if_available() -> None:
    """Load .env files when python-dotenv is available; otherwise rely on shell env."""
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


def _get_airtable_token() -> str:
    for key in ("AIRTABLE_API_KEY", "AIRTABLE_TOKEN", "AIRTABLE_PAT"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    raise ValueError(
        "Missing Airtable token. Set one of: AIRTABLE_API_KEY, AIRTABLE_TOKEN, AIRTABLE_PAT"
    )


def _to_cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _extract_linked_record_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            return []
    return []


def _build_url(base_id: str, table_name: str, max_records: int, view_name: str | None, offset: str | None) -> str:
    params: dict[str, Any] = {
        "pageSize": min(100, max_records),
    }
    if view_name:
        params["view"] = view_name
    if offset:
        params["offset"] = offset

    encoded_table = quote(table_name, safe="")
    return f"https://api.airtable.com/v0/{base_id}/{encoded_table}?{urlencode(params)}"


def _fetch_records(base_id: str, table_name: str, token: str, view_name: str | None, max_records: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset: str | None = None

    while len(records) < max_records:
        url = _build_url(base_id, table_name, max_records, view_name, offset)
        request = Request(url)
        request.add_header("Authorization", f"Bearer {token}")

        with urlopen(request, timeout=30) as response:  # nosec B310
            payload = json.loads(response.read().decode("utf-8"))

        page_records = payload.get("records", [])
        remaining = max_records - len(records)
        records.extend(page_records[:remaining])

        offset = payload.get("offset")
        if not offset or not page_records:
            break

    return records


def _fetch_record_by_id(base_id: str, table_name: str, token: str, record_id: str) -> dict[str, Any] | None:
    encoded_table = quote(table_name, safe="")
    encoded_record = quote(record_id, safe="")
    url = f"https://api.airtable.com/v0/{base_id}/{encoded_table}/{encoded_record}"
    request = Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _pick_resolved_transaction_text(fields: dict[str, Any], preferred_field: str | None) -> str:
    if preferred_field:
        value = fields.get(preferred_field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("Transaction Name", "Name", "Address", "Full Address", "Property Address"):
        value = fields.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for value in fields.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def main() -> int:
    _load_dotenv_if_available()

    try:
        token = _get_airtable_token()
        base_id = _require_env("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Incidences").strip() or "Incidences"
        view_name = os.getenv("AIRTABLE_VIEW_NAME", "").strip() or None
        transaction_table_name = os.getenv("AIRTABLE_TRANSACTION_TABLE_NAME", "Transactions").strip() or "Transactions"
        transaction_text_field = os.getenv("AIRTABLE_TRANSACTION_TEXT_FIELD", "").strip() or None
        max_records = int(os.getenv("AIRTABLE_MAX_RECORDS", "50000"))
        if max_records <= 0:
            raise ValueError("AIRTABLE_MAX_RECORDS must be a positive integer")
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        records = _fetch_records(
            base_id=base_id,
            table_name=table_name,
            token=token,
            view_name=view_name,
            max_records=max_records,
        )
    except Exception as exc:
        print(f"Airtable API request failed: {exc}", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_columns = [
        "airtable_record_id",
        "airtable_created_time",
        *NON_ATTACHMENT_FIELDS,
        *(f"{field}__attachment_count" for field in ATTACHMENT_FIELDS),
    ]

    attachment_metadata: dict[str, dict[str, Any]] = {}
    csv_rows: list[dict[str, str]] = []

    completeness_fields = [
        "Description",
        "Status",
        "Urgency",
        "Type",
        "Created date",
        "Resolved date",
        "UNIQUE ID",
    ]
    completeness_counts = {field: 0 for field in completeness_fields}
    records_with_attachments = 0
    resolved_transaction_cache: dict[str, str] = {}

    for record in records:
        fields = record.get("fields", {})
        row: dict[str, str] = {
            "airtable_record_id": _to_cell_value(record.get("id", "")),
            "airtable_created_time": _to_cell_value(record.get("createdTime", "")),
        }

        for field in NON_ATTACHMENT_FIELDS:
            value = fields.get(field)
            row[field] = _to_cell_value(value)

        # Resolve linked-record IDs in Transaction Name into readable text.
        transaction_value = fields.get("Transaction Name")
        linked_ids = _extract_linked_record_ids(transaction_value)
        resolved_values: list[str] = []
        for linked_id in linked_ids:
            if linked_id in resolved_transaction_cache:
                resolved_text = resolved_transaction_cache[linked_id]
            else:
                linked_record = _fetch_record_by_id(
                    base_id=base_id,
                    table_name=transaction_table_name,
                    token=token,
                    record_id=linked_id,
                )
                linked_fields = linked_record.get("fields", {}) if isinstance(linked_record, dict) else {}
                resolved_text = _pick_resolved_transaction_text(linked_fields, transaction_text_field)
                resolved_transaction_cache[linked_id] = resolved_text
            if resolved_text:
                resolved_values.append(resolved_text)
        row["Transaction Name Resolved"] = " | ".join(dict.fromkeys(resolved_values))

        has_attachment = False
        per_record_attachments: dict[str, Any] = {}
        for field in ATTACHMENT_FIELDS:
            value = fields.get(field)
            count = len(value) if isinstance(value, list) else 0
            row[f"{field}__attachment_count"] = str(count)
            if count > 0:
                has_attachment = True
                per_record_attachments[field] = value

        if has_attachment:
            records_with_attachments += 1

        if per_record_attachments:
            attachment_metadata[row["airtable_record_id"]] = {
                "airtable_record_id": row["airtable_record_id"],
                "airtable_created_time": row["airtable_created_time"],
                "attachments": per_record_attachments,
            }

        for field in completeness_fields:
            if row[field].strip():
                completeness_counts[field] += 1

        csv_rows.append(row)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_columns)
        writer.writeheader()
        writer.writerows(csv_rows)

    with ATTACHMENTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "source": {
                    "base_id": base_id,
                    "table_name": table_name,
                    "view_name": view_name,
                    "max_records_requested": max_records,
                    "records_exported": len(csv_rows),
                },
                "attachment_fields": ATTACHMENT_FIELDS,
                "records": attachment_metadata,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    print(f"Records exported: {len(csv_rows)}")
    print(f"CSV output: {CSV_PATH}")
    print(f"Attachment metadata output: {ATTACHMENTS_PATH}")
    print(f"Columns exported ({len(csv_columns)}): {', '.join(csv_columns)}")
    print("Completeness summary for key fields:")
    for field in completeness_fields:
        count = completeness_counts[field]
        pct = (count / len(csv_rows) * 100.0) if csv_rows else 0.0
        print(f"  - {field}: {count}/{len(csv_rows)} ({pct:.1f}%)")
    print(f"Records with attachments: {records_with_attachments}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
