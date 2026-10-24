"""只读取本项目 .env 的模型配置，不依赖相邻项目。"""
from pathlib import Path
from typing import Literal
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / '.env',
        env_file_encoding='utf-8', extra='forbid', case_sensitive=False,
    )
    llm_provider: Literal['deepseek', 'sensenova'] = 'deepseek'
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str = 'deepseek-flash'
    deepseek_thinking: Literal['enabled', 'disabled'] = 'disabled'
    sensenova_api_key: SecretStr | None = None
    sensenova_base_url: str | None = None
    sensenova_model: str = 'deepseek-v4-flash'
    temperature: float = 0.3
    request_timeout: float = 60
    max_retries: int = 2


settings = Settings()
