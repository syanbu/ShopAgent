from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    dashscope_api_key: str
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_sdk_base_url: str = "https://dashscope.aliyuncs.com/api/v1"
    chat_model: str = "qwen3.7-max"
    embedding_model: str = "qwen3.7-text-embedding"
    rerank_model: str = "qwen3-rerank"
    embedding_dimension: int = 1024
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "product_text_chunks_v1"
    retrieval_chunk_limit: int = 30
    rerank_product_limit: int = 10
    final_product_limit: int = Field(default=3, ge=1, le=3)
    model_timeout_seconds: float = 30.0
    qdrant_timeout_seconds: float = 10.0
    dataset_root: Path = Field(default=Path("ecommerce_agent_dataset"))
    public_base_url: str = "http://127.0.0.1:8000"
