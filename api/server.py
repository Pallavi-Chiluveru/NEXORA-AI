import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import main
from services.conversation_store import ConversationStore


APP_DIR = Path(main.APP_DIR)
store = ConversationStore(APP_DIR / "jarvis_history.sqlite3")
app = FastAPI(title="NEXORA AI Local API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

state = {
    "status": "idle",
    "message": "Click to Start Listening.",
    "microphone": "not_checked",
    "last_transcript": "",
    "last_response": "",
    "voice_running": False,
    "voice_conversation_id": None,
}
activity = []
subscribers = set()
voice_lock = threading.Lock()
command_lock = threading.Lock()
voice_stopper = None
voice_session_id = None


class TrackedMicrophone(main.sr.Microphone):
    """Report the result of the stream opened by the background listener."""

    def __init__(self, ready_event):
        super().__init__()
        self.ready_event = ready_event
        self.stream_available = False

    def __enter__(self):
        source = super().__enter__()
        self.stream_available = self.stream is not None
        self.ready_event.set()
        return source


def microphone_failure_status(error):
    message = str(error).lower()
    if "permission" in message or "access" in message or "denied" in message:
        return "permission_denied"
    if "device" in message or "input" in message or "no such" in message:
        return "device_unavailable"
    return "error"


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1, max_length=4000)
    input_type: str = Field(default="text", pattern="^(text|voice)$")


class ConversationRequest(BaseModel):
    title: str = Field(default="New conversation", max_length=120)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ActionRequest(BaseModel):
    command: str = Field(min_length=1, max_length=1000)


class VoiceStartRequest(BaseModel):
    conversation_id: str | None = None


