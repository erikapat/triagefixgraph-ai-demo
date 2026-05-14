"""API routes for TriageFixGraph AI."""

from __future__ import annotations

import asyncio
import json
import os
import uuid as _uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.agent import handle_message
from app.config import settings
from app.context_graph_client import (
    execute_cypher, search_entities, get_entity_graph, get_schema,
    get_schema_visualization, expand_node, get_collector, is_connected,
)
from app.gds_client import check_gds_available, run_community_detection, run_pagerank

# Try to import streaming handler (only available for some agent frameworks)
try:
    from app.agent import handle_message_stream  # type: ignore[attr-defined]
except ImportError:
    handle_message_stream = None

router = APIRouter()


def _require_neo4j():
    """Raise 503 if Neo4j is not connected."""
    if not is_connected():
        raise HTTPException(
            status_code=503,
            detail="Neo4j is unavailable. Check your database connection and restart the server.",
        )


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=4000)
    session_id: str | None = None
    graph_view_mode: str | None = None
    selected_incident_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    graph_data: dict | None = None
    tool_calls: list[dict] | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., max_length=2000)
    label: str | None = None
    limit: int = 20


class CypherRequest(BaseModel):
    query: str
    parameters: dict | None = None


class ExpandRequest(BaseModel):
    element_id: str


