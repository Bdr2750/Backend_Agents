import logging

from board.state import BoardState
from llm.gemini_client import GeminiClient
from ws.manager import WSManager
from agents.host import HostAgent
from agents.options import OptionsAgent
from agents.criteria import CriteriaAgent
from agents.result import ResultAgent
from agents.planb import PlanBAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    def __init__(
        self,
        board: BoardState,
        gemini: GeminiClient,
        ws_manager: WSManager,
    ):
        self.agents = [
            HostAgent(board, gemini, ws_manager),
            OptionsAgent(board, gemini, ws_manager),
            CriteriaAgent(board, gemini, ws_manager),
            ResultAgent(board, gemini, ws_manager),
            PlanBAgent(board, gemini, ws_manager),
        ]

    async def start_all(self):
        for agent in self.agents:
            await agent.start()
        logger.info(f"All {len(self.agents)} agents started")

    async def stop_all(self):
        for agent in self.agents:
            await agent.stop()
        logger.info("All agents stopped")
