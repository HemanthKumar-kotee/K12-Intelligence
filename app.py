from contextlib import asynccontextmanager
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field

import logging 
import uuid

from logging_config import setup_logging
from graph import build_graph, CHECKPOINTER_URL
from database import (
    open_pool,
    close_pool,
    init_db,
    handleSignUp,
    handleLogin,
    create_conversation,
    fetch_conversations,
    fetch_conversation,
    fetch_messages,
    insert_message,
    delete_conversation,
)


setup_logging()
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):

    open_pool()

    try:
        with PostgresSaver.from_conn_string(CHECKPOINTER_URL) as checkpointer:
        
            checkpointer.setup()
            app.state.graph = build_graph(checkpointer)
        
            init_db()
            yield

    finally:
        close_pool()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CredentialRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class ChatRequest(BaseModel):
    thread_id: str
    message: str = Field(min_length=1, max_length=20000)


def createConversationTitle(message: str) -> str:
    cleaned = " ".join(message.split())

    if not cleaned:
        return "New conversation"

    max_length = 40

    return (
        cleaned[: max_length - 1].rstrip() + "…"
        if len(cleaned) > max_length
        else cleaned
    )

def ensure_conversation(thread_id: str, message: str):
    conversation = fetch_conversation(thread_id)

    if conversation is None:
        create_conversation(
            conversation_id=thread_id,
            title=createConversationTitle(message),
        )


@app.get("/")
def home():
    return {"message": "K12 agent"}


@app.post("/signup", status_code=201)
def signup(credentials: CredentialRequest):
    user = handleSignUp(
        credentials.email,
        credentials.password,
    )

    if user is None:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists.",
        )

    return {
        "message": "Successfully signed up",
        **user,
    }


@app.post("/login")
def login(credentials: CredentialRequest):
    user = handleLogin(
        credentials.email,
        credentials.password,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    return {
        "message": "Login successful",
        **user,
    }


@app.post("/chat")
def chat(chat_request: ChatRequest):

    logger.info("Chat request received")        
    thread_id = chat_request.thread_id
    message = chat_request.message.strip()

    if thread_id is None:
        thread_id = uuid.uuid4()
        
    if not message:
        raise HTTPException(
            status_code=422,
            detail="Message cannot be empty.",
        )

    ensure_conversation(thread_id, message)

    graph = app.state.graph
    try:
        result = graph.invoke(
            {"query": message},
            config={
                "configurable": {
                    "thread_id": thread_id,
                }
            },
        )

    except Exception:

        logger.exception("Exception occured while, handling chat request")
        raise HTTPException(
            status_code=500,
            detail="The K12 agent failed to process the message.",
        )

    events = result.get("events", [])
    search_results = result.get("search_results", [])

    answer = result.get("response")

    # Persist conversation turn after the graph succeeds
    user_message_id = str(uuid4())
    assistant_message_id = str(uuid4())

    try:
        insert_message(
            conversation_id=thread_id,
            message_id=user_message_id,
            role="user",
            content=message,
        )

        insert_message(
            conversation_id=thread_id,
            message_id=assistant_message_id,
            role="assistant",
            content=answer,
            events=events,
            sources=search_results
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
def get_conversations():
    return fetch_conversations()


@app.get("/get-conversation/{conversation_id}")
def get_conversation(conversation_id: str):
    conversation = fetch_conversation(conversation_id)

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        **conversation,
        "messages": fetch_messages(conversation_id),
    }


@app.delete("/get-conversation/{conversation_id}")
def remove_conversation(conversation_id: str):
    deleted = delete_conversation(conversation_id)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found.",
        )

    return {
        "message": "Conversation deleted successfully",
        "conversation_id": conversation_id,
    }