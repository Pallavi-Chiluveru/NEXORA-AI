"""Safe text-only API for the NEXORA AI public demo.

This module intentionally does not import main.py. It cannot access microphones,
Windows processes, browser automation, local SQLite history, or text-to-speech.
"""

import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel, Field


app = FastAPI(title="NEXORA AI Public Demo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("PUBLIC_FRONTEND_ORIGINS", "").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
client = Groq(api_key=os.environ["GROQ_API_KEY"], timeout=20, max_retries=0) if os.getenv("GROQ_API_KEY") else None
conversations = {}


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "nexora-public-demo"}


@app.post("/api/chat")
def chat(request: ChatRequest):
    if client is None:
        raise HTTPException(status_code=503, detail="Public AI service is not configured")
    conversation_id = request.conversation_id or str(uuid.uuid4())
    messages = conversations.setdefault(conversation_id, [])
    messages.append({"role": "user", "content": request.message})
    try:
        completion = client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
            messages=messages[-12:],
            temperature=0.4,
            max_tokens=500,
        )
        answer = (completion.choices[0].message.content or "").strip()
    except Exception as error:
        messages.pop()
        raise HTTPException(status_code=502, detail="The public AI service is temporarily unavailable") from error
    if not answer:
        raise HTTPException(status_code=502, detail="The public AI service returned an empty response")
    messages.append({"role": "assistant", "content": answer})
    return {"conversation_id": conversation_id, "response": answer}
