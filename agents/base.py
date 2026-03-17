import asyncio
import json
import logging
import random
from abc import ABC, abstractmethod
from typing import Optional

from board.models import AgentId, AgentState, Task, BoardEvent
from board.state import BoardState
from llm.gemini_client import GeminiClient
from ws.manager import WSManager

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    AGENT_ID: AgentId
    HANDLES_TASK_TYPES: list[str]
    POLL_INTERVAL: float = 0.5

    def __init__(
        self,
        board: BoardState,
        gemini: GeminiClient,
        ws_manager: WSManager,
    ):
        self.board = board
        self.gemini = gemini
        self.ws = ws_manager
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Agent {self.AGENT_ID.value} started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Agent {self.AGENT_ID.value} stopped")

    async def _run_loop(self):
        while self._running:
            try:
                await self._update_state(AgentState.OBSERVING)
                claimable_task = await self._observe()

                if claimable_task:
                    # THINK
                    await self._update_state(
                        AgentState.THINKING, task_id=claimable_task.id
                    )
                    plan = await self._think(claimable_task)

                    # Check cancellation after LLM call
                    if self.board.is_task_cancelled(claimable_task.id):
                        logger.info(
                            f"Agent {self.AGENT_ID.value}: task {claimable_task.id} cancelled during thinking"
                        )
                        await self._update_state(AgentState.IDLE)
                        continue

                    # ACT
                    await self._update_state(
                        AgentState.ACTING, task_id=claimable_task.id
                    )
                    result = await self._act(claimable_task, plan)

                    # Check cancellation again after acting
                    if self.board.is_task_cancelled(claimable_task.id):
                        logger.info(
                            f"Agent {self.AGENT_ID.value}: task {claimable_task.id} cancelled during acting"
                        )
                        await self._update_state(AgentState.IDLE)
                        continue

                    # COMPLETE
                    await self.board.complete_task(claimable_task.id, result)
                    await self._post_act(claimable_task, result)
                    await self._update_state(AgentState.IDLE)
                else:
                    await self._update_state(AgentState.IDLE)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"Agent {self.AGENT_ID.value} error: {e}", exc_info=True
                )
                await self._update_state(AgentState.IDLE)

            # Random jitter so agents don't always poll in the same order
            jitter = random.uniform(0, 0.3)
            await asyncio.sleep(self.POLL_INTERVAL + jitter)

    async def _observe(self) -> Optional[Task]:
        for task_type in self.HANDLES_TASK_TYPES:
            task = await self.board.try_claim_task(
                task_type=task_type,
                agent_id=self.AGENT_ID,
            )
            if task:
                return task
        return None

    @abstractmethod
    async def _think(self, task: Task) -> dict:
        ...

    @abstractmethod
    async def _act(self, task: Task, plan: dict) -> dict:
        ...

    async def _post_act(self, task: Task, result: dict):
        pass

    async def _update_state(
        self,
        state: AgentState,
        task_id: str | None = None,
        thought: str | None = None,
    ):
        self.board.update_agent_state(self.AGENT_ID, state, task_id, thought)
        await self.ws.broadcast_agent_state(
            self.AGENT_ID, state, task_id, thought
        )

    async def _broadcast_thinking(self, title: str, detail: str = ""):
        event = BoardEvent(
            agent_id=self.AGENT_ID,
            event_type="agent_thinking",
            title=title,
            detail=detail,
        )
        self.board.board.event_log.append(event)
        await self.ws.broadcast_event(event)

    def _safe_parse_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            cleaned = text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            return json.loads(cleaned.strip())
