import json
from typing import Optional
from datetime import datetime

from board.models import (
    CoordinationBoard,
    Task,
    TaskStatus,
    AgentId,
    AgentState,
    AgentStatus,
    BoardEvent,
)
from board.lock import TaskClaimManager
from ws.manager import WSManager


class BoardState:
    def __init__(self, ws_manager: WSManager):
        self.board = CoordinationBoard()
        self.claim_manager = TaskClaimManager()
        self.ws = ws_manager
        for agent_id in AgentId:
            self.board.agents[agent_id] = AgentStatus(agent_id=agent_id)

    async def add_task(self, task: Task):
        task.version = self.board.epoch
        self.board.tasks.append(task)
        event = BoardEvent(
            agent_id=task.created_by,
            event_type="task_created",
            title=f"New task: {task.type.replace('_', ' ')}",
            detail=f"Task {task.id} queued",
            data={"task_id": task.id, "task_type": task.type},
        )
        self.board.event_log.append(event)
        await self.ws.broadcast_board_update(self.board)
        await self.ws.broadcast_event(event)

    async def try_claim_task(
        self, task_type: str, agent_id: AgentId
    ) -> Optional[Task]:
        task = await self.claim_manager.try_claim(
            self.board, task_type, agent_id
        )
        if task:
            event = BoardEvent(
                agent_id=agent_id,
                event_type="task_claimed",
                title=f"{agent_id.value.replace('_', ' ').title()} claimed task",
                detail=f"Working on: {task.type.replace('_', ' ')}",
                data={"task_id": task.id, "task_type": task.type},
            )
            self.board.event_log.append(event)
            await self.ws.broadcast_board_update(self.board)
            await self.ws.broadcast_event(event)
        return task

    # AND gate pairs: (options_type, criteria_type) that together trigger make_decision
    AND_GATE_PAIRS = [
        ("generate_options", "apply_criteria"),
        ("recalculate_options", "recalculate_criteria"),
    ]

    async def complete_task(self, task_id: str, output_data: dict):
        task = self._get_task(task_id)
        if not task:
            return
        task.status = TaskStatus.DONE
        task.output_data = output_data
        task.completed_at = datetime.utcnow()
        event = BoardEvent(
            agent_id=task.assigned_to,
            event_type="task_completed",
            title=f"Task completed: {task.type.replace('_', ' ')}",
            detail=json.dumps(output_data, default=str)[:300],
            data={"task_id": task.id, "task_type": task.type, "output": output_data},
        )
        self.board.event_log.append(event)
        await self.ws.broadcast_board_update(self.board)
        await self.ws.broadcast_event(event)

        # Check if AND gate should fire
        await self._check_and_gate(task)

    async def _check_and_gate(self, completed_task: Task):
        """AND gate: when both Options and Criteria are Done, trigger Result."""
        for options_type, criteria_type in self.AND_GATE_PAIRS:
            if completed_task.type not in (options_type, criteria_type):
                continue

            # Find the partner task in the same epoch
            epoch = completed_task.version
            options_task = self._find_done_task(options_type, epoch)
            criteria_task = self._find_done_task(criteria_type, epoch)

            if not options_task or not criteria_task:
                return

            # Both are Done — AND gate fires!
            # 1. Mark both as cleared (visual reset)
            options_task.cleared = True
            criteria_task.cleared = True

            # 2. Create the Result task with combined input
            result_task = Task(
                type="make_decision",
                created_by=None,  # created by switchboard AND gate
                input_data={
                    "options": options_task.output_data.get("options", []),
                    "criteria": criteria_task.output_data.get("criteria", {}),
                    "structured_need": options_task.input_data.get("structured_need", {}),
                    "persona": options_task.input_data.get("persona", {}),
                },
            )
            await self.add_task(result_task)

            # 3. Broadcast AND gate event
            event = BoardEvent(
                agent_id=None,
                event_type="and_gate_triggered",
                title="AND gate: Options + Criteria complete",
                detail="Result agent triggered",
                data={
                    "options_task_id": options_task.id,
                    "criteria_task_id": criteria_task.id,
                    "result_task_id": result_task.id,
                },
            )
            self.board.event_log.append(event)
            await self.ws.broadcast_board_update(self.board)
            await self.ws.broadcast_event(event)
            return

    def _find_done_task(self, task_type: str, epoch: int) -> Optional[Task]:
        """Find a Done task of the given type in the given epoch."""
        for task in reversed(self.board.tasks):
            if (
                task.type == task_type
                and task.status == TaskStatus.DONE
                and task.version == epoch
                and not task.cleared
            ):
                return task
        return None

    async def add_completed_task(self, task: Task, output_data: dict):
        """Add a task that is already completed (used by Plan B to write results directly)."""
        task.version = self.board.epoch
        task.status = TaskStatus.DONE
        task.output_data = output_data
        task.completed_at = datetime.utcnow()
        self.board.tasks.append(task)
        event = BoardEvent(
            agent_id=task.created_by,
            event_type="task_completed",
            title=f"Task completed: {task.type.replace('_', ' ')}",
            detail=json.dumps(output_data, default=str)[:300],
            data={"task_id": task.id, "task_type": task.type, "output": output_data},
        )
        self.board.event_log.append(event)
        await self.ws.broadcast_board_update(self.board)
        await self.ws.broadcast_event(event)

    async def cancel_stale_tasks(self, reason: str, exclude_task_id: str | None = None):
        """Cancel NEEDED/IN_PROGRESS tasks and bump epoch. Used by Plan B (constraint changes)."""
        cancelled_ids = []
        for task in self.board.tasks:
            if task.status in (TaskStatus.NEEDED, TaskStatus.IN_PROGRESS):
                if exclude_task_id and task.id == exclude_task_id:
                    continue
                task.status = TaskStatus.CANCELLED
                task.cancelled_reason = reason
                cancelled_ids.append(task.id)
        self.board.epoch += 1
        event = BoardEvent(
            agent_id=AgentId.PLAN_B,
            event_type="disruption",
            title="Disruption: plans invalidated",
            detail=f"{reason}. {len(cancelled_ids)} task(s) cancelled. Epoch bumped to {self.board.epoch}.",
            data={"cancelled_task_ids": cancelled_ids, "new_epoch": self.board.epoch},
        )
        self.board.event_log.append(event)
        await self.ws.broadcast_board_update(self.board)
        await self.ws.broadcast_event(event)

    async def cancel_all_tasks(self, reason: str):
        """Cancel ALL tasks (including DONE) and bump epoch. Used for fundamental re-runs."""
        cancelled_ids = []
        for task in self.board.tasks:
            if task.status != TaskStatus.CANCELLED:
                task.status = TaskStatus.CANCELLED
                task.cancelled_reason = reason
                cancelled_ids.append(task.id)
        self.board.epoch += 1
        event = BoardEvent(
            agent_id=None,
            event_type="full_rerun",
            title="Full re-run: all plans cleared",
            detail=f"{reason}. {len(cancelled_ids)} task(s) cancelled. Epoch bumped to {self.board.epoch}.",
            data={"cancelled_task_ids": cancelled_ids, "new_epoch": self.board.epoch},
        )
        self.board.event_log.append(event)
        await self.ws.broadcast_board_update(self.board)
        await self.ws.broadcast_event(event)

    def is_task_cancelled(self, task_id: str) -> bool:
        task = self._get_task(task_id)
        return task is not None and task.status == TaskStatus.CANCELLED

    def update_agent_state(
        self,
        agent_id: AgentId,
        state: AgentState,
        task_id: str | None = None,
        thought: str | None = None,
    ):
        agent = self.board.agents.get(agent_id)
        if agent:
            agent.state = state
            agent.current_task_id = task_id
            agent.last_active = datetime.utcnow()
            if thought:
                agent.last_thought = thought

    def get_task_output(self, task_id: str) -> dict:
        task = self._get_task(task_id)
        return task.output_data if task else {}

    async def reset(self):
        self.board = CoordinationBoard()
        self.claim_manager = TaskClaimManager()
        for agent_id in AgentId:
            self.board.agents[agent_id] = AgentStatus(agent_id=agent_id)
        await self.ws.broadcast_board_update(self.board)
        for agent_id in AgentId:
            await self.ws.broadcast_agent_state(agent_id, AgentState.IDLE)

    def update_task_input(self, task_id: str, extra_input: dict):
        """Merge extra data into a task's input_data (used to pass output downstream)."""
        task = self._get_task(task_id)
        if task:
            task.input_data.update(extra_input)

    def find_task_by_type(self, task_type: str) -> Optional[Task]:
        """Find the latest task of a given type."""
        for task in reversed(self.board.tasks):
            if task.type == task_type and task.status == TaskStatus.NEEDED:
                return task
        return None

    def _get_task(self, task_id: str) -> Optional[Task]:
        return next((t for t in self.board.tasks if t.id == task_id), None)
