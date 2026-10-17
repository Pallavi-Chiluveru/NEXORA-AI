"""Authenticated, text-only API for the NEXORA AI public demo.

This module intentionally does not import main.py. It cannot access microphones,
Windows processes, browser automation, local SQLite history, or text-to-speech.
"""

import json
import os
import urllib.error
import urllib.request
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel, Field

from services.public_postgres_store import PublicPostgresStore


app = FastAPI(title="NEXORA AI Public Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("PUBLIC_FRONTEND_ORIGINS", "").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
client = (
    Groq(api_key=os.environ["GROQ_API_KEY"], timeout=20, max_retries=0)
    if os.getenv("GROQ_API_KEY")
    else None
)
store = PublicPostgresStore(os.getenv("DATABASE_URL")) if os.getenv("DATABASE_URL") else None


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ConversationRequest(BaseModel):
    title: str = Field(default="New conversation", max_length=120)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


def require_store():
    if store is None:
        raise HTTPException(status_code=503, detail="Public history is not configured")
    return store


def current_user(request: Request):
    authorization = request.headers.get("authorization", "")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Authentication is required")
    token = authorization[7:].strip()
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_ANON_KEY", "")
    if not token or not supabase_url or not supabase_key:
        raise HTTPException(status_code=503, detail="Public authentication is not configured")
    request_data = urllib.request.Request(
        f"{supabase_url}/auth/v1/user",
        headers={"apikey": supabase_key, "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request_data, timeout=5) as response:
            user = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
        if isinstance(error, urllib.error.HTTPError) and error.code in {400, 401, 403}:
            raise HTTPException(status_code=401, detail="Authentication is invalid") from error
        raise HTTPException(status_code=503, detail="Authentication service is unavailable") from error
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication is invalid")
    return user_id


def conversation_or_404(repository, conversation_id, user_id):
    conversation = repository.get_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.get("/api/health")
def health():
    return {"ok": True, "service": "nexora-public-demo", "history": store is not None}


@app.get("/api/status")
def status():
    return {"status": "idle", "message": "Public text chat ready.", "microphone": "unavailable"}


@app.get("/api/conversations")
def list_conversations(search: str = "", user_id=Depends(current_user), repository=Depends(require_store)):
    return repository.list_conversations(user_id, search)


@app.post("/api/conversations")
def create_conversation(request: ConversationRequest, user_id=Depends(current_user), repository=Depends(require_store)):
    return repository.create_conversation(str(uuid.uuid4()), user_id, request.title)


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user_id=Depends(current_user), repository=Depends(require_store)):
    return conversation_or_404(repository, conversation_id, user_id)


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, request: RenameRequest, user_id=Depends(current_user), repository=Depends(require_store)):
    return_value = repository.rename(conversation_id, user_id, request.title)
    if return_value is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return return_value


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user_id=Depends(current_user), repository=Depends(require_store)):
    if not repository.delete(conversation_id, user_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations")
def clear_conversations(user_id=Depends(current_user), repository=Depends(require_store)):
    repository.clear(user_id)
    return {"ok": True}


@app.post("/api/chat")
def chat(request: ChatRequest, user_id=Depends(current_user), repository=Depends(require_store)):
    if client is None:
        raise HTTPException(status_code=503, detail="Public AI service is not configured")
    conversation_id = request.conversation_id or str(uuid.uuid4())
    if request.conversation_id is None:
        repository.create_conversation(conversation_id, user_id)
    else:
        conversation_or_404(repository, conversation_id, user_id)
    repository.add_message(conversation_id, user_id, "user", request.message)
    conversation = repository.get_conversation(conversation_id, user_id)
    messages = [
        {"role": message["role"], "content": message["content"]}
        for message in conversation["messages"][-12:]
    ]
    try:
        completion = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            messages=messages,
            temperature=0.4,
            max_tokens=500,
        )
        answer = (completion.choices[0].message.content or "").strip()
    except Exception as error:
        raise HTTPException(status_code=502, detail="The public AI service is temporarily unavailable") from error
    if not answer:
        raise HTTPException(status_code=502, detail="The public AI service returned an empty response")
    repository.add_message(conversation_id, user_id, "assistant", answer)
    return {
        "conversation_id": conversation_id,
        "response": answer,
        "conversation": repository.get_conversation(conversation_id, user_id),
    }
