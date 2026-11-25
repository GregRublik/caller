"""Agent configuration loaded from the environment (pydantic-settings).

Same approach as the STT/TTS services: section classes with an env prefix and
one top-level ``settings`` singleton. ``.env`` is read from the working
directory when present; on the node the supervisor wrapper exports the file's
variables into the environment.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveKitSettings(BaseSettings):
    url: str = ""
    api_key: str = ""
    api_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LIVEKIT_", extra="ignore"
    )


class LLMSettings(BaseSettings):
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "qwen3-14b"
    # vLLM reads its key from VLLM_API_KEY, hence the alias.
    api_key: str = Field("EMPTY", validation_alias="VLLM_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LLM_", extra="ignore"
    )


class STTSettings(BaseSettings):
    base_url: str = "http://127.0.0.1:8001"

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="STT_", extra="ignore"
    )


class TTSSettings(BaseSettings):
    base_url: str = "http://127.0.0.1:8002"

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="TTS_", extra="ignore"
    )


class SIPSettings(BaseSettings):
    outbound_trunk_id: str = ""
    answer_timeout: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="SIP_", extra="ignore"
    )


class Settings(BaseSettings):
    log_level: str = "INFO"
    agent_name: str = "sales-agent"
    connect_check_timeout: float = 8.0
    recordings_dir: str = "/workspace/caller/recordings"

    livekit: LiveKitSettings
    llm: LLMSettings
    stt: STTSettings
    tts: TTSSettings
    sip: SIPSettings

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings(
    livekit=LiveKitSettings(),
    llm=LLMSettings(),
    stt=STTSettings(),
    tts=TTSSettings(),
    sip=SIPSettings(),
)
