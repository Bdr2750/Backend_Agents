import json
from board.models import Task, AgentId
from agents.base import BaseAgent
from agents.prompts import OPTIONS_SYSTEM_PROMPT, RECALCULATE_OPTIONS_PROMPT


class OptionsAgent(BaseAgent):
    AGENT_ID = AgentId.OPTIONS
    HANDLES_TASK_TYPES = ["generate_options", "recalculate_options"]

    async def _think(self, task: Task) -> dict:
        need = task.input_data.get("structured_need", {})
        persona = task.input_data.get("persona", {})
        disruption = task.input_data.get("disruption", {})
        previous_options = task.input_data.get("previous_options", [])

        if task.type == "recalculate_options" and previous_options:
            # Plan B recalculation: keep valid options, replace invalid ones
            await self._broadcast_thinking(
                "Options is recalculating after disruption",
                f"Reviewing options against new constraints",
            )
            response = await self.gemini.generate(
                system_prompt=RECALCULATE_OPTIONS_PROMPT,
                user_message=(
                    f"Updated structured need:\n{json.dumps(need, indent=2)}\n\n"
                    f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                    f"Disruption:\n{json.dumps(disruption, indent=2)}\n\n"
                    f"Previous options (before disruption):\n{json.dumps(previous_options, indent=2)}\n\n"
                    f"Review each option. Keep valid ones, replace invalid ones with new alternatives."
                ),
            )
        else:
            # Fresh generation
            await self._broadcast_thinking(
                "Options is generating travel choices",
                f"Considering {need.get('destination', 'destination')} with persona constraints",
            )
            response = await self.gemini.generate(
                system_prompt=OPTIONS_SYSTEM_PROMPT,
                user_message=(
                    f"Structured need:\n{json.dumps(need, indent=2)}\n\n"
                    f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                    f"Generate exactly 4 concrete travel options."
                ),
            )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        options_data = self._safe_parse_json(plan["gemini_response"])
        return {"options": options_data.get("options", options_data)}

