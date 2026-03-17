import json
from board.models import AgentId, Task
from agents.base import BaseAgent
from agents.prompts import PERSONA_SYSTEM_PROMPT


class PersonaAgent(BaseAgent):
    AGENT_ID = AgentId.PERSONA
    HANDLES_TASK_TYPES = ["define_persona"]

    async def _think(self, task: Task) -> dict:
        need = task.input_data.get("structured_need", {})
        await self._broadcast_thinking(
            "Persona is building user profile",
            f"Analyzing emotional context and constraints",
        )
        response = await self.gemini.generate(
            system_prompt=PERSONA_SYSTEM_PROMPT,
            user_message=f"Structured need:\n{json.dumps(need, indent=2)}\n\nBuild a detailed user persona based on this need.",
        )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        persona = self._safe_parse_json(plan["gemini_response"])
        return {"persona": persona}
