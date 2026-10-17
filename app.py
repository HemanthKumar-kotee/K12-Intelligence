
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import secrets
import uuid
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel, EmailStr, Field

from logging_config import setup_logging
from graph import build_graph, CHECKPOINTER_URL
from database import (
    open_pool,
    close_pool,
    init_db,
    handleSignUp,
    handleLogin,
    create_session,
    get_user_by_session_hash,
    delete_session,
    create_conversation,
    fetch_conversations,
    fetch_conversation_for_user,
    fetch_messages,
    insert_message,
    delete_conversation_for_user,
)

setup_logging()
logger = logging.getLogger(__name__)

FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:8080,http://localhost:5173",
    ).split(",")
    if origin.strip()
]

SESSION_COOKIE_NAME = "k12_session"
SESSION_DURATION = timedelta(days=7)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    try:
        # Initialize K12 tables before accepting requests

        init_db()
        with PostgresSaver.from_conn_string(CHECKPOINTER_URL) as checkpointer:
            checkpointer.setup()
            app.state.graph = build_graph(checkpointer)
            yield
    finally:
        close_pool()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


class CredentialRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class ChatRequest(BaseModel):
    thread_id: str
    message: str = Field(min_length=1, max_length=20000)



def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _set_session_cookie(response: Response, user_id: str) -> None:
    
    raw_token = secrets.token_urlsafe(32)
    
    create_session(
        user_id=user_id,
        token_hash=_token_hash(raw_token),
        expires_at=datetime.now(timezone.utc) + SESSION_DURATION,
    )
    
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(SESSION_DURATION.total_seconds()),
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


def require_same_origin(request: Request) -> None:
    
    """Reject cross-origin browser mutations when an Origin header is present."""
    
    origin = request.headers.get("origin")
    
    if origin and origin not in FRONTEND_ORIGINS:
        raise HTTPException(status_code=403, detail="Origin is not allowed.")


def get_current_user(request: Request):
    
    token = request.cookies.get(SESSION_COOKIE_NAME)
    
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    user = get_user_by_session_hash(_token_hash(token))
    
    if not user:
        raise HTTPException(status_code=401, detail="Session is invalid or expired.")
    
    return user


def create_conversation_title(message: str) -> str:
    
    cleaned = " ".join(message.split())
    
    if not cleaned:
        return "New conversation"
    
    max_length = 40
    return cleaned[: max_length - 1].rstrip() + "…" if len(cleaned) > max_length else cleaned


def ensure_owned_conversation(thread_id: str, message: str, user_id: str) -> None:
    
    try:
        uuid.UUID(thread_id)
    
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid conversation ID.")

    conversation = fetch_conversation_for_user(thread_id, user_id)
    if conversation is not None:
        return

    # Do not allow a user to adopt another user's or a legacy unowned conversation.
    from database import fetch_conversation
    existing = fetch_conversation(thread_id)
    if existing is not None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    create_conversation(
        conversation_id=thread_id,
        user_id=user_id,
        title=create_conversation_title(message),
    )


@app.get("/")
def home():
    return {"message": "Welcome to K12 agent"}


@app.post("/signup", status_code=201)
def signup(credentials: CredentialRequest, response: Response):
    
    user = handleSignUp(credentials.email, credentials.password)
    
    if user is None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )
    
    _set_session_cookie(response, user["user_id"])
    return {"message": "Successfully signed up", **user}


@app.post("/login")
def login(credentials: CredentialRequest, response: Response):
    
    user = handleLogin(credentials.email, credentials.password)
    
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    _set_session_cookie(response, user["user_id"])
    return {"message": "Login successful", **user}


@app.get("/auth/me")
def auth_me(user=Depends(get_current_user)):
    return user


@app.post("/auth/logout")
def logout(request: Request, response: Response, user=Depends(get_current_user)):
    
    require_same_origin(request)
    
    token = request.cookies.get(SESSION_COOKIE_NAME)
    
    if token:
        delete_session(_token_hash(token))
    _clear_session_cookie(response)
    
    return {"message": "Logged out successfully"}


@app.post("/chat")
def chat(chat_request: ChatRequest, request: Request, user=Depends(get_current_user)):
    
    require_same_origin(request)
    
    thread_id = chat_request.thread_id
    message = chat_request.message.strip()
    
    if not message:
    
        raise HTTPException(status_code=422, detail="Message cannot be empty.")

    ensure_owned_conversation(thread_id, message, user["user_id"])
    logger.info("Chat request received for an authenticated user")

    try:
        result = app.state.graph.invoke(
            {"query": message},
            config={"configurable": {"thread_id": thread_id}},
        )
    
    except Exception:
        
        logger.exception("Exception occurred while handling chat request")
        raise HTTPException(
            status_code=500,
            detail="The K12 agent failed to process the message.",
        )

    events = result.get("events", [])
    search_results = result.get("search_results", [])
    answer = result.get("response")

    try:
        insert_message(
            conversation_id=thread_id,
            message_id=str(uuid4()),
            role="user",
            content=message,
        )
        insert_message(
            conversation_id=thread_id,
            message_id=str(uuid4()),
            role="assistant",
            content=answer or "",
            events=events,
            sources=search_results,
        )

    except Exception:
    
        logger.exception("Failed to persist chat messages")
        raise HTTPException(
            status_code=500,
            detail="The response was generated but could not be saved.",
        )

    return {
        "thread_id": thread_id,
        "query": result.get("query", message),
        "response": answer,
        "search_results": search_results,
        "events": events,
    }


@app.get("/get-conversations")
def get_conversations(user=Depends(get_current_user)):
    return fetch_conversations(user["user_id"])


@app.get("/get-conversation/{conversation_id}")
def get_conversation(conversation_id: str, user=Depends(get_current_user)):
    
    conversation = fetch_conversation_for_user(conversation_id, user["user_id"])
    
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    return {**conversation, "messages": fetch_messages(conversation_id)}


@app.delete("/get-conversation/{conversation_id}")
def remove_conversation(
    conversation_id: str,
    request: Request,
    user=Depends(get_current_user),
):

    require_same_origin(request)

    deleted = delete_conversation_for_user(conversation_id, user["user_id"])

    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    return {
        "message": "Conversation deleted successfully",
        "conversation_id": conversation_id,
    }
