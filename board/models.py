from pydantic import BaseModel, Field
from enum import Enum
from typing import Any, Optional
from datetime import datetime
import uuid


class TaskStatus(str, Enum):
    NEEDED = "needed"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class AgentId(str, Enum):
    HOST = "host"
    OPTIONS = "options"
    CRITERIA = "criteria"
    RESULT = "result"
    PLAN_B = "plan_b"


class AgentState(str, Enum):
    IDLE = "idle"
    OBSERVING = "observing"
    THINKING = "thinking"
    ACTING = "acting"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str
    status: TaskStatus = TaskStatus.NEEDED
    assigned_to: Optional[AgentId] = None
    created_by: Optional[AgentId] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    input_data: dict[str, Any] = {}
    output_data: dict[str, Any] = {}
    depends_on: list[str] = []
    version: int = 1
    cancelled_reason: Optional[str] = None
    cleared: bool = False


class AgentStatus(BaseModel):
    agent_id: AgentId
    state: AgentState = AgentState.IDLE
    current_task_id: Optional[str] = None
    last_thought: Optional[str] = None
    last_active: Optional[datetime] = None


class BoardEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    agent_id: Optional[AgentId] = None
    event_type: str
    title: str
    detail: str = ""
    data: dict[str, Any] = {}


class CoordinationBoard(BaseModel):
    epoch: int = 1
    scenario_id: Optional[str] = None
    tasks: list[Task] = []
    agents: dict[AgentId, AgentStatus] = {}
    event_log: list[BoardEvent] = []
    user_expression: Optional[str] = None
    conversation_history: list[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