def _build_graph_view_context(request: ChatRequest) -> str:
    mode = (request.graph_view_mode or "").strip().lower()
    incident_id = (request.selected_incident_id or "").strip()

    if mode not in {"incident", "decision", "schema"}:
        mode = "incident"

    context_lines = [f"[UI_GRAPH_VIEW_MODE={mode}]"]
    if incident_id:
        context_lines.append(f"[UI_SELECTED_INCIDENT_ID={incident_id}]")

    if mode == "decision":
        context_lines.append(
            "[INSTRUCTION=Respond using decision-flow reasoning and describe the selected decision path when relevant.]"
        )
    elif mode == "incident":
        context_lines.append(
            "[INSTRUCTION=Respond using incident local-context reasoning and graph neighbors for the selected incident when relevant.]"
        )
    else:
        context_lines.append(
            "[INSTRUCTION=User is viewing schema. Prioritize schema-level explanation unless user asks for a specific incident.]"
        )

    return "\n".join(context_lines)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a message to the AI agent."""
    _require_neo4j()
    try:
        collector = get_collector()
        collector.drain()  # clear stale results
        collector.drain_tool_calls()  # clear stale tool calls
        contextual_message = f"{_build_graph_view_context(request)}\n\nUser message:\n{request.message}"
        result = await handle_message(contextual_message, request.session_id)
        # Attach graph data from tool calls if agent didn't provide any
        if result.get("graph_data") is None:
            collected = collector.drain()
            if collected:
                result["graph_data"] = {"results": collected}
        # Attach tool call metadata for frontend visualization
        tool_calls = collector.drain_tool_calls()
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """Send a message to the AI agent with streaming SSE response.

    Emits Server-Sent Events:
      - session_id: {session_id}
      - tool_start: {name, inputs}
      - tool_end:   {name, output_preview, graph_data}
      - text_delta: {text}
      - entities_extracted: {entities}
      - preferences_detected: {preferences}
      - done:       {response}
      - error:      {detail}
    """
    _require_neo4j()

    session_id = request.session_id or str(_uuid.uuid4())
    collector = get_collector()
    collector.drain()
    collector.drain_tool_calls()

    event_queue: asyncio.Queue = asyncio.Queue()
    collector.set_event_queue(event_queue)

    async def run_agent():
        try:
            contextual_message = f"{_build_graph_view_context(request)}\n\nUser message:\n{request.message}"
            if handle_message_stream is not None:
                await handle_message_stream(contextual_message, session_id)
            else:
                result = await handle_message(contextual_message, session_id)
                response_text = result.get("response", "")
                collector.emit_text_delta(response_text)
                # Emit extraction events if present
                if result.get("entities_extracted"):
                    collector.emit_entities_extracted(result["entities_extracted"])
                if result.get("preferences_detected"):
                    collector.emit_preferences_detected(result["preferences_detected"])
                collector.emit_done(response_text, session_id)
        except Exception as e:
            try:
                event_queue.put_nowait({"event": "error", "data": {"detail": str(e)}})
            except Exception:
                pass
        finally:
            # Small delay to ensure events are consumed before clearing
            await asyncio.sleep(0.1)
            collector.clear_event_queue()

    async def event_generator():
        task = asyncio.create_task(run_agent())
        # Emit session_id as first event
        yield f"event: session_id\ndata: {json.dumps({'session_id': session_id})}\n\n"
        idle_timeout = 120.0  # Max seconds between events
        overall_timeout = 300.0  # 5 min total max
        loop = asyncio.get_event_loop()
        start_time = loop.time()
        try:
            while True:
                elapsed = loop.time() - start_time
                if elapsed > overall_timeout:
                    yield f"event: error\ndata: {json.dumps({'detail': 'Request exceeded maximum duration'})}\n\n"
                    break
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    yield f"event: error\ndata: {json.dumps({'detail': 'Request timed out'})}\n\n"
                    break
                event_type = event["event"]
                event_data = json.dumps(event["data"], default=str)
                yield f"event: {event_type}\ndata: {event_data}\n\n"
                if event_type in ("done", "error"):
                    break
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/search")
async def search(request: SearchRequest):
    """Search entities in the knowledge graph."""
    _require_neo4j()
    results = await search_entities(request.query, request.label, request.limit)
    return {"results": results}


@router.get("/graph/{entity_name}")
async def graph(entity_name: str, depth: int = 2):
    """Get the subgraph around an entity."""
    _require_neo4j()
    data = await get_entity_graph(entity_name, depth)
    return data


@router.get("/schema")
async def schema():
    """Get the graph database schema."""
    _require_neo4j()
    return await get_schema()


@router.get("/schema/visualization")
async def schema_visualization():
    """Get the graph schema as nodes and relationships for visualization."""
    _require_neo4j()
    return await get_schema_visualization()


@router.post("/expand")
async def expand(request: ExpandRequest):
    """Expand a node to show its immediate neighbors."""
    _require_neo4j()
    return await expand_node(request.element_id)


@router.post("/cypher")
async def cypher(request: CypherRequest):
    """Execute a Cypher query."""
    _require_neo4j()
    try:
        params = dict(request.parameters or {})
        params.setdefault("domain", settings.domain_id)
        results = await execute_cypher(request.query, params)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/gds/status")
async def gds_status():
    """Check if GDS is available."""
    _require_neo4j()
    available = await check_gds_available()
    return {"gds_available": available}


@router.get("/gds/communities")
async def communities():
    """Run community detection."""
    _require_neo4j()
    results = await run_community_detection()
    return {"communities": results}


@router.get("/gds/pagerank")
async def pagerank():
    """Run PageRank centrality."""
    _require_neo4j()
    results = await run_pagerank()
    return {"pagerank": results}


@router.get("/documents")
async def list_documents(template_id: str | None = None, skip: int = 0, limit: int = 50):
    """List documents, optionally filtered by template type."""
    _require_neo4j()
    if template_id:
        cypher = """
        MATCH (d:Document {template_id: $template_id})
        WHERE d.domain IS NULL OR d.domain = $domain
        OPTIONAL MATCH (d)-[:MENTIONS]->(e)
        RETURN d.title AS title, d.template_id AS template_id,
               d.template_name AS template_name,
               substring(d.content, 0, 200) AS preview,
               collect(DISTINCT {name: e.name, labels: labels(e)}) AS mentioned_entities
        ORDER BY d.title
        SKIP $skip LIMIT $limit
        """
        results = await execute_cypher(cypher, {"template_id": template_id, "skip": skip, "limit": limit, "domain": settings.domain_id})
    else:
        cypher = """
        MATCH (d:Document)
        WHERE d.domain IS NULL OR d.domain = $domain
        OPTIONAL MATCH (d)-[:MENTIONS]->(e)
        RETURN d.title AS title, d.template_id AS template_id,
               d.template_name AS template_name,
               substring(d.content, 0, 200) AS preview,
               collect(DISTINCT {name: e.name, labels: labels(e)}) AS mentioned_entities
        ORDER BY d.title
        SKIP $skip LIMIT $limit
        """
        results = await execute_cypher(cypher, {"skip": skip, "limit": limit, "domain": settings.domain_id})
    return {"documents": results}


@router.get("/documents/{title:path}")
async def get_document(title: str):
    """Get full document content by title."""
    _require_neo4j()
    cypher = """
    MATCH (d:Document {title: $title})
    WHERE d.domain IS NULL OR d.domain = $domain
    OPTIONAL MATCH (d)-[:MENTIONS]->(e)
    RETURN d {.title, .content, .template_id, .template_name} AS document,
           collect(DISTINCT {name: e.name, labels: labels(e)}) AS mentioned_entities
    """
    results = await execute_cypher(cypher, {"title": title, "domain": settings.domain_id})
    if not results:
        raise HTTPException(status_code=404, detail="Document not found")
    return results[0]


@router.get("/traces")
async def list_traces():
    """List decision traces with their full reasoning steps."""
    _require_neo4j()
    cypher = """
    MATCH (t:DecisionTrace)
    WHERE t.domain IS NULL OR t.domain = $domain
    OPTIONAL MATCH (t)-[:HAS_STEP]->(s:TraceStep)
    WITH t, s ORDER BY s.step_number
    RETURN t.id AS id, t.task AS task, t.outcome AS outcome,
           collect(CASE WHEN s IS NOT NULL THEN {
               step_number: s.step_number,
               thought: s.thought,
               action: s.action,
               observation: s.observation
           } END) AS steps
    """
    results = await execute_cypher(cypher, {"domain": settings.domain_id})
    return {"traces": results}


@router.get("/entities/{name}")
async def get_entity_detail(name: str):
    """Get full entity detail with all properties and connections."""
    _require_neo4j()
    cypher = """
    MATCH (n) WHERE toLower(n.name) = toLower($name)
      AND (n.domain IS NULL OR n.domain = $domain)
    OPTIONAL MATCH (n)-[r]-(connected)
    WHERE connected.name IS NOT NULL
      AND (connected.domain IS NULL OR connected.domain = $domain)
    RETURN n {.*, _labels: labels(n)} AS entity,
           collect(DISTINCT {
               name: connected.name,
               labels: labels(connected),
               relationship: type(r),
               direction: CASE WHEN startNode(r) = n THEN 'outgoing' ELSE 'incoming' END
           }) AS connections
    """
    results = await execute_cypher(cypher, {"name": name, "domain": settings.domain_id})
    if not results:
        raise HTTPException(status_code=404, detail="Entity not found")
    return results[0]

@router.get("/scenarios")
async def scenarios():
    """Get demo scenarios for the frontend."""
    return {
        "domain": "TriageFixGraph AI",
        "scenarios": [
            {
                "name": "Incident triage",
                "prompts": [
                    "Show the highest severity incidents",
                    "Explain this incident using graph context",
                    "What is the recommended action and why?",
                ],
            },
            {
                "name": "Historical context",
                "prompts": [
                    "Which prior similar incidents are connected?",
                    "Show incidents for the same property context",
                    "Compare severity and outcomes with earlier incidents",
                ],
            },
            {
                "name": "Provider routing",
                "prompts": [
                    "Which renovator handled similar cases?",
                    "Show renovator workload by category",
                    "Which trade specialist is recommended for urgent incidents?",
                ],
            },
            {
                "name": "Evidence and missing questions",
                "prompts": [
                    "Show evidence and severity signals for this incident",
                    "What information is missing before routing this incident?",
                    "Which missing questions are most frequent by category?",
                ],
            },
            {
                "name": "Severity explanation",
                "prompts": [
                    "Break down severity signals for this incident",
                    "Why is this incident high priority?",
                    "Which incidents combine high urgency and high material damage risk?",
                ],
            },
        ],
    }


@router.get("/triagefix/summary")
async def triagefix_summary():
    """Get summary metrics for the current TriageFix graph."""
    _require_neo4j()
    source = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"
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
    node_counts: dict[str, int] = {}
    for label in labels:
        rec = await execute_cypher(
            f"MATCH (n:{label}:TriageFixManaged {{source: $source}}) RETURN count(n) AS c",
            {"source": source},
        )
        node_counts[label] = int(rec[0]["c"]) if rec else 0

    rel_counts_rows = await execute_cypher(
        """
        MATCH (a:TriageFixManaged {source: $source})-[r]->(b:TriageFixManaged {source: $source})
        RETURN type(r) AS relationship_type, count(r) AS relationship_count
        ORDER BY relationship_count DESC, relationship_type ASC
        """,
        {"source": source},
    )

    top_categories = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source})-[:HAS_CATEGORY]->(c:Category)
        RETURN c.name AS category, count(i) AS incidents
        ORDER BY incidents DESC, category ASC
        LIMIT 10
        """,
        {"source": source},
    )

    top_renovators = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source})-[:HANDLED_BY]->(r:Renovator)
        RETURN r.name AS renovator, count(i) AS incidents
        ORDER BY incidents DESC, renovator ASC
        LIMIT 10
        """,
        {"source": source},
    )

    prior_similar_rows = await execute_cypher(
        """
        MATCH (:Incident:TriageFixManaged {source: $source})-[r:PRIOR_SIMILAR]->(:Incident:TriageFixManaged {source: $source})
        RETURN count(r) AS c
        """,
        {"source": source},
    )
    incidents_with_evidence_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source})-[:HAS_EVIDENCE]->(:Evidence)
        RETURN count(DISTINCT i) AS c
        """,
        {"source": source},
    )

    return {
        "node_counts": node_counts,
        "relationship_counts": rel_counts_rows,
        "top_categories": top_categories,
        "top_renovators": top_renovators,
        "prior_similar_relationships": int(prior_similar_rows[0]["c"]) if prior_similar_rows else 0,
        "incidents_with_evidence": int(incidents_with_evidence_rows[0]["c"]) if incidents_with_evidence_rows else 0,
    }


