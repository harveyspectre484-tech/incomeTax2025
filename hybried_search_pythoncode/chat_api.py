"""Local HTTP API for the Income-tax Act legal research chat."""

from __future__ import annotations

import logging
import os
import threading
import time
import asyncio
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    from .discover_dependencies import DependencyDiscoverer
    from .expand_rerank import LegalGraph
    from .grounded_answer import OpenAIDraftGenerator
    from .live_legal_retrieve import run_question
except ImportError:
    from discover_dependencies import DependencyDiscoverer
    from expand_rerank import LegalGraph
    from grounded_answer import OpenAIDraftGenerator
    from live_legal_retrieve import run_question


ROOT = Path(__file__).resolve().parent.parent
GRAPH_DIR = ROOT / "graph_data"
LOGGER = logging.getLogger(__name__)


class ConfigurationError(RuntimeError):
    pass


class SearchFilters(BaseModel):
    chapter: str | None = None
    section: str | None = None
    unit_type: str | None = None
    jurisdiction: str | None = None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    filters: SearchFilters = Field(default_factory=SearchFilters)


class ChatRuntime:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._retriever: Any = None
        self._trace_client: Any = None

    @property
    def tracing_enabled(self) -> bool:
        return os.environ.get("LANGSMITH_TRACING", "").lower() == "true"

    def missing_configuration(self) -> list[str]:
        missing = [name for name in ("OPENAI_API_KEY", "OPENAI_MODEL") if not os.environ.get(name)]
        if self.tracing_enabled and not os.environ.get("LANGSMITH_API_KEY"):
            missing.append("LANGSMITH_API_KEY")
        return missing

    def health(self) -> dict[str, Any]:
        missing = self.missing_configuration()
        return {
            "status": "configured" if not missing else "setup_required",
            "model_loaded": self._ready,
            "missing_configuration": missing,
            "tracing_enabled": self.tracing_enabled,
        }

    def _load(self) -> None:
        missing = self.missing_configuration()
        if missing:
            raise ConfigurationError("Set " + ", ".join(missing) + " before asking a question")
        if self._ready:
            return

        from hybrid_retrieve import HybridRetriever
        from sentence_transformers import CrossEncoder
        import torch

        graph = LegalGraph(GRAPH_DIR / "graph_nodes.jsonl", GRAPH_DIR / "graph_edges.jsonl")
        retriever = HybridRetriever(
            db_path=GRAPH_DIR / "legal_act_bm25.sqlite",
            qdrant_url=os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
            collection=os.environ.get("QDRANT_COLLECTION", "income_tax_2025_units_v3"),
        )
        try:
            device = os.environ.get("RERANKER_DEVICE", "cpu")
            if device not in {"cpu", "mps"}:
                raise ConfigurationError("RERANKER_DEVICE must be cpu or mps")
            reranker = CrossEncoder(
                os.environ.get("RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B"),
                device=device,
                model_kwargs={"torch_dtype": torch.float16 if device == "mps" else torch.float32},
            )
            generator = OpenAIDraftGenerator(
                os.environ["OPENAI_MODEL"], trace_openai=self.tracing_enabled,
            )
            trace_factory = None
            trace_client = None
            if self.tracing_enabled:
                from langsmith import Client, trace
                trace_client = Client()
                project = os.environ.get("LANGSMITH_PROJECT", "income-tax-2025-rag")

                def trace_factory(name: str, run_type: str, inputs: dict[str, Any]):
                    return trace(name, run_type=run_type, inputs=inputs,
                                 project_name=project, client=trace_client)

            self._graph = graph
            self._discoverer = DependencyDiscoverer(graph)
            self._retriever = retriever
            self._reranker = reranker
            self._generator = generator
            self._trace_client = trace_client
            self._trace_factory = trace_factory
            self._ready = True
        except Exception:
            retriever.close()
            raise

    def ask(self, request: ChatRequest) -> dict[str, Any]:
        with self._lock:
            self._load()
            started = time.monotonic()
            result = run_question(
                request.question.strip(), self._retriever, self._graph,
                self._discoverer, self._reranker,
                answer_generator=self._generator,
                trace_factory=self._trace_factory,
                filters=request.filters.model_dump(exclude_none=True),
            )
            if self._trace_client is not None:
                self._trace_client.flush()
            grounded = result["grounded_answer"]
            return {
                "question": result["question"],
                "status": grounded["status"],
                "answer": grounded["answer"],
                "claims": grounded["claims"],
                "missing_evidence": grounded["missing_evidence"],
                "attempts": grounded.get("attempts", 0),
                "sources": [
                    {key: unit.get(key) for key in (
                        "id", "citation", "unit_type", "text", "table_columns",
                        "parent_id", "inclusion_reason", "origin_id", "source_file",
                    )}
                    for unit in result["final_evidence"]["evidence_units"]
                ],
                "retrieval": {
                    "candidate_ids": result["hybrid_candidate_ids"],
                    "ranked_bundles": result["ranked_bundles"],
                    "omitted_bundles": result["omitted_bundles"],
                    "omitted_units": result["final_evidence"]["omitted_units"],
                },
                "tracing_enabled": self.tracing_enabled,
                "elapsed_ms": round((time.monotonic() - started) * 1000),
            }

    def close(self) -> None:
        with self._lock:
            if self._retriever is not None:
                self._retriever.close()
            if self._trace_client is not None:
                self._trace_client.flush()
            self._ready = False


def create_app(runtime: ChatRuntime | None = None) -> FastAPI:
    runtime = runtime or ChatRuntime()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="legal-chat")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await asyncio.get_running_loop().run_in_executor(executor, runtime.close)
            executor.shutdown(wait=True)

    app = FastAPI(title="Income-tax Act 2025 chat API", lifespan=lifespan)

    @app.get("/")
    @app.get("/chat.html")
    async def chat_page() -> FileResponse:
        return FileResponse(ROOT / "chat.html", media_type="text/html")

    @app.get("/chat.css")
    async def chat_styles() -> FileResponse:
        return FileResponse(ROOT / "chat.css", media_type="text/css")

    @app.get("/chat.js")
    async def chat_script() -> FileResponse:
        return FileResponse(ROOT / "chat.js", media_type="text/javascript")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return runtime.health()

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> dict[str, Any]:
        try:
            return await asyncio.get_running_loop().run_in_executor(executor, runtime.ask, request)
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            LOGGER.exception("Chat request failed")
            raise HTTPException(
                status_code=503,
                detail="Retrieval or answer generation is unavailable. Check the API and Qdrant logs.",
            ) from exc

    return app


app = create_app()
