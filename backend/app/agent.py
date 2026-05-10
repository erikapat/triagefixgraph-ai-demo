"""TriageFixGraph AI agent — OpenAI Agents SDK implementation."""

from __future__ import annotations

import json

from agents import Agent, Runner, function_tool

from app.context_graph_client import execute_cypher
from app.memory import store_message, get_context, resolve_session_id


SYSTEM_PROMPT = """You are an AI assistant for housing maintenance incident triage.
You have access to the TriageFix graph in Neo4j and must base your answers on
actual graph data from TriageFixManaged nodes.

Your capabilities include:
- Prioritizing incidents by severity and urgency
- Explaining incident context (property, area, category, status, provider)
- Finding prior similar incidents and historical routing context
- Summarizing provider workload and coverage
- Identifying missing questions and evidence gaps

IMPORTANT: You MUST use available tools before answering data questions.
Do not guess; use graph results and cite concrete fields from returned rows.

CRITICAL: Call tools directly without prefacing text. Generate final narrative
only after tool results are available."""


@function_tool
async def get_top_severity_incidents(limit: str = "10") -> str:
    """Get top incidents ranked by severity_average."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source})
    OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
    OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
    OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
    RETURN i.incident_id AS incident_id,
           i.created_date AS created_date,
           i.severity_average AS severity_average,
           c.name AS category,
           u.name AS urgency,
           t.name AS recommended_trade,
           i.provider_confidence AS provider_confidence,
           left(i.clean_description, 220) AS description_preview
    ORDER BY i.severity_average DESC, i.created_date DESC
    LIMIT toInteger($limit)
    """
    result = await execute_cypher(cypher, {"limit": limit, "source": "airtable_enriched_sample"}, tool_name="get_top_severity_incidents")
    return json.dumps(result, default=str)


@function_tool
async def get_incident_context(incident_id: str) -> str:
    """Get full triage context for a single incident."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
    OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
    OPTIONAL MATCH (p)-[:LOCATED_IN_AREA]->(a:AreaCluster)
    OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
    OPTIONAL MATCH (i)-[:HAS_SUBCATEGORY]->(sc:Subcategory)
    OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
    OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
    OPTIONAL MATCH (i)-[:REQUIRES_TRADE]->(t:TradeSpecialist)
    OPTIONAL MATCH (i)-[:HANDLED_BY]->(r:Renovator)
    OPTIONAL MATCH (i)-[:RECOMMENDED_ACTION]->(ra:RecommendedAction)
    OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
    OPTIONAL MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
    OPTIONAL MATCH (i)-[:HAS_SEVERITY_SIGNAL]->(ss:SeveritySignal)
    RETURN i.incident_id AS incident_id,
           i.created_date AS created_date,
           i.resolved_date AS resolved_date,
           i.severity_average AS severity_average,
           i.provider_confidence AS provider_confidence,
           i.clean_description AS clean_description,
           p.property_context_id AS property_context_id,
           a.name AS area_cluster,
           c.name AS category,
           sc.name AS subcategory,
           u.name AS urgency,
           s.name AS status,
           t.name AS recommended_trade,
           r.name AS provider_candidate,
           ra.name AS recommended_action,
           e.evidence_id AS evidence_id,
           e.has_incidence_docs AS has_incidence_docs,
           e.incidence_docs_count AS incidence_docs_count,
           e.lease_contract_count AS lease_contract_count,
           e.furniture_budget_count AS furniture_budget_count,
           e.finance_invoice_count AS finance_invoice_count,
           collect(DISTINCT mq.text) AS missing_questions,
           collect(DISTINCT {dimension: ss.dimension, score: ss.score}) AS severity_signals
    """
    result = await execute_cypher(
        cypher,
        {"incident_id": incident_id, "source": "airtable_enriched_sample"},
        tool_name="get_incident_context",
    )
    return json.dumps(result, default=str)


@function_tool
async def get_prior_similar_incidents(incident_id: str, limit: str = "20") -> str:
    """Get prior similar incidents connected through PRIOR_SIMILAR."""
    cypher = """
    MATCH (prior:Incident:TriageFixManaged {source: $source})-[r:PRIOR_SIMILAR]->(current:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
    OPTIONAL MATCH (prior)-[:HAS_CATEGORY]->(c:Category)
    OPTIONAL MATCH (prior)-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext)
    OPTIONAL MATCH (prior)-[:HANDLED_BY]->(ren:Renovator)
    RETURN current.incident_id AS incident_id,
           prior.incident_id AS prior_incident_id,
           r.reason AS reason,
           r.older_created_date AS prior_created_date,
           r.newer_created_date AS current_created_date,
           c.name AS category,
           p.property_context_id AS property_context_id,
           ren.name AS prior_renovator,
           prior.severity_average AS prior_severity_average,
           left(prior.clean_description, 200) AS prior_description_preview
    ORDER BY prior_created_date DESC
    LIMIT toInteger($limit)
    """
    result = await execute_cypher(
        cypher,
        {"incident_id": incident_id, "limit": limit, "source": "airtable_enriched_sample"},
        tool_name="get_prior_similar_incidents",
    )
    return json.dumps(result, default=str)


@function_tool
async def get_renovator_workload(limit: str = "20") -> str:
    """Get renovator workload distribution by category."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source})-[:HANDLED_BY]->(r:Renovator)
    OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
    RETURN r.name AS renovator,
           c.name AS category,
           count(i) AS incident_count,
           round(avg(i.severity_average), 2) AS avg_severity
    ORDER BY incident_count DESC, avg_severity DESC, renovator ASC
    LIMIT toInteger($limit)
    """
    result = await execute_cypher(cypher, {"limit": limit, "source": "airtable_enriched_sample"}, tool_name="get_renovator_workload")
    return json.dumps(result, default=str)


