import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from board.models import Task
from board.state import BoardState
from agents.prompts import FOLLOWUP_CLASSIFIER_PROMPT

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
    board.reset()
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
    board.reset()
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
    board.reset()
    await board.ws.broadcast_board_update(board.board)
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
            # FUNDAMENTAL CHANGE → Full re-run with context
            # Only cancel NEEDED/IN_PROGRESS; keep DONE tasks so frontend shows old data
            # until new results replace them in real time
            await board.cancel_stale_tasks(f"User follow-up: {expression}")
            board.board.user_expression = expression

            task = Task(
                type="translate_need",
                input_data={
                    "user_expression": expression,
                    "previous_need": previous_need,
                    "previous_persona": previous_persona,
                    "is_followup": True,
                },
            )
            await board.add_task(task)
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
