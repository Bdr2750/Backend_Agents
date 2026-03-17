from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    agent_poll_interval: float = 0.5
    host: str = "0.0.0.0"
    port: int = 8001
    cors_origins: list[str] = ["*"]

    @property
    def gemini_api_keys(self) -> list[str]:
        keys = [self.gemini_api_key_1, self.gemini_api_key_2, self.gemini_api_key_3]
        return [k for k in keys if k and k != "your-key-here"]

    model_config = {"env_file": ".env"}


settings = Settings()