@router.get("/triagefix/incidents")
async def triagefix_incidents():
    """List incident selector rows from TriageFix managed graph."""
    _require_neo4j()
    source = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"
    results = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source})
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (c)-[:HAS_SUBCATEGORY]->(sc:Subcategory)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
        OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
        OPTIONAL MATCH (p)-[:HAS_UNIQUE_ID]->(uid:UniqueId)
        OPTIONAL MATCH (p)-[:HAS_TRANSACTION_NAME]->(tn_legacy:TransactionName)
        OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
        OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
        OPTIONAL MATCH (i)-[:HAS_RESOLUTION_TIME_BAND]->(rtb:ResolutionTimeBand)
        OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
        OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
        RETURN
          i.incident_id AS incident_id,
          i.created_date AS created_date,
          i.severity_average AS severity_average,
          c.name AS category,
          sc.name AS subcategory,
          u.name AS urgency,
          s.name AS status,
          i.original_unique_id AS `UNIQUE ID`,
          coalesce(uid.name, tn_legacy.name) AS transaction_name,
          coalesce(uid.name, tn_legacy.name) AS `Transaction Name`,
          p.property_context_id AS property_context_id,
          a.name AS area_cluster,
          t.name AS trade_specialist,
          r.name AS renovator,
          r.name AS provider_candidate,
          rtb.name AS resolution_time_band,
          ra.name AS recommended_action,
          coalesce(e.has_incidence_docs, false) AS has_incidence_docs,
          left(i.clean_description, 180) AS description_preview
        ORDER BY i.severity_average DESC, i.created_date DESC
        """,
        {"source": source},
    )
    return {"incidents": results}


@router.get("/triagefix/incidents/{incident_id}/context")
async def triagefix_incident_context(incident_id: str):
    """Get local context and graph payload for one incident."""
    _require_neo4j()
    source = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"
    context_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
        OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (c)-[:HAS_SUBCATEGORY]->(sc:Subcategory)
        OPTIONAL MATCH (p)-[:HAS_UNIQUE_ID]->(uid:UniqueId)
        OPTIONAL MATCH (p)-[:HAS_TRANSACTION_NAME]->(tn_legacy:TransactionName)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
        OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
        OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
        OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
        OPTIONAL MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
        OPTIONAL MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(ss:SeveritySignal)
        OPTIONAL MATCH (prior:Incident:TriageFixManaged {source: $source})-[ps:PRIOR_SIMILAR]->(i)
        RETURN
          i.incident_id AS incident_id,
          i.created_date AS created_date,
          i.resolved_date AS resolved_date,
          i.severity_average AS severity_average,
          i.provider_confidence AS provider_confidence,
          i.clean_description AS clean_description,
          p.property_context_id AS property_context_id,
          a.name AS area_cluster,
          i.original_unique_id AS `UNIQUE ID`,
          coalesce(uid.name, tn_legacy.name) AS transaction_name,
          coalesce(uid.name, tn_legacy.name) AS `Transaction Name`,
          c.name AS category,
          sc.name AS subcategory,
          u.name AS urgency,
          s.name AS status,
          ra.name AS recommended_action,
          t.name AS trade_specialist,
          r.name AS renovator,
          e.evidence_id AS evidence_id,
          e.has_incidence_docs AS has_incidence_docs,
          e.incidence_docs_count AS incidence_docs_count,
          e.lease_contract_count AS lease_contract_count,
          e.furniture_budget_count AS furniture_budget_count,
          e.finance_invoice_count AS finance_invoice_count,
          collect(DISTINCT mq.text) AS missing_questions,
          collect(DISTINCT {dimension: ss.dimension, score: ss.score}) AS severity_signals,
          collect(DISTINCT {
            incident_id: prior.incident_id,
            created_date: prior.created_date,
            severity_average: prior.severity_average,
            reason: ps.reason
          }) AS prior_similar_incidents
        """,
        {"source": source, "incident_id": incident_id},
    )
    if not context_rows:
        raise HTTPException(status_code=404, detail="Incident not found")

    graph_nodes = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        CALL (i) {
          RETURN i AS n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(n:PropertyContext:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_CATEGORY]->(n:Category:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_CATEGORY]->(:Category:TriageFixManaged {source: $source})-[:HAS_SUBCATEGORY]->(n:Subcategory:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_URGENCY]->(n:Urgency:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:RECOMMENDED_ACTION]->(n:RecommendedAction:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:REQUIRES_TRADE]->(n:TradeSpecialist:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HANDLED_BY]->(n:Renovator:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_EVIDENCE]->(n:Evidence:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:NEEDS_QUESTION]->(n:MissingQuestion:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(n:SeveritySignal:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:LOCATED_IN_AREA]->(n:AreaCluster:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_UNIQUE_ID]->(n:UniqueId:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_TRANSACTION_NAME]->(n:TransactionName:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_TRANSACTION_NAME_RESOLVED]->(n:TransactionNameResolved:TriageFixManaged {source: $source})
          RETURN n
        }
        WITH DISTINCT n
        RETURN
          elementId(n) AS id,
          head([label IN labels(n) WHERE label <> 'TriageFixManaged']) AS label,
          coalesce(
            n.incident_id,
            n.property_context_id,
            n.name,
            n.evidence_id,
            n.case_id,
            n.text,
            elementId(n)
          ) AS title,
          properties(n) AS properties
        """,
        {"source": source, "incident_id": incident_id},
    )

    graph_edges = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        CALL (i) {
          RETURN i AS n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(n:PropertyContext:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_CATEGORY]->(n:Category:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_CATEGORY]->(:Category:TriageFixManaged {source: $source})-[:HAS_SUBCATEGORY]->(n:Subcategory:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_URGENCY]->(n:Urgency:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:RECOMMENDED_ACTION]->(n:RecommendedAction:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:REQUIRES_TRADE]->(n:TradeSpecialist:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HANDLED_BY]->(n:Renovator:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_EVIDENCE]->(n:Evidence:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:NEEDS_QUESTION]->(n:MissingQuestion:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(n:SeveritySignal:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:LOCATED_IN_AREA]->(n:AreaCluster:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_UNIQUE_ID]->(n:UniqueId:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_TRANSACTION_NAME]->(n:TransactionName:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[:HAS_TRANSACTION_NAME_RESOLVED]->(n:TransactionNameResolved:TriageFixManaged {source: $source})
          RETURN n
        }
        WITH collect(DISTINCT elementId(n)) AS node_ids
        MATCH (source:TriageFixManaged {source: $source})-[r]->(target:TriageFixManaged {source: $source})
        WHERE elementId(source) IN node_ids
          AND elementId(target) IN node_ids
          AND NOT (source:Status OR target:Status)
          AND NOT (
            (type(r) = 'HAS_PROPERTY_CONTEXT' AND source:Incident AND target:PropertyContext)
            OR (type(r) = 'HAS_UNIQUE_ID' AND source:PropertyContext AND target:UniqueId)
          )
        RETURN DISTINCT
          elementId(source) AS source,
          elementId(target) AS target,
          type(r) AS type,
          properties(r) AS properties
        """,
        {"source": source, "incident_id": incident_id},
    )
    graph_payload = {"nodes": graph_nodes, "relationships": graph_edges}
    return {
        "context": context_rows[0],
        "graph": graph_payload,
    }


