
import logging
import os
import uuid
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg import errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pwdlib import PasswordHash

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")

password_hash = PasswordHash.recommended()
pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=1,
    max_size=10,
    open=False,
    kwargs={"row_factory": dict_row},
)


def open_pool() -> None:
    pool.open(wait=True)
    pool.wait()


def close_pool() -> None:
    pool.close()


@contextmanager
def get_connection():
    """Yield a pooled connection; psycopg commits on successful context exit."""
    with pool.connection() as conn:
        yield conn


def init_db() -> None:

    """Create tables and apply backward-compatible column migrations."""
    
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id UUID PRIMARY KEY,
            username VARCHAR(100) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            session_id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            token_hash CHAR(64) NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id UUID PRIMARY KEY,
            user_id UUID REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            events JSONB,
            sources JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pending_signup (
            email VARCHAR(255) PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            password_hash TEXT NOT NULL,
            otp_hash CHAR(64) NOT NULL,
            otp_expires_at TIMESTAMPTZ NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON chat_messages(conversation_id, created_at, message_id)",
        
        # Migrations for databases created by earlier versions.
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(100)",
        "UPDATE users SET username = email WHERE username IS NULL OR BTRIM(username) = ''",
        "ALTER TABLE users ALTER COLUMN username SET NOT NULL",
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS events JSONB",
        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS sources JSONB",
        "ALTER TABLE pending_signup ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE pending_signup ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
    ]
    with get_connection() as conn:
        for statement in statements:
            conn.execute(statement)
    logger.info("Database schema initialized")


def handleSignUp(name: str, email: str, password: str):
    
    """Legacy direct-signup helper; signup API should use OTP verification instead."""
    normalized_name = name.strip()
    normalized_email = email.strip().lower()
    user_id = uuid.uuid4()
    hashed_password = password_hash.hash(password)
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO users (user_id, username, email, password_hash) VALUES (%s, %s, %s, %s)",
                (user_id, normalized_name, normalized_email, hashed_password),
            )
        return {"user_id": str(user_id), "email": normalized_email}
    except errors.UniqueViolation:
        return None


def handleLogin(email: str, password: str):
    normalized_email = email.strip().lower()
    with get_connection() as conn:
        user = conn.execute(
            "SELECT user_id, email, password_hash FROM users WHERE email = %s",
            (normalized_email,),
        ).fetchone()
    if not user or not password_hash.verify(password, user["password_hash"]):
        return None
    return {"user_id": str(user["user_id"]), "email": user["email"]}


def user_exists(email: str) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM users WHERE email = %s LIMIT 1",
            (email.strip().lower(),),
        ).fetchone() is not None


