import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from board.models import Task, AgentId, TaskStatus
from board.state import BoardState
from agents.prompts import FOLLOWUP_CLASSIFIER_PROMPT, HOST_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    message: str

router = APIRouter(prefix="/api")


def get_board() -> BoardState:
    from main import app_state
    return app_state["board"]


def get_gemini():
    from main import app_state
    return app_state["gemini"]


def _get_previous_context(board: BoardState) -> tuple[dict, dict]:
    """Extract the latest structured_need and persona from completed HOST tasks."""
    previous_need = {}
    previous_persona = {}
    for t in board.board.tasks:
        if t.type == "translate_need" and t.status.value == "done" and t.output_data:
            previous_need = t.output_data.get("structured_need", {})
            previous_persona = t.output_data.get("persona", {})
    return previous_need, previous_persona


async def _classify_followup(gemini, original_expression: str, structured_need: dict, new_message: str) -> dict:
    """Use Gemini to classify a follow-up as 'constraint' or 'fundamental'."""
    user_message = (
        f"Original user request: \"{original_expression}\"\n\n"
        f"Structured need extracted from it:\n{json.dumps(structured_need, indent=2)}\n\n"
        f"New follow-up message: \"{new_message}\"\n\n"
        f"Classify this follow-up."
    )
    response = await gemini.generate(
        system_prompt=FOLLOWUP_CLASSIFIER_PROMPT,
        user_message=user_message,
    )
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # If parsing fails, default to fundamental (safer — re-runs everything)
        logger.warning(f"Failed to parse classifier response: {response}")
        return {"type": "fundamental", "reasoning": "Classification failed, defaulting to full re-run"}


@router.post("/scenarios/grandmother")
async def trigger_grandmother():
    board = get_board()
    await board.reset()
    expression = "I'm bored, I wish I could see my grandchildren"
    board.board.user_expression = expression
    board.board.scenario_id = "grandmother"
    task = Task(
        type="translate_need",
        input_data={"user_expression": expression},
    )
    await board.add_task(task)
    return {"status": "started", "scenario": "grandmother"}


@router.post("/scenarios/planb")
async def trigger_planb():
    board = get_board()
    await board.reset()
    expression = "I have an important meeting in Chicago next Tuesday"
    board.board.user_expression = expression
    board.board.scenario_id = "planb_chicago"
    task = Task(
        type="translate_need",
        input_data={"user_expression": expression},
    )
    await board.add_task(task)
    return {"status": "started", "scenario": "planb_chicago"}


@router.post("/scenarios/planb/disrupt")
async def trigger_disruption():
    board = get_board()
    # Find the original structured need from the Host's completed task
    original_need = {}
    for t in board.board.tasks:
        if t.type == "translate_need" and t.output_data:
            original_need = t.output_data.get("structured_need", {})
            break

    disruption_task = Task(
        type="disrupt",
        input_data={
            "disruption_event": {
                "type": "location_change",
                "original": "Chicago",
                "new": "Indianapolis",
                "reason": "The meeting has been rescheduled to Indianapolis",
            },
            "original_need": original_need,
        },
    )
    await board.add_task(disruption_task)
    return {"status": "disruption_injected"}


@router.post("/board/reset")
async def reset_board():
    board = get_board()
    await board.reset()
    return {"status": "reset"}


@router.post("/chat")
async def chat(body: ChatMessage):
    board = get_board()
    expression = body.message.strip()
    has_history = len(board.board.tasks) > 0

    # Always track conversation history
    board.board.conversation_history.append(expression)

    if has_history:
        # Follow-up: classify whether it's a constraint or fundamental change
        previous_need, previous_persona = _get_previous_context(board)
        original_expression = board.board.user_expression or ""

        gemini = get_gemini()
        classification = await _classify_followup(
            gemini, original_expression, previous_need, expression
        )
        followup_type = classification.get("type", "fundamental")
        logger.info(f"Follow-up classified as: {followup_type} — {classification.get('reasoning', '')}")

        if followup_type == "constraint":
            # CONSTRAINT CHANGE → Plan B path (build on previous results)
            disruption_event = classification.get("disruption_event", {})
            board.board.user_expression = expression

            task = Task(
                type="disrupt",
                input_data={
                    "disruption_event": disruption_event,
                    "original_need": previous_need,
                    "persona": previous_persona,
                },
            )
            await board.add_task(task)
            return {"status": "constraint_followup", "expression": expression, "classification": classification}
        else:
            # FUNDAMENTAL CHANGE → Re-parse need/persona silently (no Host agent)
            # then go straight to Options + Criteria
            await board.cancel_stale_tasks(f"User follow-up: {expression}")
            board.board.user_expression = expression

            gemini = get_gemini()
            user_message = (
                f"CONTEXT: This is a follow-up message. The user previously expressed a need that was analyzed as:\n"
                f"{json.dumps(previous_need, indent=2)}\n\n"
                f"Their persona was:\n{json.dumps(previous_persona, indent=2)}\n\n"
                f"NOW the user says: \"{expression}\"\n\n"
                f"Update the structured need and persona based on this new input. "
                f"Keep what's still relevant, modify what changed."
            )
            response = await gemini.generate(
                system_prompt=HOST_SYSTEM_PROMPT,
                user_message=user_message,
            )
            try:
                parsed = json.loads(response)
            except json.JSONDecodeError:
                parsed = {}

            need = parsed.get("structured_need", previous_need)
            persona = parsed.get("persona", previous_persona)

            # Store as a completed translate_need task so downstream agents can find it
            host_task = Task(
                type="translate_need",
                created_by=AgentId.HOST,
                input_data={"user_expression": expression},
                output_data={"structured_need": need, "persona": persona},
            )
            host_task.status = TaskStatus.DONE
            board.board.tasks.append(host_task)
            await board.ws.broadcast_board_update(board.board)

            # Directly create Options + Criteria tasks (skip Host agent)
            options_task = Task(
                type="generate_options",
                created_by=AgentId.HOST,
                input_data={"structured_need": need, "persona": persona},
            )
            await board.add_task(options_task)

            criteria_task = Task(
                type="apply_criteria",
                created_by=AgentId.HOST,
                input_data={"structured_need": need, "persona": persona},
            )
            await board.add_task(criteria_task)

            return {"status": "fundamental_followup", "expression": expression, "classification": classification}
    else:
        # First message: fresh start
        board.board.user_expression = expression
        board.board.scenario_id = "chat"
        task = Task(
            type="translate_need",
            input_data={"user_expression": expression},
        )
        await board.add_task(task)
        return {"status": "started", "expression": expression}


@router.get("/board")
async def get_board_state():
    board = get_board()
    return board.board.model_dump(mode="json")
