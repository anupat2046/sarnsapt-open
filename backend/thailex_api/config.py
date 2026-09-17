from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    sparql_endpoint: str = "http://localhost:7200/repositories/thailex"
    selector_mode: str = "heuristic"
    request_timeout_seconds: float = 30.0
    max_graph_edges: int = 50
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    thaillm_api_key: str | None = None
    thaillm_model: str = "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0"
    thaillm_base_url: str = "https://thaillm.or.th/api/v1"
    thaillm_max_tokens: int = 700
    thaillm_temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("THAILEX_SELECTOR_MODE", "heuristic").strip().lower()
        if mode not in {"heuristic", "openai", "thaillm"}:
            raise ValueError(
                "THAILEX_SELECTOR_MODE must be 'heuristic', 'openai', or 'thaillm'"
            )
        max_edges = int(os.getenv("THAILEX_MAX_GRAPH_EDGES", "50"))
        if not 1 <= max_edges <= 200:
            raise ValueError("THAILEX_MAX_GRAPH_EDGES must be between 1 and 200")
        request_timeout = float(os.getenv("THAILEX_REQUEST_TIMEOUT_SECONDS", "30"))
        if request_timeout <= 0:
            raise ValueError("THAILEX_REQUEST_TIMEOUT_SECONDS must be greater than 0")
        thaillm_max_tokens = int(os.getenv("THAILLM_MAX_TOKENS", "700"))
        if not 64 <= thaillm_max_tokens <= 4096:
            raise ValueError("THAILLM_MAX_TOKENS must be between 64 and 4096")
        thaillm_temperature = float(os.getenv("THAILLM_TEMPERATURE", "0.0"))
        if not 0 <= thaillm_temperature <= 2:
            raise ValueError("THAILLM_TEMPERATURE must be between 0 and 2")
        return cls(
            sparql_endpoint=os.getenv(
                "THAILEX_SPARQL_ENDPOINT",
                "http://localhost:7200/repositories/thailex",
            ).rstrip("/"),
            selector_mode=mode,
            request_timeout_seconds=request_timeout,
            max_graph_edges=max_edges,
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL") or None,
            openai_base_url=os.getenv(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            thaillm_api_key=os.getenv("THAILLM_API_KEY") or None,
            thaillm_model=os.getenv(
                "THAILLM_MODEL", "Pathumma-ThaiLLM-qwen3-8b-think-3.0.0"
            ).strip(),
            thaillm_base_url=os.getenv(
                "THAILLM_BASE_URL", "https://thaillm.or.th/api/v1"
            ).rstrip("/"),
            thaillm_max_tokens=thaillm_max_tokens,
            thaillm_temperature=thaillm_temperature,
        )

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    @property
    def thaillm_ready(self) -> bool:
        return bool(self.thaillm_api_key and self.thaillm_model)

    @property
    def llm_ready(self) -> bool:
        if self.selector_mode == "openai":
            return self.openai_ready
        if self.selector_mode == "thaillm":
            return self.thaillm_ready
        return False

    @property
    def llm_model(self) -> str | None:
        if self.selector_mode == "openai":
            return self.openai_model
        if self.selector_mode == "thaillm":
            return self.thaillm_model
        return None
