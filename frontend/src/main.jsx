import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createClient } from "@supabase/supabase-js";
import {
  Activity, AppWindow, ArrowLeft, Bot, Calculator, ChevronDown, Clock3,
  FileText, FolderOpen, Globe2, Github, History, Home, Mail, Menu, MessageSquare,
  Mic, Moon, MoreHorizontal, Palette, Paintbrush, Plus, RefreshCw, Search,
  Send, Settings, Sparkles, Sun, Terminal, Trash2, Youtube, Zap
} from "lucide-react";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8765/api";
const EVENTS = API.replace(/^http/, "ws") + "/events";
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
const supabase = supabaseUrl && supabaseAnonKey ? createClient(supabaseUrl, supabaseAnonKey) : null;
let publicAccessToken = null;
const navItems = [
  ["home", "Home", Home], ["chat", "Chat", MessageSquare],
  ["voice", "Voice Control", Mic], ["apps", "Applications", AppWindow],
  ["browser", "Browser", Globe2], ["history", "Chat History", History],
  ["settings", "Settings", Settings],
];
const shortcuts = [
  ["Google", "Open Google", Globe2, "website"], ["YouTube", "Open YouTube", Youtube, "website"],
  ["Gmail", "Open Gmail", Mail, "website"], ["GitHub", "Open GitHub", Github, "website"],
  ["Notepad", "Open Notepad", FileText, "app"], ["Calculator", "Open Calculator", Calculator, "app"],
  ["Paint", "Open Paint", Paintbrush, "app"], ["File Explorer", "Open File Explorer", FolderOpen, "app"],
];

function microphoneLabel(value) {
  return {
    not_checked: "Not checked",
    checking: "Checking",
    available: "Available",
    listening: "Listening",
    permission_denied: "Permission denied",
    device_unavailable: "Device unavailable",
    error: "Error",
  }[value] || "Not checked";
}

async function request(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeout || 15000);
  delete options.timeout;
  const response = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(publicAccessToken ? { Authorization: `Bearer ${publicAccessToken}` } : {}),
      ...(options.headers || {}),
    }, ...options,
    signal: controller.signal,
  }).finally(() => clearTimeout(timeout));
  if (!response.ok) throw new Error((await response.json()).detail || "Backend request failed");
  return response.json();
}

