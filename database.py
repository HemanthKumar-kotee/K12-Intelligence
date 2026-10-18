
import os
import uuid
import logging

from contextlib import contextmanager
from dotenv import load_dotenv
from psycopg import errors
from psycopg.types.json import Jsonb
from psycopg.rows import dict_row
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


def open_pool():
    pool.open(wait=True)
    pool.wait()


def close_pool():
    pool.close()


@contextmanager
def get_connection():
    with pool.connection() as conn:
        yield conn


def init_db():
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id UUID PRIMARY KEY,
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
        CREATE INDEX IF NOT EXISTS idx_auth_sessions_user
        ON auth_sessions(user_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id UUID PRIMARY KEY,
            user_id UUID REFERENCES users(user_id)
                ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'New conversation',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
                REFERENCES conversations(conversation_id)
                ON DELETE CASCADE,
            role TEXT NOT NULL
                CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS username VARCHAR(100) NOT NULL
        """,
        """
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS events JSONB
        """,
        """
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS sources JSONB
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_conversations_user
        ON conversations(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_messages_conversation
        ON chat_messages(conversation_id, created_at, message_id)
        """,
    ]

    with get_connection() as conn:
        for statement in statements:
            conn.execute(statement)

    logger.info("Initializing database")


def handleSignUp(name: str, email: str, password: str):

    name = name.strip().lower()
    email = email.strip().lower()
    user_id = uuid.uuid4()
    hashed_password = password_hash.hash(password)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO users
                    (user_id, username, email, password_hash)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, name, email, hashed_password),
            )
        
        return {
            "user_id": str(user_id),
            "email": email,
        }

    except errors.UniqueViolation:
        return None


def handleLogin(email: str, password: str):
    email = email.strip().lower()

    with get_connection() as conn:
        user = conn.execute(
            """
            SELECT user_id, email, password_hash
            FROM users
            WHERE email = %s
            """,
            (email,),
        ).fetchone()

    if not user:
        return None

    if not password_hash.verify(
        password, user["password_hash"]
    ):
        return None

    return {
        "user_id": str(user["user_id"]),
        "email": user["email"],
    }



def create_session(user_id: str, token_hash: str, expires_at):
    
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions (session_id, user_id, token_hash, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (uuid.uuid4(), user_id, token_hash, expires_at),
        )


def get_user_by_session_hash(token_hash: str):
    
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT u.user_id, u.email
            FROM auth_sessions s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > NOW()
            """,
            (token_hash,),
        ).fetchone()
    if row:
        return {"user_id": str(row["user_id"]), "email": row["email"]}
    return None


def delete_session(token_hash: str):
    
    with get_connection() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash,))


def fetch_conversation_for_user(conversation_id: str, user_id: str):
    
    with get_connection() as conn:
    
        return conn.execute(
            """
            SELECT * FROM conversations
            WHERE conversation_id = %s AND user_id = %s
            """,
            (conversation_id, user_id),
        ).fetchone()


def delete_conversation_for_user(conversation_id: str, user_id: str):
    
    with get_connection() as conn:
    
        cursor = conn.execute(
            """
            DELETE FROM conversations
            WHERE conversation_id = %s AND user_id = %s
            """,
            (conversation_id, user_id),
        )
        return cursor.rowcount > 0


def create_conversation(
    conversation_id: str,
    user_id: str | None = None,
    title: str = "New conversation",
):
    with get_connection() as conn:
        
        conn.execute(
            """
            INSERT INTO conversations
                (conversation_id, user_id, title)
            VALUES (%s, %s, %s)
            """,
            (conversation_id, user_id, title),
        )


def fetch_conversations(user_id: str | None = None):
    
    with get_connection() as conn:
    
        if user_id is None:
            rows = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE user_id IS NULL
                ORDER BY updated_at DESC
                """
            ).fetchall()
        
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM conversations
                WHERE user_id = %s
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()

    return rows


def fetch_conversation(conversation_id: str):
    
    with get_connection() as conn:
    
        row = conn.execute(
            """
            SELECT *
            FROM conversations
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        ).fetchone()

    return row



def fetch_messages(conversation_id: str):
    
    with get_connection() as conn:
    
        rows = conn.execute(
            """
            SELECT *
            FROM chat_messages
            WHERE conversation_id = %s
            ORDER BY created_at ASC, message_id ASC
            """,
            (conversation_id,),
        ).fetchall()

    return rows


def insert_message(
    conversation_id: str,
    message_id: str,
    role: str,
    content: str,
    events=None,
    sources=None
):
    with get_connection() as conn:
        
        if role == 'assistant':
        
            conn.execute(
                        """
                        INSERT INTO chat_messages
                            (message_id, conversation_id, role, content, events, sources)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            message_id, 
                            conversation_id, 
                            role, 
                            content, 
                            Jsonb(events) if events is not None else None, 
                            Jsonb(sources) if sources is not None else None
                        ),
                    )
        else:
            conn.execute(
                """
                INSERT INTO chat_messages
                    (message_id, conversation_id, role, content)
                VALUES (%s, %s, %s, %s)
                """,
                (message_id, conversation_id, role, content),
            )

        conn.execute(
            """
            UPDATE conversations
            SET updated_at = NOW()
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        )


def delete_conversation(conversation_id: str):
    
    with get_connection() as conn:
    
        cursor = conn.execute(
            """
            DELETE FROM conversations
            WHERE conversation_id = %s
            """,
            (conversation_id,),
        )

        return cursor.rowcount > 0
