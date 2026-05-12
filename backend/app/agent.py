"""TriageFixGraph AI agent — OpenAI Agents SDK implementation."""

from __future__ import annotations

import json
import re

from agents import Agent, Runner, function_tool

from app.config import settings
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
only after tool results are available.

SCOPE RULES:
- If the user asks about a specific incident (explicit incident_id or "this incident"), use incident-scoped tools.
- If the user asks a global analysis question (e.g., trends, communities, pending vs resolved patterns),
  query across the full TriageFixManaged graph and do NOT restrict to the currently visualized incident subgraph.

GRAPH RENDERING RULE:
- If user asks to draw/visualize/show a graph, prioritize draw_* tools that return nodes/relationships for visualization.
- Do not answer draw requests with only tabular rows when a draw_* tool is available."""

GRAPH_SOURCE = settings.triagefix_graph_source

PENDING_STATUSES = [
    "follow up",
    "pending - partner",
    "action required",
    "pending to evaluate",
    "unknown",
    "pending",
    "follow-up",
    "to evaluate",
]
RESOLVED_STATUSES = [
    "resolved",
    "resolved - partner",
    "resolution",
]


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
    result = await execute_cypher(cypher, {"limit": limit, "source": GRAPH_SOURCE}, tool_name="get_top_severity_incidents")
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
        {"incident_id": incident_id, "source": GRAPH_SOURCE},
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
        {"incident_id": incident_id, "limit": limit, "source": GRAPH_SOURCE},
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
    result = await execute_cypher(cypher, {"limit": limit, "source": GRAPH_SOURCE}, tool_name="get_renovator_workload")
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
    result = await execute_cypher(cypher, {"limit": limit, "source": GRAPH_SOURCE}, tool_name="get_missing_questions_by_category")
    return json.dumps(result, default=str)


@function_tool
async def get_evidence_coverage() -> str:
    """Get evidence coverage metrics across incidents."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source})
    OPTIONAL MATCH (i)-[:HAS_EVIDENCE]->(e:Evidence)
    WITH i, e
    WITH
      count(DISTINCT i) AS total_incidents,
      collect(DISTINCT CASE WHEN e IS NOT NULL THEN i END) AS evidence_incidents,
      collect(DISTINCT CASE WHEN coalesce(e.has_incidence_docs, false) THEN i END) AS incidence_docs_incidents,
      sum(coalesce(e.incidence_docs_count, 0)) AS total_incidence_docs,
      sum(coalesce(e.lease_contract_count, 0)) AS total_lease_contract_docs,
      sum(coalesce(e.furniture_budget_count, 0)) AS total_furniture_budget_docs,
      sum(coalesce(e.finance_invoice_count, 0)) AS total_finance_invoice_docs
    WITH
      total_incidents,
      size([x IN evidence_incidents WHERE x IS NOT NULL]) AS incidents_with_evidence,
      size([x IN incidence_docs_incidents WHERE x IS NOT NULL]) AS incidents_with_incidence_docs,
      total_incidence_docs,
      total_lease_contract_docs,
      total_furniture_budget_docs,
      total_finance_invoice_docs
    RETURN
      total_incidents,
      incidents_with_evidence,
      CASE
        WHEN total_incidents = 0 THEN 0.0
        ELSE round(100.0 * incidents_with_evidence / toFloat(total_incidents), 2)
      END AS evidence_coverage_pct,
      incidents_with_incidence_docs,
      total_incidence_docs,
      total_lease_contract_docs,
      total_furniture_budget_docs,
      total_finance_invoice_docs
    """
    result = await execute_cypher(cypher, {"source": GRAPH_SOURCE}, tool_name="get_evidence_coverage")
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
    labels = await execute_cypher(labels_q, {"source": GRAPH_SOURCE}, tool_name="get_graph_schema")
    rels = await execute_cypher(rels_q, {"source": GRAPH_SOURCE}, tool_name="get_graph_schema")
    return json.dumps({"labels": labels, "relationship_types": rels}, default=str)


@function_tool
async def draw_incident_local_graph(incident_id: str) -> str:
    """Return local subgraph (nodes/relationships) around one incident for visualization."""
    cypher = """
    MATCH (i:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})
    OPTIONAL MATCH (i)-[r1]-(n1:TriageFixManaged {source: $source})
    OPTIONAL MATCH (i)-[:HAS_PROPERTY_CONTEXT]->(:PropertyContext:TriageFixManaged {source: $source})-[r2:LOCATED_IN_AREA]->(a:AreaCluster:TriageFixManaged {source: $source})
    WITH
      [x IN (collect(DISTINCT i) + collect(DISTINCT n1) + collect(DISTINCT a)) WHERE x IS NOT NULL] AS nodes,
      [x IN (collect(DISTINCT r1) + collect(DISTINCT r2)) WHERE x IS NOT NULL] AS relationships
    RETURN nodes, relationships
    """
    result = await execute_cypher(
        cypher,
        {"incident_id": incident_id, "source": GRAPH_SOURCE},
        tool_name="draw_incident_local_graph",
    )
    return json.dumps(result, default=str)


@function_tool
async def draw_pending_to_resolved_similar_graph(pair_limit: str = "60") -> str:
    """Return global graph of pending incidents connected to resolved similar incidents."""
    cypher = """
    MATCH (src:Incident:TriageFixManaged {source: $source})-[:HAS_STATUS]->(src_status:Status)
    MATCH (src)-[sim:SIMILAR_TO]-(tgt:Incident:TriageFixManaged {source: $source})
    MATCH (tgt)-[:HAS_STATUS]->(tgt_status:Status)
    WHERE toLower(coalesce(src_status.name, '')) IN $pending_statuses
      AND toLower(coalesce(tgt_status.name, '')) IN $resolved_statuses
      AND src.incident_id <> tgt.incident_id
    WITH src, sim, tgt
    ORDER BY src.severity_average DESC, coalesce(sim.similarity_score, 0.0) DESC
    LIMIT toInteger($pair_limit)

    OPTIONAL MATCH (src)-[:HAS_CATEGORY]->(src_cat:Category)
    OPTIONAL MATCH (src)-[:HAS_URGENCY]->(src_urg:Urgency)
    OPTIONAL MATCH (src)-[:HAS_PROPERTY_CONTEXT]->(src_prop:PropertyContext)
    OPTIONAL MATCH (tgt)-[:HAS_CATEGORY]->(tgt_cat:Category)
    OPTIONAL MATCH (tgt)-[:HANDLED_BY]->(tgt_ren:Renovator)

    WITH
      [x IN (
        collect(DISTINCT src) + collect(DISTINCT tgt) +
        collect(DISTINCT src_cat) + collect(DISTINCT src_urg) + collect(DISTINCT src_prop) +
        collect(DISTINCT tgt_cat) + collect(DISTINCT tgt_ren)
      ) WHERE x IS NOT NULL] AS nodes,
      collect(DISTINCT sim) AS sim_relationships

    UNWIND nodes AS n1
    UNWIND nodes AS n2
    OPTIONAL MATCH (n1)-[r]->(n2)
    WITH nodes, sim_relationships, collect(DISTINCT r) AS context_relationships

    WITH nodes, [x IN (sim_relationships + context_relationships) WHERE x IS NOT NULL] AS relationships
    RETURN nodes, relationships
    """
    result = await execute_cypher(
        cypher,
        {
            "source": GRAPH_SOURCE,
            "pair_limit": pair_limit,
            "pending_statuses": PENDING_STATUSES,
            "resolved_statuses": RESOLVED_STATUSES,
        },
        tool_name="draw_pending_to_resolved_similar_graph",
    )
    return json.dumps(result, default=str)


@function_tool
async def draw_property_timeline_graph(incident_id: str = "", property_context_id: str = "") -> str:
    """Return property timeline graph for one property context (incidents + PRIOR_SIMILAR chain)."""
    if property_context_id.strip():
        cypher = """
        MATCH (p:PropertyContext:TriageFixManaged {source: $source, property_context_id: $property_context_id})
        MATCH (p)<-[:HAS_PROPERTY_CONTEXT]-(i:Incident:TriageFixManaged {source: $source})
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[ps:PRIOR_SIMILAR]->(j:Incident:TriageFixManaged {source: $source})-[:HAS_PROPERTY_CONTEXT]->(p)
        WITH
          [x IN (collect(DISTINCT p) + collect(DISTINCT i) + collect(DISTINCT j) + collect(DISTINCT c) + collect(DISTINCT s) + collect(DISTINCT u)) WHERE x IS NOT NULL] AS nodes,
          collect(DISTINCT ps) AS relationships
        RETURN nodes, [x IN relationships WHERE x IS NOT NULL] AS relationships
        """
        params = {"source": GRAPH_SOURCE, "property_context_id": property_context_id}
    else:
        cypher = """
        MATCH (base:Incident:TriageFixManaged {source: $source, incident_id: $incident_id})-[:HAS_PROPERTY_CONTEXT]->(p:PropertyContext:TriageFixManaged {source: $source})
        MATCH (p)<-[:HAS_PROPERTY_CONTEXT]-(i:Incident:TriageFixManaged {source: $source})
        OPTIONAL MATCH (i)-[:HAS_CATEGORY]->(c:Category)
        OPTIONAL MATCH (i)-[:HAS_STATUS]->(s:Status)
        OPTIONAL MATCH (i)-[:HAS_URGENCY]->(u:Urgency)
        OPTIONAL MATCH (i)-[ps:PRIOR_SIMILAR]->(j:Incident:TriageFixManaged {source: $source})-[:HAS_PROPERTY_CONTEXT]->(p)
        WITH
          [x IN (collect(DISTINCT p) + collect(DISTINCT base) + collect(DISTINCT i) + collect(DISTINCT j) + collect(DISTINCT c) + collect(DISTINCT s) + collect(DISTINCT u)) WHERE x IS NOT NULL] AS nodes,
          collect(DISTINCT ps) AS relationships
        RETURN nodes, [x IN relationships WHERE x IS NOT NULL] AS relationships
        """
        params = {"source": GRAPH_SOURCE, "incident_id": incident_id}

    result = await execute_cypher(cypher, params, tool_name="draw_property_timeline_graph")
    return json.dumps(result, default=str)


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
        draw_incident_local_graph,
        draw_pending_to_resolved_similar_graph,
        draw_property_timeline_graph,
    ],
)

INCIDENT_ID_RE = re.compile(r"\brec[a-zA-Z0-9]{8,}\b")
THIS_INCIDENT_RE = re.compile(r"\b(this incident|este incidente)\b", re.IGNORECASE)
GLOBAL_SCOPE_HINT_RE = re.compile(
    r"\b(show|visualize|anal(y|i)ze|community|communities|global|across|all incidents|pendientes|resolved|resuelt[oa]s)\b",
    re.IGNORECASE,
)
DRAW_INTENT_RE = re.compile(r"\b(draw|visualize|graph|grafo|dibuja|visualiza|mostrar grafo|show graph)\b", re.IGNORECASE)


def _scope_prefix_for_message(message: str) -> str:
    has_incident_id = INCIDENT_ID_RE.search(message) is not None
    refers_this_incident = THIS_INCIDENT_RE.search(message) is not None
    if has_incident_id or refers_this_incident:
        return "SCOPE: incident_scope. Use incident-focused tools and that specific incident."
    if GLOBAL_SCOPE_HINT_RE.search(message):
        return (
            "SCOPE: global_scope. Query across all Incident:TriageFixManaged nodes "
            f"for source={GRAPH_SOURCE}; do not limit to one incident unless asked."
        )
    return "SCOPE: global_scope unless the user explicitly asks for one incident."


def _intent_prefix_for_message(message: str) -> str:
    if DRAW_INTENT_RE.search(message):
        return (
            "INTENT: draw_graph. Prefer draw_* tools that return nodes/relationships. "
            "Do not rely only on tabular tools."
        )
    return "INTENT: analysis_text."


async def handle_message(message: str, session_id: str | None = None) -> dict:
    """Handle an incoming chat message."""
    session_id = resolve_session_id(session_id)

    if THIS_INCIDENT_RE.search(message) and not INCIDENT_ID_RE.search(message):
        clarification = (
            "I need an explicit incident id to explain a specific incident. "
            "Please select an incident in the center panel or include an id like "
            "`recRH51V2eHHlTh9W` in your message."
        )
        await store_message(session_id, "user", message)
        await store_message(session_id, "assistant", clarification)
        return {
            "response": clarification,
            "session_id": session_id,
            "graph_data": None,
            "entities_extracted": [],
            "preferences_detected": [],
        }

    scoped_message = message
    match = INCIDENT_ID_RE.search(message)
    if match:
        scoped_message = (
            f"Use incident_id={match.group(0)} as the primary incident for context tools.\n\n"
            f"{message}"
        )
    scoped_message = (
        f"{_scope_prefix_for_message(message)}\n"
        f"{_intent_prefix_for_message(message)}\n\n"
        f"{scoped_message}"
    )

    await store_message(session_id, "user", message)
    context = await get_context(session_id, query=message)
    history = context.get("messages", [])

    if history:
        history_block = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in history
        )
        input_message = (
            f"<conversation_history>\n{history_block}\n</conversation_history>\n\n"
            f"[USER]\n{scoped_message}"
        )
    else:
        input_message = scoped_message

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

    if THIS_INCIDENT_RE.search(message) and not INCIDENT_ID_RE.search(message):
        clarification = (
            "I need an explicit incident id to explain a specific incident. "
            "Please select an incident in the center panel or include an id like "
            "`recRH51V2eHHlTh9W` in your message."
        )
        collector.emit_text_delta(clarification)
        collector.emit_done(clarification, session_id)
        return {"response": clarification, "session_id": session_id, "graph_data": None}

    scoped_message = message
    match = INCIDENT_ID_RE.search(message)
    if match:
        scoped_message = (
            f"Use incident_id={match.group(0)} as the primary incident for context tools.\n\n"
            f"{message}"
        )
    scoped_message = (
        f"{_scope_prefix_for_message(message)}\n"
        f"{_intent_prefix_for_message(message)}\n\n"
        f"{scoped_message}"
    )

    await store_message(session_id, "user", message)
    context = await get_context(session_id, query=message)
    history = context.get("messages", [])

    if history:
        history_block = "\n\n".join(
            f"[{m['role'].upper()}]\n{m['content']}" for m in history
        )
        input_message = (
            f"<conversation_history>\n{history_block}\n</conversation_history>\n\n"
            f"[USER]\n{scoped_message}"
        )
    else:
        input_message = scoped_message

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
