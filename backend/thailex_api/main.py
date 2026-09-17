from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .models import (
    AlignmentsResponse,
    AskRequest,
    AskResponse,
    HealthResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    ReviewQueueResponse,
    SearchResponse,
    SenseDetailResponse,
    SensesResponse,
)
from .query_templates import QueryTemplates
from .repository import GraphRepository
from .retrieval import CandidateRetriever
from .selector import (
    HeuristicSenseSelector,
    OpenAIResponsesSenseSelector,
    ThaiLLMChatSenseSelector,
)
from .services import AskService, AskServiceError
from .sparql import SparqlClient, SparqlError


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    repository: GraphRepository
    ask_service: AskService
    llm_selector: OpenAIResponsesSenseSelector | ThaiLLMChatSenseSelector | None = None

    async def close(self) -> None:
        if self.llm_selector is not None:
            await self.llm_selector.close()
        await self.repository.close()


def build_container(settings: Settings) -> AppContainer:
    repository = GraphRepository(
        SparqlClient(
            settings.sparql_endpoint,
            timeout=settings.request_timeout_seconds,
        ),
        QueryTemplates(),
    )
    heuristic = HeuristicSenseSelector()
    llm_selector: OpenAIResponsesSenseSelector | ThaiLLMChatSenseSelector | None = None
    if settings.selector_mode == "openai" and settings.openai_ready:
        llm_selector = OpenAIResponsesSenseSelector(
            api_key=settings.openai_api_key or "",
            model=settings.openai_model or "",
            base_url=settings.openai_base_url,
            timeout=settings.request_timeout_seconds,
        )
    elif settings.selector_mode == "thaillm" and settings.thaillm_ready:
        llm_selector = ThaiLLMChatSenseSelector(
            api_key=settings.thaillm_api_key or "",
            model=settings.thaillm_model,
            base_url=settings.thaillm_base_url,
            timeout=settings.request_timeout_seconds,
            max_tokens=settings.thaillm_max_tokens,
            temperature=settings.thaillm_temperature,
        )
    ask_service = AskService(
        repository=repository,
        retriever=CandidateRetriever(repository),
        heuristic_selector=heuristic,
        llm_selector=llm_selector,
        default_to_llm=settings.selector_mode in {"openai", "thaillm"},
        max_graph_edges=settings.max_graph_edges,
        provider=settings.selector_mode,
        model=settings.llm_model,
    )
    return AppContainer(
        settings=settings,
        repository=repository,
        ask_service=ask_service,
        llm_selector=llm_selector,
    )


def create_app(
    settings: Settings | None = None, container: AppContainer | None = None
) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    resolved_container = container or build_container(resolved_settings)
    owns_container = container is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = resolved_container
        yield
        if owns_container:
            await resolved_container.close()

    app = FastAPI(
        title="ThaiLex GraphRAG API",
        version="0.1.0",
        description=(
            "Evidence-grounded Thai lexical search and 1–2 hop graph retrieval. "
            "ThaiLLM/OpenAI selection is optional and cannot add graph facts."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.exception_handler(SparqlError)
    async def sparql_error_handler(_: Request, exc: SparqlError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "GraphDB is unavailable or rejected the query",
                "error": str(exc),
            },
        )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> HealthResponse:
        current: AppContainer = request.app.state.container
        if not await current.repository.health():
            raise HTTPException(status_code=503, detail="GraphDB repository is empty")
        return HealthResponse(
            status="ok",
            graphdb="reachable",
            repository=current.settings.sparql_endpoint,
            selector_mode=current.settings.selector_mode,
            llm_configured=current.settings.llm_ready,
            llm_provider=(
                current.settings.selector_mode
                if current.settings.selector_mode in {"openai", "thaillm"}
                else None
            ),
            llm_model=current.settings.llm_model,
        )

    @app.get("/api/search", response_model=SearchResponse, tags=["lexicon"])
    async def search(
        request: Request,
        q: str = Query(min_length=1, max_length=100),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> SearchResponse:
        current: AppContainer = request.app.state.container
        return SearchResponse(
            query=q,
            results=await current.repository.search(q, limit=limit),
        )

    @app.get("/api/senses", response_model=SensesResponse, tags=["lexicon"])
    async def senses(
        request: Request,
        lemma: str = Query(min_length=1, max_length=100),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> SensesResponse:
        current: AppContainer = request.app.state.container
        return SensesResponse(
            lemma=lemma,
            candidates=await current.repository.get_senses(lemma, limit=limit),
        )

    @app.get("/api/sense", response_model=SenseDetailResponse, tags=["lexicon"])
    @app.get("/api/sense/details", response_model=SenseDetailResponse, tags=["lexicon"])
    async def sense_detail(
        request: Request,
        uri: str = Query(min_length=8, max_length=500),
        hops: int = Query(default=1, ge=1, le=2),
    ) -> SenseDetailResponse:
        current: AppContainer = request.app.state.container
        candidate = await current.repository.get_sense(uri)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Sense not found")
        graph = await current.repository.semantic_graph(
            candidate.sense_uri,
            max_edges=current.settings.max_graph_edges,
        )
        details = await current.repository.get_sense_details(
            candidate, semantic_graph=graph
        )
        return SenseDetailResponse(candidate=candidate, details=details, graph=graph)

    @app.exception_handler(AskServiceError)
    async def ask_service_error_handler(_: Request, exc: AskServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.message,
                "code": exc.code,
                "retryable": exc.retryable,
                "fallback_used": False,
            },
        )

    @app.get("/api/alignments", response_model=AlignmentsResponse, tags=["alignment"])
    async def alignments(
        request: Request,
        lemma: str | None = Query(default=None, min_length=1, max_length=100),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> AlignmentsResponse:
        current: AppContainer = request.app.state.container
        return AlignmentsResponse(
            lemma=lemma,
            candidates=await current.repository.get_alignments(lemma, limit=limit),
        )

    @app.get("/api/review/candidates", response_model=ReviewQueueResponse, tags=["review"])
    async def review_candidates(
        request: Request,
        lemma: str | None = Query(default=None, min_length=1, max_length=100),
        limit: int = Query(default=40, ge=1, le=100),
    ) -> ReviewQueueResponse:
        current: AppContainer = request.app.state.container
        return ReviewQueueResponse(
            lemma=lemma,
            candidates=await current.repository.get_review_queue(lemma, limit=limit),
        )

    @app.post("/api/review/decisions", response_model=ReviewDecisionResponse, tags=["review"])
    async def save_review_decision(
        payload: ReviewDecisionRequest, request: Request
    ) -> ReviewDecisionResponse:
        current: AppContainer = request.app.state.container
        try:
            candidate = await current.repository.save_review_decision(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if candidate is None:
            raise HTTPException(status_code=404, detail="Alignment candidate not found")
        return ReviewDecisionResponse(candidate=candidate)

    @app.post("/api/ask", response_model=AskResponse, tags=["graphrag"])
    async def ask(payload: AskRequest, request: Request) -> AskResponse:
        current: AppContainer = request.app.state.container
        return await current.ask_service.ask(payload)

    return app


app = create_app()
