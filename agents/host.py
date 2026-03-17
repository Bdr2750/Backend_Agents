import asyncio
import json
from board.models import AgentId, Task
from agents.base import BaseAgent
from agents.prompts import HOST_SYSTEM_PROMPT


class HostAgent(BaseAgent):
    AGENT_ID = AgentId.HOST
    HANDLES_TASK_TYPES = ["translate_need"]

    async def _think(self, task: Task) -> dict:
        user_expression = task.input_data.get("user_expression", "")
        is_followup = task.input_data.get("is_followup", False)

        await self._broadcast_thinking(
            "Host is interpreting the user's expression",
            f'Analyzing: "{user_expression[:100]}"',
        )

        if is_followup:
            previous_need = task.input_data.get("previous_need", {})
            previous_persona = task.input_data.get("previous_persona", {})
            user_message = (
                f"CONTEXT: This is a follow-up message. The user previously expressed a need that was analyzed as:\n"
                f"{json.dumps(previous_need, indent=2)}\n\n"
                f"Their persona was:\n{json.dumps(previous_persona, indent=2)}\n\n"
                f"NOW the user says: \"{user_expression}\"\n\n"
                f"Update the structured need and persona based on this new input. "
                f"Keep what's still relevant, modify what changed."
            )
        else:
            user_message = f'The user said: "{user_expression}"\n\nTranslate this into a structured need and build a user persona.'

        response = await self.gemini.generate(
            system_prompt=HOST_SYSTEM_PROMPT,
            user_message=user_message,
        )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        parsed = self._safe_parse_json(plan["gemini_response"])
        return {
            "structured_need": parsed.get("structured_need", parsed),
            "persona": parsed.get("persona", {}),
        }

    async def _post_act(self, task: Task, result: dict):
        need = result["structured_need"]
        persona = result["persona"]

        # Wait for the Host's two-phase speech to finish on the frontend
        # before creating downstream tasks for Options and Criteria agents.
        await asyncio.sleep(20)

        # Create Options and Criteria tasks — both get NEEDED simultaneously.
        # Random polling jitter determines which agent claims first.
        # The AND gate in BoardState will auto-create the Result task when both are Done.
        options_task = Task(
            type="generate_options",
            created_by=AgentId.HOST,
            input_data={"structured_need": need, "persona": persona},
        )
        await self.board.add_task(options_task)

        criteria_task = Task(
            type="apply_criteria",
            created_by=AgentId.HOST,
            input_data={"structured_need": need, "persona": persona},
        )
        await self.board.add_task(criteria_task)
