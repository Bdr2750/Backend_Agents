import json
from typing import Any
from fastapi import WebSocket
from datetime import datetime

from board.models import (
    CoordinationBoard,
    AgentId,
    AgentState,
    BoardEvent,
)
from ws.protocol import WSMessage, WSMessageType


class WSManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, message: WSMessage):
        data = message.model_dump_json()
        dead: list[WebSocket] = []
        for conn in self._connections:
            try:
                await conn.send_text(data)
            except Exception:
                dead.append(conn)
        for conn in dead:
            self._connections.remove(conn)

    async def broadcast_board_update(self, board: CoordinationBoard):
        await self.broadcast(
            WSMessage(
                type=WSMessageType.BOARD_FULL,
                payload=board.model_dump(mode="json"),
            )
        )

    async def broadcast_agent_state(
        self,
        agent_id: AgentId,
        state: AgentState,
        task_id: str | None = None,
        thought: str | None = None,
    ):
        await self.broadcast(
            WSMessage(
                type=WSMessageType.AGENT_STATE,
                payload={
                    "agent_id": agent_id.value,
                    "state": state.value,
                    "task_id": task_id,
                    "thought": thought,
                },
            )
        )

    async def broadcast_event(self, event: BoardEvent):
        await self.broadcast(
            WSMessage(
                type=WSMessageType.EVENT,
                payload=event.model_dump(mode="json"),
            )
        )
