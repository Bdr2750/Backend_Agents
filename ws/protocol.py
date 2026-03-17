from pydantic import BaseModel, Field
from enum import Enum
from typing import Any
from datetime import datetime


class WSMessageType(str, Enum):
    BOARD_FULL = "board_full"
    AGENT_STATE = "agent_state"
    EVENT = "event"
    SCENARIO_STARTED = "scenario_started"
    ERROR = "error"


class WSMessage(BaseModel):
    type: WSMessageType
    payload: dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)
