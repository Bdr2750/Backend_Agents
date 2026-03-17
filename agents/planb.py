import json
from board.models import AgentId, Task, TaskStatus
from agents.base import BaseAgent
from agents.prompts import (
    PLAN_B_SYSTEM_PROMPT,
    RECALCULATE_OPTIONS_PROMPT,
    CRITERIA_SYSTEM_PROMPT,
    RESULT_SYSTEM_PROMPT,
)


class PlanBAgent(BaseAgent):
    AGENT_ID = AgentId.PLAN_B
    HANDLES_TASK_TYPES = ["disrupt"]

    async def _think(self, task: Task) -> dict:
        disruption = task.input_data.get("disruption_event", {})
        current_tasks = [
            {"id": t.id, "type": t.type, "status": t.status.value}
            for t in self.board.board.tasks
            if t.status != TaskStatus.CANCELLED
        ]

        await self._broadcast_thinking(
            "Plan B: disruption detected",
            f"Analyzing impact of: {disruption.get('reason', 'unknown change')}",
        )
        response = await self.gemini.generate(
            system_prompt=PLAN_B_SYSTEM_PROMPT,
            user_message=(
                f"Disruption event:\n{json.dumps(disruption, indent=2)}\n\n"
                f"Current active tasks:\n{json.dumps(current_tasks, indent=2)}\n\n"
                f"Analyze the impact and determine what needs to change."
            ),
        )
        return {"gemini_response": response}

    async def _act(self, task: Task, plan: dict) -> dict:
        analysis = self._safe_parse_json(plan["gemini_response"])
        # Cancel all stale tasks and bump epoch, but exclude our own task
        await self.board.cancel_stale_tasks(
            reason=analysis.get("explanation", "Disruption occurred"),
            exclude_task_id=task.id,
        )
        # Keep this task visible by updating its version to the new epoch
        task.version = self.board.board.epoch
        # Broadcast so frontend sees Plan B as in_progress in the new epoch
        await self.board.ws.broadcast_board_update(self.board.board)

        # ── Do ALL recalculation here so sub-tasks exist BEFORE this task completes ──

        new_constraints = analysis.get("new_constraints", {})
        original_need = task.input_data.get("original_need", {})
        persona = task.input_data.get("persona", {})

        # Fallback: if persona wasn't passed in, look it up from the last completed HOST task
        if not persona:
            for t in reversed(self.board.board.tasks):
                if t.type == "translate_need" and t.status.value == "done" and t.output_data:
                    persona = t.output_data.get("persona", {})
                    break

        # Find previous options from the last completed OPTIONS task
        previous_options = []
        for t in reversed(self.board.board.tasks):
            if t.type in ("generate_options", "recalculate_options") and t.status.value == "done" and t.output_data:
                previous_options = t.output_data.get("options", [])
                break

        # Merge new constraints into the original need
        updated_need = {**original_need}
        if new_constraints.get("destination"):
            updated_need["destination"] = new_constraints["destination"]
        if new_constraints.get("timeframe"):
            updated_need["timeframe"] = new_constraints["timeframe"]

        # Merge all other constraint info into implicit_constraints
        existing_constraints = updated_need.get("implicit_constraints", [])
        if isinstance(existing_constraints, list):
            for key, value in new_constraints.items():
                if key not in ("destination", "timeframe") and value:
                    existing_constraints.append(f"{key}: {value}")
            updated_need["implicit_constraints"] = existing_constraints

        disruption_context = {
            **task.input_data.get("disruption_event", {}),
            "analysis_summary": analysis.get("disruption_summary", ""),
            "analysis_explanation": analysis.get("explanation", ""),
        }

        # 1. Recalculate Options
        await self._broadcast_thinking(
            "Plan B: recalculating options",
            "Replacing invalid options with new alternatives",
        )
        options_response = await self.gemini.generate(
            system_prompt=RECALCULATE_OPTIONS_PROMPT,
            user_message=(
                f"Updated structured need:\n{json.dumps(updated_need, indent=2)}\n\n"
                f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                f"Disruption:\n{json.dumps(disruption_context, indent=2)}\n\n"
                f"Previous options (before disruption):\n{json.dumps(previous_options, indent=2)}\n\n"
                f"Review each option. Keep valid ones, replace invalid ones with new alternatives."
            ),
        )
        options_data = self._safe_parse_json(options_response)
        options = options_data.get("options", options_data)

        options_task = Task(
            type="recalculate_options",
            created_by=AgentId.PLAN_B,
            input_data={"structured_need": updated_need, "persona": persona},
        )
        await self.board.add_completed_task(options_task, {"options": options})

        # 2. Recalculate Criteria
        await self._broadcast_thinking(
            "Plan B: recalculating criteria",
            "Adjusting evaluation framework for new constraints",
        )
        criteria_response = await self.gemini.generate(
            system_prompt=CRITERIA_SYSTEM_PROMPT,
            user_message=(
                f"Structured need:\n{json.dumps(updated_need, indent=2)}\n\n"
                f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                f"Define the 3 most relevant criteria and their weights for this decision."
            ),
        )
        criteria_data = self._safe_parse_json(criteria_response)

        criteria_task = Task(
            type="recalculate_criteria",
            created_by=AgentId.PLAN_B,
            input_data={"structured_need": updated_need, "persona": persona},
        )
        await self.board.add_completed_task(criteria_task, {"criteria": criteria_data})

        # 3. Score & Pick Winner
        await self._broadcast_thinking(
            "Plan B: scoring options and picking winner",
            "Applying criteria to updated options",
        )
        expression = self.board.board.user_expression or ""
        result_response = await self.gemini.generate(
            system_prompt=RESULT_SYSTEM_PROMPT,
            user_message=(
                f"Original user expression: \"{expression}\"\n\n"
                f"Options to evaluate:\n{json.dumps(options, indent=2)}\n\n"
                f"Criteria framework:\n{json.dumps(criteria_data, indent=2)}\n\n"
                f"User persona:\n{json.dumps(persona, indent=2)}\n\n"
                f"Score each option on each criterion, compute weighted totals, rank them, and make the final recommendation."
            ),
        )
        result_data = self._safe_parse_json(result_response)

        result_task = Task(
            type="make_decision",
            created_by=AgentId.PLAN_B,
            input_data={"options": options, "criteria": criteria_data},
        )
        await self.board.add_completed_task(result_task, {"recommendation": result_data})

        return {"disruption_analysis": analysis}
