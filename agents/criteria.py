import json
from board.models import AgentId, Task
from agents.base import BaseAgent
from agents.prompts import CRITERIA_SYSTEM_PROMPT


class CriteriaAgent(BaseAgent):
    AGENT_ID = AgentId.CRITERIA
    HANDLES_TASK_TYPES = ["apply_criteria", "recalculate_criteria"]

    async def _think(self, task: Task) -> dict:
        need = task.input_data.get("structured_need", {})
        persona = task.input_data.get("persona", {})

        await self._broadcast_thinking(
            "Criteria is defining evaluation framework",
            "Choosing criteria and weights based on persona",
        )
        response = await self.gemini.generate(
            system_prompt=CRITERIA_SYSTEM_PROMPT,
            user_message=(
                f"Structured need:\n{json.dumps(need, indent=2)}\n\n"
                f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                f"Define the 3 most relevant criteria and their weights for this decision."
            ),
        )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        parsed = self._safe_parse_json(plan["gemini_response"])
        return {"criteria": parsed}

    # No _post_act needed — the AND gate in BoardState auto-creates
    # the Result task when both Options and Criteria are Done.
