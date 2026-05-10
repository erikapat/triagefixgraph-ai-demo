"""Agent Memory AI Agent — OpenAI Agents SDK implementation."""

from __future__ import annotations

import json

from agents import Agent, Runner, function_tool

from app.config import settings
from app.context_graph_client import execute_cypher, get_schema
from app.memory import store_message, get_context, resolve_session_id


SYSTEM_PROMPT = """You are an AI assistant for agent memory and conversation intelligence. You have
access to a knowledge graph tracking AI agents, their conversations, extracted
entities, memories, tool usage, and session histories.

Your capabilities include:
- Exploring agent memory stores and knowledge states
- Analyzing conversation patterns and entity extraction quality
- Reviewing tool usage statistics and performance
- Tracing reasoning chains across sessions
- Identifying knowledge gaps and memory consolidation opportunities

Always provide specific data from the knowledge graph. When analyzing agent behavior,
reference concrete conversations, memory entries, and tool invocations.


IMPORTANT: You MUST use the available tools to query the knowledge graph before answering any question about the data. Never guess or make up information — always use tools to look up actual data from the graph.

CRITICAL: Call tools DIRECTLY without any introductory text. Do NOT say "I'll search for..." or "Let me look up..." before calling a tool — just call the tool immediately. Only generate text AFTER you have received the tool results and are ready to provide your final answer."""

# ---------------------------------------------------------------------------
# Agent tools — domain-specific for Agent Memory
# ---------------------------------------------------------------------------

@function_tool
async def search_agent(query: str) -> str:
    """Search for agents by name, model, or status"""
    cypher = """MATCH (a:Agent)
    WHERE toLower(a.name) CONTAINS toLower($query)
       OR toLower(coalesce(a.model, '')) CONTAINS toLower($query)
    OPTIONAL MATCH (a)-[r]-(related)
    RETURN a, type(r) AS rel_type, related
    LIMIT 20
"""
    params = {
        "query": query,
    }
    result = await execute_cypher(cypher, params, tool_name="search_agent")
    return json.dumps(result, default=str)

@function_tool
async def conversation_history(agent_name: str) -> str:
    """Get conversation history for a specific agent"""
    cypher = """MATCH (a:Agent {name: $agent_name})-[:PARTICIPATED_IN]->(c:Conversation)
    OPTIONAL MATCH (c)-[:EXTRACTED]->(e:Entity)
    OPTIONAL MATCH (c)<-[:TRIGGERED_BY]-(tc:ToolCall)
    RETURN c, collect(DISTINCT e) AS entities, collect(DISTINCT tc) AS tool_calls
    ORDER BY c.started_at DESC
    LIMIT 20
"""
    params = {
        "agent_name": agent_name,
    }
    result = await execute_cypher(cypher, params, tool_name="conversation_history")
    return json.dumps(result, default=str)

@function_tool
async def memory_recall(agent_name: str, query: str) -> str:
    """Search agent memories by content or referenced entities"""
    cypher = """MATCH (a:Agent {name: $agent_name})-[:REMEMBERED]->(m:Memory)
    WHERE toLower(m.content) CONTAINS toLower($query)
    OPTIONAL MATCH (m)-[:REFERENCED]->(e:Entity)
    RETURN m, collect(e) AS referenced_entities
    ORDER BY m.importance DESC, m.last_accessed DESC
    LIMIT 20
"""
    params = {
        "agent_name": agent_name,
        "query": query,
    }
    result = await execute_cypher(cypher, params, tool_name="memory_recall")
    return json.dumps(result, default=str)

@function_tool
async def tool_usage_stats(agent_name: str) -> str:
    """Analyze tool usage patterns for an agent"""
    cypher = """MATCH (a:Agent {name: $agent_name})-[:INVOKED]->(tc:ToolCall)
    WITH tc.tool_name AS tool, count(tc) AS call_count,
         avg(tc.duration_ms) AS avg_duration,
         sum(CASE WHEN tc.status = 'success' THEN 1 ELSE 0 END) AS successes
    RETURN tool, call_count, avg_duration, successes,
           toFloat(successes) / call_count AS success_rate
    ORDER BY call_count DESC
"""
    params = {
        "agent_name": agent_name,
    }
    result = await execute_cypher(cypher, params, tool_name="tool_usage_stats")
    return json.dumps(result, default=str)

@function_tool
async def entity_graph(query: str) -> str:
    """Explore the entity relationship graph extracted from conversations"""
    cypher = """MATCH (e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($query)
    OPTIONAL MATCH (e)<-[:EXTRACTED]-(c:Conversation)
    OPTIONAL MATCH (e)<-[:REFERENCED]-(m:Memory)
    OPTIONAL MATCH (e)<-[:REASONED_ABOUT]-(a:Agent)
    RETURN e, collect(DISTINCT c) AS conversations,
           collect(DISTINCT m) AS memories, collect(DISTINCT a) AS agents
    LIMIT 20
"""
    params = {
        "query": query,
    }
    result = await execute_cypher(cypher, params, tool_name="entity_graph")
    return json.dumps(result, default=str)

@function_tool
async def list_agents(limit: str) -> str:
    """List agent records with optional limit"""
    cypher = """MATCH (n:Agent)
    RETURN n
    ORDER BY n.name
    LIMIT toInteger($limit)
"""
    params = {
        "limit": limit,
    }
    result = await execute_cypher(cypher, params, tool_name="list_agents")
    return json.dumps(result, default=str)

@function_tool
async def get_agent_by_id(id: str) -> str:
    """Get a specific agent by ID with all connections"""
    cypher = """MATCH (n:Agent {agent_id: $id})
    OPTIONAL MATCH (n)-[r]-(related)
    RETURN n, type(r) AS relationship, labels(related) AS related_labels, related.name AS related_name
    LIMIT 50
"""
    params = {
        "id": id,
    }
    result = await execute_cypher(cypher, params, tool_name="get_agent_by_id")
    return json.dumps(result, default=str)



@function_tool
async def run_cypher(query: str, parameters: str = "{}") -> str:
    """Execute a read-only Cypher query against the knowledge graph."""
    try:
        params = json.loads(parameters) if parameters else {}
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON parameters"})
    params.setdefault("domain", settings.domain_id)
    try:
        result = await execute_cypher(query, params, tool_name="run_cypher")
        return json.dumps(result, default=str)
    except Exception as e:
        return json.dumps({"error": f"Cypher query failed: {e}"})


@function_tool
async def get_graph_schema() -> str:
    """Get the knowledge graph schema (node labels and relationship types)."""
    result = await get_schema()
    return json.dumps(result, default=str)

agent = Agent(
    name="Agent Memory Assistant",
    instructions=SYSTEM_PROMPT,
    tools=[
        search_agent,
        conversation_history,
        memory_recall,
        tool_usage_stats,
        entity_graph,
        list_agents,
        get_agent_by_id,
        run_cypher,
        get_graph_schema,
    ],
)


# ---------------------------------------------------------------------------
# Message handler
# ---------------------------------------------------------------------------


async def handle_message(message: str, session_id: str | None = None) -> dict:
    """Handle an incoming chat message."""
    session_id = resolve_session_id(session_id)

    # Retrieve conversation history and store the new user message
    await store_message(session_id, "user", message)
    context = await get_context(session_id, query=message)
    history = context.get("messages", [])

    # Build input with structured conversation history
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

    # Build input with structured conversation history
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
            # Only emit text content deltas — skip tool call argument deltas
            # which would otherwise leak raw JSON into the response stream.
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
