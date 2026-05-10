"""Domain models for Agent Memory — auto-generated from ontology."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

class Person(BaseModel):
    """Entity model for Person."""

    name: str = ...
    email: str | None = None
    role: str | None = None
    description: str | None = None

class Organization(BaseModel):
    """Entity model for Organization."""

    name: str = ...
    description: str | None = None
    industry: str | None = None

class Location(BaseModel):
    """Entity model for Location."""

    name: str = ...
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None

class Event(BaseModel):
    """Entity model for Event."""

    name: str = ...
    date: datetime | None = None
    description: str | None = None

class Object(BaseModel):
    """Entity model for Object."""

    name: str = ...
    description: str | None = None

class AgentStatusEnum(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    SUSPENDED = "suspended"
    RETIRED = "retired"

class Agent(BaseModel):
    """Entity model for Agent."""

    agent_id: str = ...
    name: str = ...
    model: str | None = None
    version: str | None = None
    capabilities: str | None = None
    status: AgentStatusEnum | None = None

class ConversationStatusEnum(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"

class Conversation(BaseModel):
    """Entity model for Conversation."""

    conversation_id: str = ...
    topic: str | None = None
    started_at: datetime = ...
    ended_at: datetime | None = None
    message_count: int | None = None
    status: ConversationStatusEnum | None = None

class EntityEntityTypeEnum(str, Enum):
    PERSON = "person"
    PLACE = "place"
    CONCEPT = "concept"
    EVENT = "event"
    PRODUCT = "product"
    ORGANIZATION = "organization"

class Entity(BaseModel):
    """Entity model for Entity."""

    entity_id: str = ...
    name: str = ...
    entity_type: EntityEntityTypeEnum | None = None
    confidence: float | None = None
    source_text: str | None = None

class MemoryMemoryTypeEnum(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"

class Memory(BaseModel):
    """Entity model for Memory."""

    memory_id: str = ...
    content: str = ...
    memory_type: MemoryMemoryTypeEnum | None = None
    importance: float | None = None
    created_at: datetime = ...
    last_accessed: datetime | None = None

class ToolCallStatusEnum(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

class ToolCall(BaseModel):
    """Entity model for ToolCall."""

    call_id: str = ...
    tool_name: str = ...
    input_params: str | None = None
    output_summary: str | None = None
    duration_ms: int | None = None
    status: ToolCallStatusEnum | None = None
    called_at: datetime = ...

class SessionOutcomeEnum(str, Enum):
    ACHIEVED = "achieved"
    PARTIAL = "partial"
    FAILED = "failed"
    ONGOING = "ongoing"

class Session(BaseModel):
    """Entity model for Session."""

    session_id: str = ...
    started_at: datetime = ...
    ended_at: datetime | None = None
    goal: str | None = None
    outcome: SessionOutcomeEnum | None = None