@router.get("/triagefix/incidents/{incident_id}/decision-context")
async def triagefix_incident_decision_context(incident_id: str):
    """Get decision-only graph payload for one incident."""
    _require_neo4j()
    source = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"
    context_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
        OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (c)-[:HAS_SUBCATEGORY]->(sc:Subcategory)
        OPTIONAL MATCH (p)-[:HAS_UNIQUE_ID]->(uid:UniqueId)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
        OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
        OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
        OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
        RETURN
          i.incident_id AS incident_id,
          i.created_date AS created_date,
          i.resolved_date AS resolved_date,
          i.severity_average AS severity_average,
          i.provider_confidence AS provider_confidence,
          i.clean_description AS clean_description,
          p.property_context_id AS property_context_id,
          a.name AS area_cluster,
          i.original_unique_id AS `UNIQUE ID`,
          uid.name AS transaction_name,
          uid.name AS `Transaction Name`,
          c.name AS category,
          sc.name AS subcategory,
          u.name AS urgency,
          s.name AS status,
          ra.name AS recommended_action,
          t.name AS trade_specialist,
          r.name AS renovator,
          e.evidence_id AS evidence_id,
          e.has_incidence_docs AS has_incidence_docs,
          e.incidence_docs_count AS incidence_docs_count,
          e.lease_contract_count AS lease_contract_count,
          e.furniture_budget_count AS furniture_budget_count,
          e.finance_invoice_count AS finance_invoice_count
        """,
        {"source": source, "incident_id": incident_id},
    )
    if not context_rows:
        raise HTTPException(status_code=404, detail="Incident not found")

    graph_nodes = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        CALL (i) {
          RETURN i AS n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(n:PropertyContext:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_UNIQUE_ID]->(n:UniqueId:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_TRANSACTION_NAME_RESOLVED]->(n:TransactionNameResolved:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (n:TriageFixManaged:DecisionGraph {source: $source})
          WHERE coalesce(n.node_id, '') STARTS WITH $incident_prefix
            AND EXISTS {
              MATCH (i)-[r*1..8]-(n)
              WHERE ALL(rel IN r WHERE type(rel) STARTS WITH 'DECISION_' AND coalesce(rel.active, true))
            }
          RETURN n
        }
        WITH DISTINCT n
        RETURN
          elementId(n) AS id,
          head([label IN labels(n) WHERE label <> 'TriageFixManaged']) AS label,
          coalesce(
            n.incident_id,
            n.property_context_id,
            n.name,
            n.evidence_id,
            n.case_id,
            n.text,
            elementId(n)
          ) AS title,
          properties(n) AS properties
        """,
        {"source": source, "incident_id": incident_id, "incident_prefix": f"{incident_id}::"},
    )

    graph_edges = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        CALL (i) {
          RETURN i AS n

          UNION

          MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(n:PropertyContext:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_UNIQUE_ID]->(n:UniqueId:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (i)-[:HAS_TRANSACTION_NAME_RESOLVED]->(n:TransactionNameResolved:TriageFixManaged {source: $source})
          RETURN n

          UNION

          MATCH (n:TriageFixManaged:DecisionGraph {source: $source})
          WHERE coalesce(n.node_id, '') STARTS WITH $incident_prefix
            AND EXISTS {
              MATCH (i)-[r*1..8]-(n)
              WHERE ALL(rel IN r WHERE type(rel) STARTS WITH 'DECISION_' AND coalesce(rel.active, true))
            }
          RETURN n
        }
        WITH collect(DISTINCT elementId(n)) AS node_ids
        MATCH (source:TriageFixManaged {source: $source})-[r]->(target:TriageFixManaged {source: $source})
        WHERE elementId(source) IN node_ids
          AND elementId(target) IN node_ids
          AND NOT (
            (type(r) = 'HAS_PROPERTY_CONTEXT' AND source:Incident AND target:PropertyContext)
            OR (type(r) = 'HAS_UNIQUE_ID' AND source:PropertyContext AND target:UniqueId)
          )
          AND (
            (type(r) STARTS WITH 'DECISION_' AND coalesce(r.active, true))
            OR type(r) IN [
              'HAS_PROPERTY_CONTEXT',
              'HAS_UNIQUE_ID',
              'HAS_TRANSACTION_NAME_RESOLVED',
              'RESOLVES_TO_UNIQUE_ID',
              'IDENTIFIES_PROPERTY_CONTEXT'
            ]
          )
        RETURN DISTINCT
          elementId(source) AS source,
          elementId(target) AS target,
          type(r) AS type,
          properties(r) AS properties
        """,
        {"source": source, "incident_id": incident_id, "incident_prefix": f"{incident_id}::"},
    )

    return {"context": context_rows[0], "graph": {"nodes": graph_nodes, "relationships": graph_edges}}


@router.get("/triagefix/incidents/{incident_id}/decision-support")
async def triagefix_incident_decision_support(incident_id: str):
    """Get selected-incident operational decision support for the right panel."""
    _require_neo4j()
    source = os.getenv("TRIAGEFIX_GRAPH_SOURCE", "airtable_enriched_full").strip() or "airtable_enriched_full"

    incident_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
        OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (c)-[:HAS_SUBCATEGORY]->(sc:Subcategory)
        OPTIONAL MATCH (p)-[:HAS_UNIQUE_ID]->(uid:UniqueId)
        OPTIONAL MATCH (p)-[:HAS_TRANSACTION_NAME]->(tn_legacy:TransactionName)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
        OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
        OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
        OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
        RETURN
          i.incident_id AS incident_id,
          i.created_date AS created_date,
          i.severity_average AS severity_average,
          i.provider_confidence AS provider_confidence,
          i.clean_description AS clean_description,
          p.property_context_id AS property_context_id,
          a.name AS area_cluster,
          i.original_unique_id AS `UNIQUE ID`,
          coalesce(uid.name, tn_legacy.name) AS transaction_name,
          coalesce(uid.name, tn_legacy.name) AS `Transaction Name`,
          c.name AS category,
          sc.name AS subcategory,
          u.name AS urgency,
          s.name AS status,
          ra.name AS recommended_action,
          t.name AS recommended_trade,
          r.name AS provider,
          e.has_incidence_docs AS has_incidence_docs,
          e.incidence_docs_count AS incidence_docs_count,
          e.lease_contract_count AS lease_contract_count,
          e.furniture_budget_count AS furniture_budget_count,
          e.finance_invoice_count AS finance_invoice_count
        """,
        {"source": source, "incident_id": incident_id},
    )
    if not incident_rows:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident = incident_rows[0]
    property_context_id = incident.get("property_context_id")

    severity_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        OPTIONAL MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(ss:SeveritySignal)
        RETURN ss.dimension AS dimension, ss.score AS score
        ORDER BY ss.dimension ASC
        """,
        {"source": source, "incident_id": incident_id},
    )
    severity_signals = [
        {"dimension": row["dimension"], "score": row["score"]}
        for row in severity_rows
        if row.get("dimension")
    ]

    missing_questions_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
        OPTIONAL MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
        RETURN collect(DISTINCT mq.text) AS questions
        """,
        {"source": source, "incident_id": incident_id},
    )
    missing_questions = (
        [q for q in (missing_questions_rows[0].get("questions") or []) if q]
        if missing_questions_rows
        else []
    )

    prior_summary = {
        "prior_same_property_count": 0,
        "prior_same_property_categories": [],
        "latest_prior_incident_date": None,
    }
    prior_same_property_rows: list[dict] = []
    if property_context_id:
        prior_counts = await execute_cypher(
            """
            MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
            MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext {property_context_id: $property_context_id})
            MATCH (older:Incident:TriageFixManaged {source: $source})-[:HAS_PROPERTY_CONTEXT]->(p)
            WHERE older.incident_id <> i.incident_id
              AND older.created_date IS NOT NULL
              AND i.created_date IS NOT NULL
              AND date(older.created_date) < date(i.created_date)
            WITH collect(DISTINCT older) AS priors, max(older.created_date) AS latest_prior_incident_date
            CALL (priors) {
              UNWIND priors AS o
              OPTIONAL MATCH (o)-[:HAS_CATEGORY]->(oc:Category)
              WITH DISTINCT oc.name AS category
              WHERE category IS NOT NULL
              RETURN collect(category) AS prior_categories
            }
            RETURN
              size(priors) AS prior_count,
              prior_categories,
              latest_prior_incident_date
            """,
            {"source": source, "incident_id": incident_id, "property_context_id": property_context_id},
        )
        if prior_counts:
            prior_summary = {
                "prior_same_property_count": int(prior_counts[0].get("prior_count") or 0),
                "prior_same_property_categories": sorted(
                    [x for x in (prior_counts[0].get("prior_categories") or []) if x]
                ),
                "latest_prior_incident_date": prior_counts[0].get("latest_prior_incident_date"),
            }

        prior_same_property_rows = await execute_cypher(
            """
            MATCH (older:Incident:TriageFixManaged {source: $source})-[r:PRIOR_SIMILAR {reason: 'same_property'}]->(i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
            OPTIONAL MATCH (older)-[:HAS_CATEGORY]->(c:Category)
            OPTIONAL MATCH (older)-[:HAS_STATUS]->(s:Status)
            RETURN
              older.incident_id AS incident_id,
              older.created_date AS created_date,
              c.name AS category,
              s.name AS status,
              older.severity_average AS severity_average,
              r.reason AS reason
            ORDER BY older.created_date DESC
            LIMIT 10
            """,
            {"source": source, "incident_id": incident_id},
        )

    semantic_similar_rows = await execute_cypher(
        """
        MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})-[r:SIMILAR_TO {reason: 'embedding_description_similarity'}]->(j:Incident:TriageFixManaged {source: $source})
        OPTIONAL MATCH (j)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (j)-[:HAS_CATEGORY]->(c:Category)
        WHERE toLower(coalesce(s.name, '')) IN ['resolved', 'resolved - partner']
        RETURN
          j.incident_id AS incident_id,
          j.created_date AS created_date,
          c.name AS category,
          s.name AS status,
          j.severity_average AS severity_average,
          r.similarity_score AS similarity_score
        ORDER BY r.similarity_score DESC, j.created_date DESC
        LIMIT 10
        """,
        {"source": source, "incident_id": incident_id},
    )

    evidence_items = []
    if int(incident.get("incidence_docs_count") or 0) > 0:
        evidence_items.append({"type": "Incidence docs", "count": int(incident.get("incidence_docs_count") or 0)})
    if int(incident.get("lease_contract_count") or 0) > 0:
        evidence_items.append({"type": "Lease Contract", "count": int(incident.get("lease_contract_count") or 0)})
    if int(incident.get("finance_invoice_count") or 0) > 0:
        evidence_items.append({"type": "Finance Invoice", "count": int(incident.get("finance_invoice_count") or 0)})
    if int(incident.get("furniture_budget_count") or 0) > 0:
        evidence_items.append({"type": "Furniture budget doc", "count": int(incident.get("furniture_budget_count") or 0)})

    if missing_questions:
        next_action = "Collect missing questions and update routing decision."
    elif incident.get("provider"):
        next_action = f"Confirm assignment with provider {incident.get('provider')}."
    else:
        next_action = "Assign recommended trade specialist and confirm provider availability."

    recommendation_summary = {
        "recommended_action": incident.get("recommended_action"),
        "recommended_trade": incident.get("recommended_trade"),
        "provider": incident.get("provider"),
        "provider_confidence": incident.get("provider_confidence"),
        "severity_average": incident.get("severity_average"),
        "next_action": next_action,
    }

    property_info = {
        "property_context_id": property_context_id,
        "area_cluster": incident.get("area_cluster"),
        **prior_summary,
    }

    evidence = {
        "has_incidence_docs": bool(incident.get("has_incidence_docs")),
        "incidence_docs_count": int(incident.get("incidence_docs_count") or 0),
        "lease_contract_count": int(incident.get("lease_contract_count") or 0),
        "finance_invoice_count": int(incident.get("finance_invoice_count") or 0),
        "items": evidence_items,
    }

    decision_trace = [
        {
            "step_number": 1,
            "title": "Classify incident",
            "observation": f"Category={incident.get('category') or 'unknown'}, subcategory={incident.get('subcategory') or 'unknown'}, urgency={incident.get('urgency') or 'unknown'}, status={incident.get('status') or 'unknown'}.",
            "source": "category/severity graph",
        },
        {
            "step_number": 2,
            "title": "Check property history",
            "observation": f"PropertyContext={property_context_id or 'unknown'} has {prior_summary['prior_same_property_count']} prior incidents; latest prior date={prior_summary['latest_prior_incident_date'] or 'unknown'}.",
            "source": "PRIOR_SIMILAR",
        },
        {
            "step_number": 3,
            "title": "Find semantically similar resolved cases",
            "observation": f"Found {len(semantic_similar_rows)} semantic similar resolved cases using clean_description.",
            "source": "SIMILAR_TO embeddings",
        },
        {
            "step_number": 4,
            "title": "Recommend next action",
            "observation": f"Action={incident.get('recommended_action') or 'unknown'}, trade={incident.get('recommended_trade') or 'unknown'}, provider={incident.get('provider') or 'not assigned'}, confidence={incident.get('provider_confidence') or 'unknown'}.",
            "source": "recommended_action/provider routing",
        },
    ]

    return {
        "incident_id": incident_id,
        "recommendation_summary": recommendation_summary,
        "property_info": property_info,
        "evidence": evidence,
        "severity_signals": severity_signals,
        "similar_cases": {
            "prior_same_property": prior_same_property_rows,
            "semantic_similar_resolved": semantic_similar_rows,
        },
        "decision_trace": decision_trace,
    }
