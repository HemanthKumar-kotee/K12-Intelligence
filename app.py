
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import hashlib
import logging
import os
import secrets
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel, EmailStr, Field, field_validator
from pwdlib import PasswordHash

from logging_config import setup_logging
from graph import build_graph, CHECKPOINTER_URL
from database import (
    open_pool,
    close_pool,
    init_db,
    handleLogin,
    create_session,
    get_user_by_session_hash,
    delete_session,
    create_conversation,
    fetch_conversations,
    fetch_conversation,
    fetch_conversation_for_user,
    fetch_messages,
    insert_message,
    delete_conversation_for_user,
    insert_pending_signup,
    user_exists,
    complete_pending_signup,
)
from otp import generate_otp, hash_otp, send_otp_email

setup_logging()
logger = logging.getLogger(__name__)


def _configured_origins() -> list[str]:
    
    origins = os.getenv("FRONTEND_ORIGIN", "http://localhost:8080")
    return list(dict.fromkeys(origins))


FRONTEND_ORIGINS = _configured_origins()
SESSION_COOKIE_NAME = "k12_session"
SESSION_DURATION = timedelta(days=7)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes"}
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none" if COOKIE_SECURE else "lax").strip().lower()

if COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError("COOKIE_SAMESITE must be lax, strict, or none")

if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")

password_hasher = PasswordHash.recommended()


@asynccontextmanager
async def lifespan(app: FastAPI):
    open_pool()
    try:
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


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name cannot be blank")
        return normalized


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str = Field(pattern=r"^\d{6}$")


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
        samesite=COOKIE_SAMESITE,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAMESITE,
    )


def require_same_origin(request: Request) -> None:
    
    """Reject browser mutations from an unapproved Origin, if one is supplied."""
    
    origin = request.headers.get("origin")
    
    if origin and origin.rstrip("/") not in FRONTEND_ORIGINS:
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
    
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid conversation ID.") from exc

    if fetch_conversation_for_user(thread_id, user_id) is not None:
        return
    
    if fetch_conversation(thread_id) is not None:
        # Prevent adopting another user's or a legacy unowned conversation.
        raise HTTPException(status_code=404, detail="Conversation not found.")
    
    create_conversation(
        conversation_id=thread_id,
        user_id=user_id,
        title=create_conversation_title(message),
    )


@app.get("/")
def home():
    return {"message": "Welcome to K12 agent"}


@app.post("/signup", status_code=status.HTTP_202_ACCEPTED)
@app.post("/signup/request-otp", status_code=status.HTTP_202_ACCEPTED)
def request_otp(signup: SignupRequest, request: Request):
    
    """Start signup and email an OTP. No user/session is created at this stage."""
    
    require_same_origin(request)
    email = str(signup.email).strip().lower()
    name = signup.name.strip()

    if user_exists(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    otp = generate_otp()
    encoded_password = password_hasher.hash(signup.password)
    otp_digest = hash_otp(email, otp)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    try:
        insert_pending_signup(email, name, encoded_password, otp_digest, expires_at)
    
    except Exception as exc:
        logger.exception("Could not store pending signup")
        raise HTTPException(status_code=500, detail="Unable to process signup request.") from exc

    try:
        send_otp_email(email, otp)
    
    except Exception as exc:
        # Keep the pending row so a subsequent request can replace it with a new code.
        logger.exception("Signup verification email delivery failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to send verification email. Please retry signup.",
        ) from exc

    logger.info("Signup verification code issued")
    return {"message": "Verification code sent. It expires in 5 minutes.", "email": email}


@app.post("/signup/verify-otp", status_code=status.HTTP_201_CREATED)
def verify_signup_otp(payload: OTPVerifyRequest, request: Request, response: Response):
    
    """Verify the email OTP and atomically create the user account."""
    
    require_same_origin(request)
    email = str(payload.email).strip().lower()

    try:
        submitted_digest = hash_otp(email, payload.otp)
        result = complete_pending_signup(email, submitted_digest, max_attempts=5)
    
    except Exception as exc:
    
        logger.exception("Signup OTP verification failed unexpectedly")
        raise HTTPException(status_code=500, detail="Unable to verify signup at this time.") from exc

    result_status = result.get("status")
    
    if result_status == "not_found":
        raise HTTPException(status_code=400, detail="No pending signup found. Request a new verification code.")
    
    if result_status == "expired":
        raise HTTPException(status_code=400, detail="Verification code expired. Request a new code.")
    
    if result_status == "invalid":
        raise HTTPException(
            status_code=400,
            detail=f"Invalid verification code. Attempts remaining: {result.get('attempts_remaining', 0)}.",
        )
    
    if result_status == "too_many_attempts":
        raise HTTPException(status_code=429, detail="Too many incorrect codes. Please restart signup.")
    
    if result_status == "already_registered":
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    
    if result_status != "verified" or not result.get("user"):
        logger.error("OTP verification returned an unexpected status")
        raise HTTPException(status_code=500, detail="Unable to complete signup.")

    user = result["user"]
    _set_session_cookie(response, user["user_id"])
    logger.info("Email verified and signup completed")
    
    return {"message": "Email verified and account created successfully.", **user}


@app.post("/login")
def login(credentials: LoginRequest, response: Response, request: Request):
    
    require_same_origin(request)
    
    user = handleLogin(str(credentials.email), credentials.password)
    
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
    logger.info("User logged out successfully")
    return {"message": "Logged out successfully"}


@app.post("/chat")
def chat(chat_request: ChatRequest, request: Request, user=Depends(get_current_user)):
    
    require_same_origin(request)
    thread_id = chat_request.thread_id
    message = chat_request.message
    
    ensure_owned_conversation(thread_id, message, user["user_id"])
    logger.info("Chat request received for an authenticated user")

    try:
        result = app.state.graph.invoke(
            {"query": message},
            config={"configurable": {"thread_id": thread_id}},
        )
    
    except Exception as exc:
        logger.exception("Exception occurred while handling chat request")
        raise HTTPException(status_code=500, detail="The K12 agent failed to process the message.") from exc

    events = result.get("events", [])
    search_results = result.get("search_results", [])
    answer = result.get("response")
    
    try:
        insert_message(
            thread_id, 
            str(uuid4()), 
            "user", 
            message
        )

        insert_message(
            thread_id, 
            str(uuid4()), 
            "assistant", 
            answer or "", 
            events=events, 
            sources=search_results
        )
    
    except Exception as exc:
        
        logger.exception("Failed to persist chat messages")
        raise HTTPException(status_code=500, detail="The response was generated but could not be saved.") from exc

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
def remove_conversation(conversation_id: str, request: Request, user=Depends(get_current_user)):
    
    require_same_origin(request)
    
    deleted = delete_conversation_for_user(conversation_id, user["user_id"])
    
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return {"message": "Conversation deleted successfully", "conversation_id": conversation_id}
