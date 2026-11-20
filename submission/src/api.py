"""FastAPI backend implementing the Delivery Analytics Agent API contract."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.agent import AgentServiceError, DeliveryAnalyticsAgent, OutcomeType
from src.auth import verify_bearer_token
from src.config import Settings, get_settings
from src.conversations import ConversationStore
from src.gold import NeonGoldClient

app = FastAPI(title="Delivery Analytics Agent API", version="1.0.0")


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)


class ConversationSummary(BaseModel):
    id: str
    title: str
    updated_at: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]


class MessageItem(BaseModel):
    role: str
    content: str
    created_at: str


class ConversationDetailResponse(BaseModel):
    id: str
    messages: list[MessageItem]


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class SendMessageResponse(BaseModel):
    conversation_id: str
    answer: str
    data_basis: str
    outcome: OutcomeType


def get_store(settings: Settings = Depends(get_settings)) -> ConversationStore:
    """Provide the SQLite conversation store."""
    return ConversationStore(settings.sqlite_path)


def get_agent(settings: Settings = Depends(get_settings)) -> DeliveryAnalyticsAgent:
    """Provide the analytics agent wired to read-only Neon Gold."""
    gold_client = NeonGoldClient(
        database_url=settings.gold_database_url,
        timeout_ms=settings.sql_timeout_ms,
        max_rows=settings.sql_max_rows,
        max_bytes=settings.sql_max_bytes,
    )
    return DeliveryAnalyticsAgent(settings=settings, gold_executor=gold_client)


@app.exception_handler(Exception)
async def safe_unhandled_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """Return a generic error without stack traces or internal details."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Terjadi kesalahan internal pada server."},
    )


@app.get("/")
def root() -> dict[str, str]:
    """Root endpoint providing basic API metadata."""
    return {
        "service": "Delivery Analytics Agent API",
        "status": "ok",
        "healthz": "/healthz",
        "docs": "/docs",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Return empty 204 response for browser favicon requests."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness endpoint."""
    return {"status": "ok"}


@app.get("/api/v1/conversations", response_model=ConversationListResponse)
def list_conversations(
    user_id: str = Depends(verify_bearer_token),
    store: ConversationStore = Depends(get_store),
) -> ConversationListResponse:
    """List conversations belonging strictly to the authenticated user."""
    items = store.list_conversations(owner_id=user_id)
    return ConversationListResponse(
        conversations=[ConversationSummary(**item) for item in items]
    )


@app.post(
    "/api/v1/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
def create_conversation(
    payload: CreateConversationRequest | None = None,
    user_id: str = Depends(verify_bearer_token),
    store: ConversationStore = Depends(get_store),
) -> ConversationSummary:
    """Create a new conversation owned by the authenticated user."""
    title = payload.title if payload and payload.title else "Percakapan Baru"
    created = store.create_conversation(owner_id=user_id, title=title)
    return ConversationSummary(**created)


@app.get("/api/v1/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(
    conversation_id: str,
    user_id: str = Depends(verify_bearer_token),
    store: ConversationStore = Depends(get_store),
) -> ConversationDetailResponse:
    """Read a conversation and its messages if owned by the authenticated user."""
    conv = store.get_conversation(conversation_id=conversation_id, owner_id=user_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Percakapan tidak ditemukan.",
        )
    return ConversationDetailResponse(
        id=conv["id"],
        messages=[MessageItem(**m) for m in conv["messages"]],
    )


@app.post("/api/v1/conversations/{conversation_id}/messages", response_model=SendMessageResponse)
def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    user_id: str = Depends(verify_bearer_token),
    store: ConversationStore = Depends(get_store),
    agent: DeliveryAnalyticsAgent = Depends(get_agent),
    settings: Settings = Depends(get_settings),
) -> SendMessageResponse:
    """Send a user message to an owned conversation and return the grounded agent reply."""
    content = payload.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pesan tidak boleh kosong.",
        )
    if len(content) > settings.max_message_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Pesan melebihi batas maksimal {settings.max_message_length} karakter.",
        )

    if not store.is_owner(conversation_id=conversation_id, owner_id=user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Percakapan tidak ditemukan.",
        )

    history = store.get_recent_messages(
        conversation_id=conversation_id,
        owner_id=user_id,
        limit=settings.max_history_messages,
    )

    try:
        result = agent.run(user_message=content, history=history)
    except AgentServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    store.add_message(
        conversation_id=conversation_id,
        owner_id=user_id,
        role="user",
        content=content,
    )
    store.add_message(
        conversation_id=conversation_id,
        owner_id=user_id,
        role="assistant",
        content=result.answer,
        data_basis=result.data_basis,
        outcome=result.outcome,
    )

    return SendMessageResponse(
        conversation_id=conversation_id,
        answer=result.answer,
        data_basis=result.data_basis,
        outcome=result.outcome,
    )