def insert_pending_signup(email: str, name: str, encoded_password: str, otp_hash: str, expires_at) -> None:
    
    """Insert or replace a pending signup (e.g. when a code is resent)."""
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pending_signup (email, name, password_hash, otp_hash, otp_expires_at, attempts)
            VALUES (%s, %s, %s, %s, %s, 0)
            ON CONFLICT (email) DO UPDATE SET
                name = EXCLUDED.name,
                password_hash = EXCLUDED.password_hash,
                otp_hash = EXCLUDED.otp_hash,
                otp_expires_at = EXCLUDED.otp_expires_at,
                attempts = 0,
                created_at = NOW()
            """,
            (email.strip().lower(), name.strip(), encoded_password, otp_hash, expires_at),
        )


def complete_pending_signup(email: str, supplied_otp_hash: str, max_attempts: int = 5) -> dict:
    
    """Atomically verify OTP, create user, and delete pending signup.

    Returns a status dictionary. Failed-attempt increments and expiry cleanup are
    committed before the caller translates the status into an HTTP response.
    """
    normalized_email = email.strip().lower()
    status = {"status": "invalid"}
    try:
        with get_connection() as conn:
            pending = conn.execute(
                "SELECT * FROM pending_signup WHERE email = %s FOR UPDATE",
                (normalized_email,),
            ).fetchone()

            if pending is None:
                return {"status": "not_found"}

            if pending["otp_expires_at"] is None:
                conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                return {"status": "expired"}

            # PostgreSQL's clock is authoritative and avoids app/database clock drift.
            still_valid = conn.execute(
                "SELECT %s > NOW() AS valid",
                (pending["otp_expires_at"],),
            ).fetchone()["valid"]
            if not still_valid:
                conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                return {"status": "expired"}

            if pending["attempts"] >= max_attempts:
                conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                return {"status": "too_many_attempts"}

            # Digest comparison is performed by the API with hmac.compare_digest;
            # compare here too in constant time to keep the DB operation self-contained.
            import hmac
            if not hmac.compare_digest(str(pending["otp_hash"]), supplied_otp_hash):
                attempts = pending["attempts"] + 1
                if attempts >= max_attempts:
                    conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                    status = {"status": "too_many_attempts"}
                else:
                    conn.execute(
                        "UPDATE pending_signup SET attempts = %s WHERE email = %s",
                        (attempts, normalized_email),
                    )
                    status = {"status": "invalid", "attempts_remaining": max_attempts - attempts}
            else:
                existing = conn.execute(
                    "SELECT 1 FROM users WHERE email = %s",
                    (normalized_email,),
                ).fetchone()
                if existing:
                    conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                    status = {"status": "already_registered"}
                else:
                    user_id = uuid.uuid4()
                    conn.execute(
                        "INSERT INTO users (user_id, username, email, password_hash) VALUES (%s, %s, %s, %s)",
                        (user_id, pending["name"], normalized_email, pending["password_hash"]),
                    )
                    conn.execute("DELETE FROM pending_signup WHERE email = %s", (normalized_email,))
                    status = {"status": "verified", "user": {"user_id": str(user_id), "email": normalized_email}}
        return status
    except errors.UniqueViolation:
        # Another request may have registered this email concurrently.
        return {"status": "already_registered"}


def create_session(user_id: str, token_hash: str, expires_at) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (session_id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)",
            (uuid.uuid4(), user_id, token_hash, expires_at),
        )


def get_user_by_session_hash(token_hash: str):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.user_id, u.email FROM auth_sessions s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > NOW()
            """,
            (token_hash,),
        ).fetchone()
    if row:
        return {"user_id": str(row["user_id"]), "email": row["email"]}
    return None


def delete_session(token_hash: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))


def fetch_conversation_for_user(conversation_id: str, user_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = %s AND user_id = %s",
            (conversation_id, user_id),
        ).fetchone()


def delete_conversation_for_user(conversation_id: str, user_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE conversation_id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        return cursor.rowcount > 0


def create_conversation(conversation_id: str, user_id: str | None = None, title: str = "New conversation") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversations (conversation_id, user_id, title) VALUES (%s, %s, %s)",
            (conversation_id, user_id, title),
        )


def fetch_conversations(user_id: str | None = None):
    with get_connection() as conn:
        if user_id is None:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE user_id IS NULL ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE user_id = %s ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
    return rows


def fetch_conversation(conversation_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = %s",
            (conversation_id,),
        ).fetchone()


def fetch_messages(conversation_id: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM chat_messages WHERE conversation_id = %s ORDER BY created_at ASC, message_id ASC",
            (conversation_id,),
        ).fetchall()


def insert_message(conversation_id: str, message_id: str, role: str, content: str, events=None, sources=None) -> None:
    with get_connection() as conn:
        if role == "assistant":
            conn.execute(
                """
                INSERT INTO chat_messages (message_id, conversation_id, role, content, events, sources)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (message_id, conversation_id, role, content,
                 Jsonb(events) if events is not None else None,
                 Jsonb(sources) if sources is not None else None),
            )
        else:
            conn.execute(
                "INSERT INTO chat_messages (message_id, conversation_id, role, content) VALUES (%s, %s, %s, %s)",
                (message_id, conversation_id, role, content),
            )
        conn.execute("UPDATE conversations SET updated_at = NOW() WHERE conversation_id = %s", (conversation_id,))


def delete_conversation(conversation_id: str) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM conversations WHERE conversation_id = %s", (conversation_id,))
        return cursor.rowcount > 0
