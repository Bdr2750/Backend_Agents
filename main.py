import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from board.state import BoardState
from ws.manager import WSManager
from llm.gemini_client import GeminiClient
from agents.registry import AgentRegistry
from api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global app state accessible from routes
app_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP
    ws_manager = WSManager()
    board = BoardState(ws_manager)

    gemini_keys = settings.gemini_api_keys
    if not gemini_keys:
        logger.warning("No Gemini API keys configured. Set GEMINI_API_KEY_1/2/3 in .env")
        gemini_keys = ["dummy"]

    gemini = GeminiClient(api_keys=gemini_keys, model=settings.gemini_model)
    registry = AgentRegistry(board, gemini, ws_manager)

    app_state["board"] = board
    app_state["ws_manager"] = ws_manager
    app_state["gemini"] = gemini
    app_state["registry"] = registry

    await registry.start_all()
    logger.info("AgentCLass system started")

    yield

    # SHUTDOWN
    await registry.stop_all()
    logger.info("AgentCLass system stopped")


app = FastAPI(
    title="AgentCLass - Multi-Agent Coordination System",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ws_manager: WSManager = app_state["ws_manager"]
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected")

    # Send initial board state
    board: BoardState = app_state["board"]
    await ws_manager.broadcast_board_update(board.board)

    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await websocket.receive_text()
            # Client messages could be used for future features
            logger.debug(f"WS received: {data}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")


@app.get("/health")
async def health():
    return {"status": "ok", "agents": len(app_state.get("registry", {}).agents if app_state.get("registry") else [])}
