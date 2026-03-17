import json
from board.models import AgentId, Task
from agents.base import BaseAgent
from agents.prompts import RESULT_SYSTEM_PROMPT


class ResultAgent(BaseAgent):
    AGENT_ID = AgentId.RESULT
    HANDLES_TASK_TYPES = ["make_decision"]

    async def _think(self, task: Task) -> dict:
        options = task.input_data.get("options", [])
        criteria = task.input_data.get("criteria", {})
        persona = task.input_data.get("persona", {})
        expression = self.board.board.user_expression or ""

        await self._broadcast_thinking(
            "Result is scoring options and composing recommendation",
            "Applying criteria weights, scoring each option, and selecting the best",
        )
        response = await self.gemini.generate(
            system_prompt=RESULT_SYSTEM_PROMPT,
            user_message=(
                f"Original user expression: \"{expression}\"\n\n"
                f"Options to evaluate:\n{json.dumps(options, indent=2)}\n\n"
                f"Criteria framework:\n{json.dumps(criteria, indent=2)}\n\n"
                f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                f"Score each option on each criterion, compute weighted totals, rank them, and make the final recommendation."
            ),
        )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        result = self._safe_parse_json(plan["gemini_response"])
        return {"recommendation": result}