def emit(event_type, **payload):
    event = {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    activity.insert(0, event)
    del activity[10:]
    for key in ("status", "microphone", "last_transcript", "last_response"):
        if key in payload:
            state[key] = payload[key]
    if isinstance(payload.get("message"), str):
        state["message"] = payload["message"]
    for subscriber in tuple(subscribers):
        try:
            subscriber.put_nowait(event)
        except asyncio.QueueFull:
            pass


def conversation_or_404(conversation_id):
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def run_command(command, conversation_id, input_type, correlation_id=None):
    started_at = time.perf_counter()
    emit(
        "processing_started",
        status="processing",
        message="Processing your request...",
        correlation_id=correlation_id,
    )
    user_conversation = store.add_message(
        conversation_id, "user", command, input_type
    )
    user_message = user_conversation["last_message"]
    emit(
        "message_saved",
        correlation_id=correlation_id,
        conversation_id=conversation_id,
        message_id=user_message["id"],
        saved_message=user_message,
    )
    responses = []
    with command_lock:
        original_respond = main.respond
        main.respond = lambda text: responses.append(text)
        try:
            stopped = main.execute_command(command) is False
        finally:
            main.respond = original_respond
    answer = responses[-1] if responses else (
        "Goodbye. Have a great day." if stopped else "Your command was processed."
    )
    if responses:
        print(f"NEXORA AI: {answer}")
    assistant_conversation = store.add_message(
        conversation_id, "assistant", answer, "text"
    )
    assistant_message = assistant_conversation["last_message"]
    emit(
        "speech_started",
        status="speaking",
        message="NEXORA AI is responding...",
        correlation_id=correlation_id,
        conversation_id=conversation_id,
        message_id=assistant_message["id"],
        user_message=user_message,
        assistant_message=assistant_message,
    )
    speech_started = time.perf_counter()
    main.speak(answer)
    print(
        f"[{correlation_id or 'command'}] TTS completed in "
        f"{(time.perf_counter() - speech_started) * 1000:.0f} ms"
    )
    emit(
        "response_received",
        status="idle",
        message="Click to Start Listening.",
        response=answer,
        last_response=answer,
        correlation_id=correlation_id,
        elapsed_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return conversation_or_404(conversation_id)


@app.get("/api/health")
def health():
    return {"ok": True, "service": "jarvis"}


@app.get("/api/status")
def status():
    return {
        **state,
        "groq": "configured" if main.groq_client is not None else "unavailable",
        "speech_engine": "ready",
    }


@app.get("/api/activity")
def get_activity():
    return activity


@app.get("/api/conversations")
def list_conversations(search: str = ""):
    return store.list_conversations(search)


@app.post("/api/conversations")
def create_conversation(request: ConversationRequest):
    return store.create_conversation(str(uuid.uuid4()), request.title)


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    return conversation_or_404(conversation_id)


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, request: RenameRequest):
    result = store.rename(conversation_id, request.title)
    if result is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return result


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    if not store.delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations")
def clear_conversations():
    store.clear()
    return {"ok": True}


@app.post("/api/chat")
def chat(request: ChatRequest):
    conversation_or_404(request.conversation_id)
    started_at = time.perf_counter()
    emit("processing_started", status="processing", message="Processing your request...")
    store.add_message(
        request.conversation_id, "user", request.message, request.input_type
    )
    answer = main.ask_ai(request.message)
    store.add_message(request.conversation_id, "assistant", answer)
    main.speak(answer)
    emit(
        "response_received",
        status="idle",
        message="Click to Start Listening.",
        response=answer,
        last_response=answer,
        elapsed_ms=round((time.perf_counter() - started_at) * 1000),
    )
    return {"response": answer, "conversation": conversation_or_404(request.conversation_id)}


@app.post("/api/actions/command")
def action_command(request: ActionRequest):
    conversation = store.create_conversation(str(uuid.uuid4()))
    return run_command(request.command, conversation["id"], "text")


@app.post("/api/voice/start")
def start_voice(request: VoiceStartRequest = VoiceStartRequest()):
    global voice_stopper, voice_session_id
    with voice_lock:
        if state["voice_running"]:
            return {"ok": False, "message": "Voice capture is already running."}
        if request.conversation_id:
            if store.get_conversation(request.conversation_id) is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            conversation_id = request.conversation_id
        else:
            conversation_id = store.create_conversation(str(uuid.uuid4()))["id"]
        state["voice_running"] = True
        state["voice_conversation_id"] = conversation_id
        voice_session_id = str(uuid.uuid4())
        session_id = voice_session_id
    emit(
        "listening_starting",
        status="starting",
        message="Starting microphone...",
        microphone="checking",
        correlation_id=session_id,
        conversation_id=conversation_id,
    )
    try:
        ready_event = threading.Event()
        microphone = TrackedMicrophone(ready_event)
        voice_stopper = main.recognizer.listen_in_background(
            microphone,
            lambda recognizer, audio: voice_callback(session_id, recognizer, audio),
            phrase_time_limit=10,
        )
        if not ready_event.wait(timeout=2):
            voice_stopper(wait_for_stop=True)
            raise RuntimeError("Microphone initialization timed out")
        if not microphone.stream_available:
            voice_stopper(wait_for_stop=True)
            raise RuntimeError("Microphone device could not be opened")
        emit(
            "microphone_available",
            status="starting",
            message="Microphone ready.",
            microphone="available",
            correlation_id=session_id,
            conversation_id=conversation_id,
        )
        emit(
            "listening_started",
            status="listening",
            message="Listening... Speak now...",
            microphone="listening",
            correlation_id=session_id,
            conversation_id=conversation_id,
        )
        return {
            "ok": True,
            "correlation_id": session_id,
            "conversation_id": conversation_id,
        }
    except Exception as error:
        with voice_lock:
            state["voice_running"] = False
            state["voice_conversation_id"] = None
            microphone_status = microphone_failure_status(error)
            print(f"Voice start error ({type(error).__name__}): {error}")
            emit(
                "error",
                status="error",
                message="The microphone could not be started.",
                microphone=microphone_status,
            )
            return {
                "ok": False,
                "message": "The microphone could not be started.",
                "microphone": microphone_status,
            }


def voice_callback(session_id, recognizer, audio):
    global voice_stopper, voice_session_id
    started_at = time.perf_counter()
    try:
        with voice_lock:
            if not state["voice_running"] or voice_session_id != session_id:
                return
            conversation_id = state["voice_conversation_id"]
            stopper = voice_stopper
            voice_stopper = None
            state["voice_running"] = False
        if stopper is not None:
            stopper(wait_for_stop=False)
        emit(
            "speech_detected",
            status="processing",
            message="Speech detected. Processing...",
            microphone="available",
            correlation_id=session_id,
            conversation_id=conversation_id,
        )
        command = main.transcribe_audio(audio)
        if not command:
            emit(
                "error",
                status="idle",
                message="I couldn't hear anything. Please try again.",
                correlation_id=session_id,
            )
            return
        emit(
            "transcript_received",
            transcript=command,
            last_transcript=command,
            correlation_id=session_id,
            conversation_id=conversation_id,
            transcription_ms=round((time.perf_counter() - started_at) * 1000),
        )
        print(
            f"[{session_id}] transcription completed in "
            f"{(time.perf_counter() - started_at) * 1000:.0f} ms"
        )
        run_command(command, conversation_id, "voice", session_id)
    except Exception as error:
        print(f"Voice API error ({type(error).__name__}): {error}")
        emit(
            "error",
            status="error",
            message="The microphone could not be used.",
            microphone=microphone_failure_status(error),
            correlation_id=session_id,
        )
    finally:
        with voice_lock:
            voice_session_id = None
            state["voice_conversation_id"] = None
        if state["status"] not in {"error", "idle"}:
            emit(
                "listening_finished",
                status="idle",
                message="Click to Start Listening.",
                correlation_id=session_id,
            )


@app.post("/api/voice/stop")
def stop_voice():
    global voice_stopper, voice_session_id
    with voice_lock:
        if not state["voice_running"]:
            return {"ok": True, "message": "Listening is already stopped."}
        state["voice_running"] = False
        session_id = voice_session_id
        voice_session_id = None
        stopper = voice_stopper
        voice_stopper = None
    if stopper is not None:
        emit(
            "listening_stopping",
            status="stopping",
            message="Stopping listening...",
            correlation_id=session_id,
        )
        stopper(wait_for_stop=False)
    emit(
        "listening_stopped",
        status="idle",
        message="Listening stopped.",
        microphone="available",
        correlation_id=session_id,
    )
    return {"ok": True, "message": "Listening stopped."}


@app.websocket("/api/events")
async def events(websocket: WebSocket):
    await websocket.accept()
    queue = asyncio.Queue(maxsize=20)
    subscribers.add(queue)
    try:
        while True:
            await websocket.send_json(await queue.get())
    except WebSocketDisconnect:
        pass
    finally:
        subscribers.discard(queue)