@function_tool
async def get_missing_questions_by_category(limit: str = "50") -> str:
    """Get missing questions grouped by incident category."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source})-[:HAS_CATEGORY]->(c:Category)
    MATCH (i)-[:NEEDS_QUESTION]->(mq:MissingQuestion)
    RETURN c.name AS category,
           mq.text AS missing_question,
           count(*) AS frequency
    ORDER BY category ASC, frequency DESC, missing_question ASC
    LIMIT toInteger($limit)
    """
    result = await execute_cypher(cypher, {"limit": limit, "source": "airtable_enriched_sample"}, tool_name="get_missing_questions_by_category")
    return json.dumps(result, default=str)


@function_tool
async def get_evidence_coverage() -> str:
    """Get evidence coverage metrics across incidents."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source})
    OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
    RETURN count(i) AS total_incidents,
           count(DISTINCT CASE WHEN e IS NOT NULL THEN i END) AS incidents_with_evidence,
           round(100.0 * count(DISTINCT CASE WHEN e IS NOT NULL THEN i END) / count(i), 2) AS evidence_coverage_pct,
           count(DISTINCT CASE WHEN e.has_incidence_docs THEN i END) AS incidents_with_incidence_docs,
           sum(coalesce(e.incidence_docs_count, 0)) AS total_incidence_docs,
           sum(coalesce(e.lease_contract_count, 0)) AS total_lease_contract_docs,
           sum(coalesce(e.furniture_budget_count, 0)) AS total_furniture_budget_docs,
           sum(coalesce(e.finance_invoice_count, 0)) AS total_finance_invoice_docs
    """
    result = await execute_cypher(cypher, {"source": "airtable_enriched_sample"}, tool_name="get_evidence_coverage")
    return json.dumps(result, default=str)


@function_tool
async def get_graph_schema() -> str:
    """Get current TriageFix graph schema from managed nodes."""
    labels_q = """
    MATCH (n:TriageFixManaged {source: $source})
    UNWIND labels(n) AS l
    WITH DISTINCT l
    WHERE l <> 'TriageFixManaged'
    RETURN l AS label
    ORDER BY label
    """
    rels_q = """
    MATCH (a:TriageFixManaged {source: $source})-[r]->(b:TriageFixManaged {source: $source})
    RETURN DISTINCT type(r) AS relationship_type
    ORDER BY relationship_type
    """
    labels = await execute_cypher(labels_q, {"source": "airtable_enriched_sample"}, tool_name="get_graph_schema")
    rels = await execute_cypher(rels_q, {"source": "airtable_enriched_sample"}, tool_name="get_graph_schema")
    return json.dumps({"labels": labels, "relationship_types": rels}, default=str)


agent = Agent(
    name="TriageFixGraph Assistant",
    instructions=SYSTEM_PROMPT,
    tools=[
        get_top_severity_incidents,
        get_incident_context,
        get_prior_similar_incidents,
        get_renovator_workload,
        get_missing_questions_by_category,
        get_evidence_coverage,
        get_graph_schema,
    ],
)


async def handle_message(message: str, session_id: str | None = None) -> dict:
    """Handle an incoming chat message."""
    session_id = resolve_session_id(session_id)

    await store_message(session_id, "user", message)
    context = await get_context(session_id, query=message)
    history = context.get("messages", [])

    if history:
        history_block = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in history
        )
        input_message = (
            f"<conversation_history>\n{history_block}\n</conversation_history>\n\n"
            f"[USER]\n{message}"
        )
    else:
        input_message = message

    result = await Runner.run(agent, input_message)

    response_text = result.final_output
    assistant_result = await store_message(session_id, "assistant", response_text)

    return {
        "response": response_text,
        "session_id": session_id,
        "graph_data": None,
        "entities_extracted": (assistant_result or {}).get("entities", []),
        "preferences_detected": (assistant_result or {}).get("preferences", []),
    }


async def handle_message_stream(message: str, session_id: str | None = None) -> dict:
    """Handle a chat message with streaming text deltas via the collector event queue."""
    from app.context_graph_client import get_collector

    session_id = resolve_session_id(session_id)

    collector = get_collector()
    await store_message(session_id, "user", message)
    context = await get_context(session_id, query=message)
    history = context.get("messages", [])

    if history:
        history_block = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in history
        )
        input_message = (
            f"<conversation_history>\n{history_block}\n</conversation_history>\n\n"
            f"[USER]\n{message}"
        )
    else:
        input_message = message

    result = Runner.run_streamed(agent, input_message)
    response_text = ""
    async for event in result.stream_events():
        if event.type == "raw_response_event":
            data = event.data
            event_type = getattr(data, "type", "")
            if event_type == "response.output_text.delta":
                delta = getattr(data, "delta", "")
                if delta:
                    collector.emit_text_delta(delta)
                    response_text += delta

    if not response_text:
        response_text = result.final_output or ""

    assistant_result = await store_message(session_id, "assistant", response_text)
    if assistant_result:
        collector.emit_entities_extracted(assistant_result.get("entities", []))
        collector.emit_preferences_detected(assistant_result.get("preferences", []))
    collector.emit_done(response_text, session_id)

    return {
        "response": response_text,
        "session_id": session_id,
        "graph_data": None,
    }