function App() {
  const [session, setSession] = useState(null);
  const [page, setPage] = useState("home");
  const [theme, setTheme] = useState(() => localStorage.getItem("nexora-theme") || "dark");
  const [status, setStatus] = useState({ status: "idle", message: "Click to Start Listening.", groq: "unknown", microphone: "not_checked", speech_engine: "unknown" });
  const [conversations, setConversations] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [historySearch, setHistorySearch] = useState("");
  const [activity, setActivity] = useState([]);
  const [toast, setToast] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  useEffect(() => {
    if (!supabase) return undefined;
    supabase.auth.getSession().then(({ data }) => {
      publicAccessToken = data.session?.access_token || null;
      setSession(data.session);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      publicAccessToken = nextSession?.access_token || null;
      setSession(nextSession);
    });
    return () => listener.subscription.unsubscribe();
  }, []);

  const loadConversations = async (search = historySearch) => {
    try { setConversations(await request(`/conversations?search=${encodeURIComponent(search)}`)); }
    catch (error) { setToast(error.message); }
  };
  const selectConversation = async (id) => {
    try { const conversation = await request(`/conversations/${id}`); setSelected(id); setMessages(conversation.messages); setPage("chat"); }
    catch (error) { setToast(error.message); }
  };
  const newChat = async () => {
    try { const conversation = await request("/conversations", { method: "POST", body: JSON.stringify({}) }); setSelected(conversation.id); setMessages([]); setPage("chat"); loadConversations(); }
    catch (error) { setToast(error.message); }
  };
  const runAction = async (command) => {
    try { await request("/actions/command", { method: "POST", body: JSON.stringify({ command }) }); setToast(`${command} completed.`); refreshActivity(); }
    catch (error) { setToast(error.message); }
  };
  const toggleVoice = useCallback(async () => {
    if (status.status === "listening") {
      setStatus((current) => ({ ...current, status: "stopping", message: "Stopping listening..." }));
      try { await request("/voice/stop", { method: "POST", timeout: 5000 }); }
      catch (error) { setToast(error.name === "AbortError" ? "Stopping listening timed out." : error.message); }
      return;
    }
    if (["starting", "processing", "speaking", "stopping"].includes(status.status)) return;
    setStatus((current) => ({ ...current, status: "starting", message: "Starting microphone..." }));
    try {
      let conversationId = selected;
      if (!conversationId) {
        const conversation = await request("/conversations", {
          method: "POST", body: JSON.stringify({}), timeout: 5000,
        });
        conversationId = conversation.id;
        setSelected(conversationId);
        setMessages([]);
      }
      const result = await request("/voice/start", {
        method: "POST",
        body: JSON.stringify({ conversation_id: conversationId }),
        timeout: 5000,
      });
      if (!result.ok) {
        setStatus((current) => ({ ...current, microphone: result.microphone || "error" }));
        throw new Error(result.message);
      }
    }
    catch (error) {
      const message = error.name === "AbortError" ? "Microphone startup timed out." : error.message;
      setStatus((current) => ({ ...current, status: "error", message, microphone: current.microphone === "checking" ? "error" : current.microphone }));
      setToast(message);
      setTimeout(() => setStatus((current) => current.status === "error" ? { ...current, status: "idle", message: "Click to Start Listening." } : current), 2500);
    }
  }, [selected, status.status]);
  const sendMessage = async (message) => {
    if (!message.trim()) return;
    let id = selected;
    if (!id) { const conversation = await request("/conversations", { method: "POST", body: JSON.stringify({}) }); id = conversation.id; setSelected(id); }
    setMessages((current) => [...current, { role: "user", content: message, created_at: new Date().toISOString() }]);
    try {
      const result = await request("/chat", { method: "POST", body: JSON.stringify({ conversation_id: id, message }) });
      setMessages(result.conversation.messages); loadConversations();
    } catch (error) { setToast(error.message); }
  };
  const refreshActivity = async () => { try { setActivity(await request("/activity")); } catch { /* status card shows backend errors */ } };

  useEffect(() => {
    if (supabase && !session) return undefined;
    localStorage.setItem("nexora-theme", theme);
    request("/status").then(setStatus).catch(() => setStatus((current) => ({ ...current, status: "error", message: "Backend unavailable" })));
    loadConversations(); refreshActivity();
    const socket = new WebSocket(EVENTS);
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setStatus((current) => ({ ...current, ...data, status: data.status || current.status, message: data.message || current.message }));
      setActivity((current) => [data, ...current].slice(0, 10));
      if (data.conversation_id && data.conversation_id === selectedRef.current) {
        const incoming = data.saved_message || data.user_message || data.assistant_message;
        if (incoming && incoming.id) {
          setMessages((current) => current.some((item) => item.id === incoming.id)
            ? current
            : [...current, incoming]);
        }
      }
      if (data.type === "listening_stopped" || data.type === "error") {
        setTimeout(() => setStatus((current) => (
          current.status === (data.type === "error" ? "error" : "idle")
            ? { ...current, status: "idle", message: "Click to Start Listening." }
            : current
        )), data.type === "error" ? 2500 : 1400);
      }
    };
    return () => socket.close();
  }, [session]);
  useEffect(() => {
    if (supabase && !session) return undefined;
    const timer = setTimeout(() => loadConversations(historySearch), 250);
    return () => clearTimeout(timer);
  }, [historySearch, session]);
  useEffect(() => {
    const handler = (event) => {
      const target = document.activeElement;
      const editing = target && (
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
        || target.isContentEditable
      );
      if (event.code === "Space" && !editing) {
        event.preventDefault();
        toggleVoice();
      }
      if (event.code === "Escape" && !editing && status.status === "listening") {
        event.preventDefault();
        toggleVoice();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [status.status, toggleVoice]);

  const nav = (target) => { setPage(target); setMobileOpen(false); };
  if (supabase && !session) return <AuthScreen />;
  return <div className={`app ${theme}`}><Sidebar theme={theme} page={page} nav={nav} conversations={conversations} search={historySearch} setSearch={setHistorySearch} selected={selected} select={selectConversation} newChat={newChat} mobileOpen={mobileOpen} status={status} />
    <main className="workspace">
      <header className="topbar"><button className="icon-button mobile-menu" onClick={() => setMobileOpen(true)}><Menu size={18} /></button><div className={`status-dot ${status.status}`} /><span>{status.message}</span><div className="top-actions"><button className="icon-button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Toggle theme">{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button><span className="live-label">LOCAL / {status.status.toUpperCase()}</span></div></header>
      <div className="content">{page === "home" && <HomePage status={status} activity={activity} runAction={runAction} toggleVoice={toggleVoice} nav={nav} />}
        {page === "chat" && <ChatPage messages={messages} send={sendMessage} />}
        {page === "voice" && <VoicePage status={status} toggleVoice={toggleVoice} />}
        {page === "apps" && <AppsPage runAction={runAction} />}
        {page === "browser" && <BrowserPage runAction={runAction} />}
        {page === "history" && <HistoryPage conversations={conversations} select={selectConversation} search={historySearch} setSearch={setHistorySearch} refresh={loadConversations} setToast={setToast} />}
        {page === "settings" && <SettingsPage theme={theme} setTheme={setTheme} status={status} setToast={setToast} />}
      </div>
    </main>
    {toast && <button className="toast" onClick={() => setToast("")}>{toast}</button>}
  </div>;
}

function AuthScreen() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState("sign_in");
  const [message, setMessage] = useState("");
  const submit = async (event) => {
    event.preventDefault();
    setMessage("");
    const result = mode === "sign_in"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password });
    if (result.error) setMessage(result.error.message);
    else if (mode === "sign_up") setMessage("Check your email to confirm your account.");
  };
  return <div className="auth-screen"><form className="auth-card" onSubmit={submit}><span className="eyebrow">NEXORA AI</span><h1>{mode === "sign_in" ? "Sign in to your history" : "Create your account"}</h1><p>Your public conversations are private to your account.</p><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="Email address" required /><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Password" minLength={6} required /><button className="primary-button" type="submit">{mode === "sign_in" ? "Sign in" : "Create account"}</button>{message && <p className="auth-message">{message}</p>}<button className="text-button" type="button" onClick={() => setMode(mode === "sign_in" ? "sign_up" : "sign_in")}>{mode === "sign_in" ? "Need an account?" : "Already have an account?"}</button></form></div>;
}

