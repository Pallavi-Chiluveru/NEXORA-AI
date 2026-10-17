from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row


class PublicPostgresStore:
    """PostgreSQL conversation store used only by the public API."""

    def __init__(self, database_url):
        if not database_url:
            raise ValueError("DATABASE_URL is required for public history")
        self.database_url = database_url
        self._initialize()

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL
                );
                CREATE TABLE IF NOT EXISTS public_messages (
                    id BIGSERIAL PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES public_conversations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    input_type TEXT NOT NULL DEFAULT 'text',
                    created_at TIMESTAMPTZ NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_public_conversations_user_updated
                    ON public_conversations(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_public_messages_conversation
                    ON public_messages(conversation_id, created_at, id);
                """
            )

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def list_conversations(self, user_id, query=""):
        pattern = f"%{query.strip()}%"
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT DISTINCT c.id, c.title, c.created_at, c.updated_at
                FROM public_conversations c
                LEFT JOIN public_messages m ON m.conversation_id = c.id
                WHERE c.user_id = %s
                  AND (%s = '' OR c.title ILIKE %s OR m.content ILIKE %s)
                ORDER BY c.updated_at DESC
                """,
                (user_id, query.strip(), pattern, pattern),
            ).fetchall()

    def create_conversation(self, conversation_id, user_id, title="New conversation"):
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO public_conversations
                    (id, user_id, title, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (conversation_id, user_id, title.strip() or "New conversation", now, now),
            )
        return self.get_conversation(conversation_id, user_id)

    def get_conversation(self, conversation_id, user_id):
        with self._connect() as connection:
            conversation = connection.execute(
                """
                SELECT id, title, created_at, updated_at
                FROM public_conversations
                WHERE id = %s AND user_id = %s
                """,
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            messages = connection.execute(
                """
                SELECT id, role, content, input_type, created_at
                FROM public_messages
                WHERE conversation_id = %s AND user_id = %s
                ORDER BY created_at, id
                """,
                (conversation_id, user_id),
            ).fetchall()
        result = dict(conversation)
        result["messages"] = [dict(message) for message in messages]
        return result

    def add_message(self, conversation_id, user_id, role, content, input_type="text"):
        clean_content = content.strip()
        if role not in {"user", "assistant"} or not clean_content:
            raise ValueError("Message role and content are required")
        now = self._now()
        with self._connect() as connection:
            conversation = connection.execute(
                """
                SELECT title FROM public_conversations
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """,
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                return None
            title = conversation["title"]
            if role == "user" and title == "New conversation":
                title = " ".join(clean_content.split())[:60] or "New conversation"
            message = connection.execute(
                """
                INSERT INTO public_messages
                    (conversation_id, user_id, role, content, input_type, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, role, content, input_type, created_at
                """,
                (conversation_id, user_id, role, clean_content, input_type, now),
            ).fetchone()
            connection.execute(
                """
                UPDATE public_conversations
                SET title = %s, updated_at = %s
                WHERE id = %s AND user_id = %s
                """,
                (title, now, conversation_id, user_id),
            )
        result = self.get_conversation(conversation_id, user_id)
        result["last_message"] = dict(message)
        return result

    def rename(self, conversation_id, user_id, title):
        clean_title = " ".join(title.split()).strip()
        if not clean_title:
            raise ValueError("Conversation title cannot be empty")
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE public_conversations
                SET title = %s, updated_at = %s
                WHERE id = %s AND user_id = %s
                """,
                (clean_title[:120], self._now(), conversation_id, user_id),
            )
            if result.rowcount == 0:
                return None
        return self.get_conversation(conversation_id, user_id)

    def delete(self, conversation_id, user_id):
        with self._connect() as connection:
            result = connection.execute(
                """
                DELETE FROM public_conversations
                WHERE id = %s AND user_id = %s
                """,
                (conversation_id, user_id),
            )
        return result.rowcount > 0

    def clear(self, user_id):
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM public_conversations WHERE user_id = %s",
                (user_id,),
            )
