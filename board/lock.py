import asyncio
from typing import Optional
from datetime import datetime

from board.models import CoordinationBoard, Task, TaskStatus, AgentId


class TaskClaimManager:
    def __init__(self):
        self._global_lock = asyncio.Lock()

    async def try_claim(
        self,
        board: CoordinationBoard,
        task_type: str,
        agent_id: AgentId,
    ) -> Optional[Task]:
        async with self._global_lock:
            # Only one agent can be IN_PROGRESS at a time
            if any(t.status == TaskStatus.IN_PROGRESS for t in board.tasks):
                return None

            for task in board.tasks:
                if (
                    task.type == task_type
                    and task.status == TaskStatus.NEEDED
                    and task.version == board.epoch
                    and self._dependencies_met(board, task)
                ):
                    task.status = TaskStatus.IN_PROGRESS
                    task.assigned_to = agent_id
                    task.started_at = datetime.utcnow()
                    return task
            return None

    def _dependencies_met(self, board: CoordinationBoard, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = next((t for t in board.tasks if t.id == dep_id), None)
            if dep is None or dep.status != TaskStatus.DONE:
                return False
        return True