function Sidebar({ theme, page, nav, conversations, search, setSearch, selected, select, newChat, mobileOpen, status }) {
  const brandImage = theme === "light" ? "/lightmode.png" : "/darkmode.png";
  return <aside className={`sidebar ${mobileOpen ? "open" : ""}`}><div className="brand"><img src={brandImage} alt="NEXORA AI — Your Intelligent Companion" className="brand-art" /></div>
    <div className="nav">{navItems.map(([id, label, Icon]) => <button key={id} className={page === id ? "active" : ""} onClick={() => nav(id)}><Icon size={17} />{label}</button>)}</div>
    <div className="history-head"><span>CHAT HISTORY</span><button className="icon-button" onClick={newChat} title="New chat"><Plus size={16} /></button></div>
    <button className="new-chat" onClick={newChat}><Plus size={16} /> New Chat</button>
    <div className="history-search"><Search size={14} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search chats" /></div>
    <div className="history-list">{conversations.length ? conversations.slice(0, 8).map((conversation) => <button className={`history-item ${selected === conversation.id ? "selected" : ""}`} onClick={() => select(conversation.id)} key={conversation.id}><MessageSquare size={14} /><span>{conversation.title}</span></button>) : <p className="empty-history">Your conversations will appear here.</p>}</div>
    <div className="system-card"><div className="system-title"><span className={`status-dot ${status.status}`} /> System status</div><StatusRow label="NEXORA" value={status.status === "error" ? "Attention" : "Online"} /><StatusRow label="Microphone" value={microphoneLabel(status.microphone)} /><StatusRow label="Groq AI" value={status.groq || "Unknown"} /><StatusRow label="Speech engine" value={status.speech_engine || "Unknown"} /></div>
  </aside>;
}
function StatusRow({ label, value }) { return <div className="status-row"><span>{label}</span><b>{value}</b></div>; }
function voiceCopy(status) {
  return {
    idle: ["Click to Start Listening.", "Spacebar: Start / Stop Listening"],
    starting: ["Starting microphone...", "Please wait..."],
    listening: ["Listening... Speak now...", "Click Stop Listening or press the spacebar"],
    processing: ["Processing your request...", "Thinking about your request..."],
    speaking: ["NEXORA AI is responding...", "NEXORA AI is speaking..."],
    stopping: ["Stopping listening...", "Releasing the microphone..."],
    error: [status.message || "Microphone unavailable", "Try again when the microphone is ready"],
  }[status.status] || ["Click to Start Listening.", "Spacebar: Start / Stop Listening"];
}
function Orb({ status = "idle", onClick }) { return <div className={`orb-wrap ${status}`}><div className="wave wave-left" /><button className="orb" onClick={onClick} aria-label={status === "listening" ? "Stop listening" : "Start listening"}><Mic size={42} /></button><div className="wave wave-right" /></div>; }
function HomePage({ status, activity, runAction, toggleVoice, nav }) { const active = status.status === "listening"; const [title, subtitle] = voiceCopy(status); return <><PageIntro eyebrow="COMMAND CENTER" title="Good to see you, Commander." description="Your intelligent desktop companion is ready for your next instruction." /><section className={`hero-panel voice-state-${status.status}`}><Orb status={status.status} onClick={toggleVoice} /><h2>{title}</h2><p>{subtitle}</p>{status.status === "processing" && <span className="loading-indicator" aria-label="Processing" />}{active && <button className="stop-button" onClick={toggleVoice}>Stop Listening</button>}</section><section className="section-heading"><div><span className="eyebrow">SHORTCUTS</span><h3>Quick Actions</h3></div><button className="text-button" onClick={() => nav("apps")}>View all <ArrowLeft size={14} className="flip" /></button></section><ShortcutGrid runAction={runAction} /><ActivityList activity={activity} /></>; }
function PageIntro({ eyebrow, title, description }) { return <div className="page-intro"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>; }
function ShortcutGrid({ runAction }) { return <div className="shortcut-grid">{shortcuts.map(([label, command, Icon]) => <button className="shortcut" key={label} onClick={() => runAction(command)}><span className="shortcut-icon"><Icon size={19} /></span><span>{label}</span><Zap size={13} className="shortcut-arrow" /></button>)}</div>; }
function ActivityList({ activity }) { return <section className="activity-panel"><div className="section-heading"><div><span className="eyebrow">LIVE FEED</span><h3>Recent Activity</h3></div><Activity size={18} /></div>{activity.length ? activity.slice(0, 5).map((item, index) => <div className="activity-row" key={`${item.timestamp}-${index}`}><span className="activity-icon"><Activity size={14} /></span><span>{item.message || item.type.replaceAll("_", " ")}</span><time>{new Date(item.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time></div>) : <p className="muted">Actions performed by NEXORA AI will appear here.</p>}</section>; }
function ChatPage({ messages, send }) { const [input, setInput] = useState(""); return <><PageIntro eyebrow="CONVERSATION" title="Chat with NEXORA AI" description="Ask a question or give a command. Text messages use the same local AI pipeline." /><section className="chat-panel"><div className="messages">{messages.length ? messages.map((message, index) => <div className={`message ${message.role}`} key={message.id || index}><span className="message-avatar">{message.role === "user" ? "YOU" : "N"}</span><div><small>{message.role === "user" ? "You" : "NEXORA AI"}</small><p>{message.content}</p></div></div>) : <div className="chat-empty"><Bot size={36} /><h3>Start a conversation</h3><p>Your messages will be saved locally in SQLite.</p></div>}</div><form className="chat-input" onSubmit={(event) => { event.preventDefault(); send(input); setInput(""); }}><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Ask NEXORA AI anything..." /><button className="primary-button"><Send size={16} /></button></form></section></>; }
function VoicePage({ status, toggleVoice }) { const active = status.status === "listening"; const [title, subtitle] = voiceCopy(status); return <><PageIntro eyebrow="VOICE CONTROL" title="Speak naturally." description="The Python engine owns microphone capture, transcription, command execution, and speech synthesis." /><section className={`voice-panel voice-state-${status.status}`}><Orb status={status.status} onClick={toggleVoice} /><h2>{title}</h2><p className="muted">{subtitle}</p>{status.status === "processing" && <span className="loading-indicator" aria-label="Processing" />}{active && <button className="stop-button" onClick={toggleVoice}>Stop Listening</button>}<div className="transcript"><span>Latest transcript</span><strong>{status.last_transcript || "Waiting for a voice command..."}</strong></div></section></>; }
function AppsPage({ runAction }) { return <><PageIntro eyebrow="APPLICATIONS" title="Desktop control" description="Only allowlisted Windows applications can be launched." /><ShortcutGrid runAction={runAction} /></>; }
function BrowserPage({ runAction }) { const [query, setQuery] = useState(""); return <><PageIntro eyebrow="BROWSER CONTROL" title="Navigate the web." description="Search and navigation actions are routed through the safe Python browser layer." /><div className="browser-tools"><form onSubmit={(event) => { event.preventDefault(); runAction(`Search Google for ${query}`); }}><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search Google..." /><button className="primary-button">Search</button></form><form onSubmit={(event) => { event.preventDefault(); runAction(`Search YouTube for ${query}`); }}><Youtube size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search YouTube..." /><button className="primary-button">Search</button></form></div><ShortcutGrid runAction={runAction} /></>; }
function HistoryPage({ conversations, select, search, setSearch, refresh, setToast }) { const remove = async (id) => { if (!window.confirm("Delete this conversation?")) return; try { await request(`/conversations/${id}`, { method: "DELETE" }); refresh(); } catch (error) { setToast(error.message); } }; return <><PageIntro eyebrow="ARCHIVE" title="Chat History" description="Your conversations are stored locally and never sent to a remote history service." /><div className="history-page-toolbar"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search titles and messages" /></div><div className="history-cards">{conversations.map((conversation) => <article className="history-card" key={conversation.id}><MessageSquare size={18} /><div onClick={() => select(conversation.id)}><h3>{conversation.title}</h3><p>{new Date(conversation.updated_at).toLocaleString()}</p></div><button className="icon-button danger" onClick={() => remove(conversation.id)}><Trash2 size={16} /></button></article>)}</div></>; }
function SettingsPage({ theme, setTheme, status, setToast }) { const clear = async () => { if (!window.confirm("Clear all locally saved conversations?")) return; try { await request("/conversations", { method: "DELETE" }); setToast("Conversation history cleared."); } catch (error) { setToast(error.message); } }; return <><PageIntro eyebrow="SYSTEM" title="Settings" description="Tune the local dashboard without exposing credentials." /><div className="settings-grid"><article className="setting-card"><Palette size={20} /><h3>Appearance</h3><p>Choose the interface theme.</p><button className="setting-choice" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Moon size={16} /> : <Sun size={16} />} {theme === "dark" ? "Dark mode" : "Light mode"}</button></article><article className="setting-card"><Mic size={20} /><h3>Audio services</h3><StatusRow label="Microphone" value={status.microphone} /><StatusRow label="Speech engine" value={status.speech_engine} /><p className="muted">Voice capture remains owned by the Python assistant.</p></article><article className="setting-card"><History size={20} /><h3>Chat history</h3><p>Delete local conversations and their messages.</p><button className="danger-button" onClick={clear}><Trash2 size={15} /> Clear all history</button></article></div></>; }

createRoot(document.getElementById("root")).render(<App />);
