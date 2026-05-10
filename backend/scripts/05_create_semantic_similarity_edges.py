#!/usr/bin/env python3
"""Create semantic SIMILAR_TO edges using incident description embeddings."""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from neo4j import GraphDatabase


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
SOURCE_VALUE = "airtable_enriched_sample"
MANAGED_LABEL = "TriageFixManaged"


@dataclass
class IncidentRow:
    incident_id: str
    clean_description: str
    status: str
    created_date: str | None


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


def _parse_float(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid float for {name}: {raw}") from exc


def _parse_int(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {name}: {raw}") from exc


def _normalize(v: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v))
    if norm == 0:
        return v
    return [x / norm for x in v]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    return sum(x * y for x, y in zip(a, b))


def _is_valid_text(s: str | None) -> bool:
    text = (s or "").strip()
    return len(text) >= 20


def _fetch_incidents(session: Any, statuses: list[str], created_cutoff: str) -> tuple[list[IncidentRow], list[IncidentRow]]:
    source_rows = session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HAS_STATUS]->(s:Status)
        WHERE s.name IN $statuses OR (i.created_date IS NOT NULL AND i.created_date >= $created_cutoff)
        RETURN i.incident_id AS incident_id,
               i.clean_description AS clean_description,
               s.name AS status,
               i.created_date AS created_date
        """,
        source=SOURCE_VALUE,
        statuses=statuses,
        created_cutoff=created_cutoff,
    )

    target_rows = session.run(
        f"""
        MATCH (i:Incident:{MANAGED_LABEL} {{source: $source}})-[:HAS_STATUS]->(s:Status)
        WHERE s.name IN ['Resolved', 'Resolved - Partner']
        RETURN i.incident_id AS incident_id,
               i.clean_description AS clean_description,
               s.name AS status,
               i.created_date AS created_date
        """,
        source=SOURCE_VALUE,
    )

    sources = [
        IncidentRow(
            incident_id=r["incident_id"],
            clean_description=r["clean_description"] or "",
            status=r["status"] or "unknown",
            created_date=r["created_date"],
        )
        for r in source_rows
    ]
    targets = [
        IncidentRow(
            incident_id=r["incident_id"],
            clean_description=r["clean_description"] or "",
            status=r["status"] or "unknown",
            created_date=r["created_date"],
        )
        for r in target_rows
    ]
    return sources, targets


def _embed_sentence_transformers(texts: list[str], model_name: str) -> list[list[float]]:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    vectors = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


def _embed_openai(texts: list[str], model_name: str, api_key: str) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        body = json.dumps({"model": model_name, "input": text}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI embeddings request failed: {exc}") from exc
        data = payload.get("data", [])
        if not data:
            raise RuntimeError("OpenAI embeddings response missing data")
        vectors.append([float(x) for x in data[0]["embedding"]])
    return [_normalize(v) for v in vectors]


def _create_edges(
    session: Any,
    provider: str,
    model_name: str,
    top_k: int,
    threshold: float,
    sources: list[IncidentRow],
    source_vecs: dict[str, list[float]],
    targets: list[IncidentRow],
    target_vecs: dict[str, list[float]],
) -> tuple[int, list[float], dict[str, int]]:
    # Remove previous SIMILAR_TO edges created for this managed source.
    session.run(
        f"""
        MATCH (:Incident:{MANAGED_LABEL} {{source: $source}})-[r:SIMILAR_TO]->(:Incident:{MANAGED_LABEL} {{source: $source}})
        DELETE r
        """,
        source=SOURCE_VALUE,
    ).consume()

    created = 0
    scores: list[float] = []
    source_match_counts: dict[str, int] = {}

    target_by_id = {t.incident_id: t for t in targets}

    for src in sources:
        src_vec = source_vecs.get(src.incident_id)
        if not src_vec:
            continue

        candidates: list[tuple[float, str]] = []
        for tgt in targets:
            if tgt.incident_id == src.incident_id:
                continue
            tgt_vec = target_vecs.get(tgt.incident_id)
            if not tgt_vec:
                continue
            if src.created_date and tgt.created_date and tgt.created_date > src.created_date:
                continue
            sim = _cosine(src_vec, tgt_vec)
            if sim >= threshold:
                candidates.append((sim, tgt.incident_id))

        candidates.sort(key=lambda x: x[0], reverse=True)
        chosen = candidates[:top_k]

        if not chosen:
            source_match_counts[src.incident_id] = 0
            continue

        source_match_counts[src.incident_id] = len(chosen)

        for sim, target_id in chosen:
            tgt = target_by_id[target_id]
            session.run(
                f"""
                MATCH (s:Incident:{MANAGED_LABEL} {{source: $source, incident_id: $source_id}})
                MATCH (t:Incident:{MANAGED_LABEL} {{source: $source, incident_id: $target_id}})
                MERGE (s)-[r:SIMILAR_TO]->(t)
                SET r.reason = 'embedding_description_similarity',
                    r.similarity_method = 'embedding',
                    r.embedding_provider = $embedding_provider,
                    r.embedding_model = $embedding_model,
                    r.similarity_score = $similarity_score,
                    r.source_text_field = 'clean_description',
                    r.source_status = $source_status,
                    r.target_status = $target_status
                """,
                source=SOURCE_VALUE,
                source_id=src.incident_id,
                target_id=target_id,
                embedding_provider=provider,
                embedding_model=model_name,
                similarity_score=float(sim),
                source_status=src.status,
                target_status=tgt.status,
            ).consume()
            created += 1
            scores.append(float(sim))

    return created, scores, source_match_counts


def main() -> int:
    _load_dotenv_if_available()

    provider = os.getenv("EMBEDDING_PROVIDER", "sentence_transformers").strip() or "sentence_transformers"
    model_name = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip() or "sentence-transformers/all-MiniLM-L6-v2"
    top_k = _parse_int("SIMILARITY_TOP_K", "5")
    threshold = _parse_float("SIMILARITY_THRESHOLD", "0.55")

    if top_k <= 0:
        raise ValueError("SIMILARITY_TOP_K must be > 0")

    uri = _require_env("NEO4J_URI")
    username = _require_env("NEO4J_USERNAME")
    password = _require_env("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE", "neo4j").strip() or "neo4j"

    source_statuses = [
        "Follow up",
        "Pending - Partner",
        "Action required",
        "Pending to evaluate",
        "unknown",
    ]
    created_cutoff = "2026-04-01"

    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        with driver.session(database=database) as session:
            sources_all, targets_all = _fetch_incidents(session, source_statuses, created_cutoff)

            sources = [s for s in sources_all if _is_valid_text(s.clean_description)]
            targets = [t for t in targets_all if _is_valid_text(t.clean_description)]

            source_texts = [s.clean_description for s in sources]
            target_texts = [t.clean_description for t in targets]

            if provider == "openai":
                api_key = _require_env("OPENAI_API_KEY")
                source_vectors = _embed_openai(source_texts, model_name, api_key)
                target_vectors = _embed_openai(target_texts, model_name, api_key)
            else:
                source_vectors = _embed_sentence_transformers(source_texts, model_name)
                target_vectors = _embed_sentence_transformers(target_texts, model_name)

            source_vecs = {s.incident_id: v for s, v in zip(sources, source_vectors)}
            target_vecs = {t.incident_id: v for t, v in zip(targets, target_vectors)}

            created_count, scores, source_match_counts = _create_edges(
                session,
                provider=provider,
                model_name=model_name,
                top_k=top_k,
                threshold=threshold,
                sources=sources,
                source_vecs=source_vecs,
                targets=targets,
                target_vecs=target_vecs,
            )

            score_min = min(scores) if scores else None
            score_mean = mean(scores) if scores else None
            score_max = max(scores) if scores else None

            top_sources = sorted(source_match_counts.items(), key=lambda x: x[1], reverse=True)[:10]

            print(f"embedding provider: {provider}")
            print(f"embedding model: {model_name}")
            print(f"number of source incidents: {len(sources)}")
            print(f"number of target incidents: {len(targets)}")
            print(f"number of SIMILAR_TO relationships created: {created_count}")
            print(f"similarity score min/mean/max: {score_min} / {score_mean} / {score_max}")
            print("top 10 source incidents with created matches:")
            for incident_id, match_count in top_sources:
                print(f"  - {incident_id}: {match_count}")

    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
