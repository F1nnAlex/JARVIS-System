"""JARVIS's brain: a streaming conversation with Claude, with tools for
long-term memory, opening apps and websites, and live web search."""

from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime
from typing import Callable

import anthropic

from .apps import AppCatalog
from .config import Settings
from .memory import Memory

MAX_TOOL_ROUNDS = 8
MAX_SESSION_MESSAGES = 40

PERSONA = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), the personal AI assistant from the Iron Man films, now running as a desktop app on the user's own computer.

Personality: calm, composed, impeccably polite, with a dry British wit. You are loyal and genuinely helpful, and you occasionally offer a gently sardonic aside, but you never let humour get in the way of an answer.

Everything you write is converted to speech and spoken aloud, so:
- Answer in plain spoken English. Never use markdown, bullet points, numbered lists, headings, tables, code blocks, emoji or URLs.
- Keep replies short: usually one to three sentences. Go longer only when the user explicitly asks for detail, and even then speak in natural paragraphs.
- Write numbers, units and symbols the way a person would say them (for example "twenty-two degrees" or "five past nine").
- Lead with the answer. Do not repeat the question back.
- Speech recognition can mishear words. If a request seems garbled, make the most sensible interpretation, or briefly ask the user to repeat it.

You have a long-term memory. Earlier conversations with the user are included at the start of this conversation, older ones can be found with the search_memory tool, and you keep a list of important facts about the user. When the user tells you something worth knowing later (their preferences, people in their life, plans, projects, how they like things done), save it with the remember tool without making a fuss about it. When the user refers to something from the past that you can't see, search your memory before saying you don't know.

You can open applications installed on the user's computer with open_app, and websites with open_website. When the user asks you to open, launch or start something, do it, then confirm in a few words. If open_app reports several close matches, pick the obvious one or ask which they meant. You cannot type into apps, close them, or control the computer in other ways yet; say so briefly if asked.

When the user asks about current events or live information and the web_search tool is available, use it, then give a brief spoken summary without reading out sources or links."""

CLIENT_TOOLS = [
    {
        "name": "open_app",
        "description": "Launch an application installed on the user's computer, matched by name (for example 'Spotify', 'Chrome', 'Calculator', 'Word'). Returns what was opened, or close matches if the name was ambiguous.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The app's name as the user said it."}},
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_apps",
        "description": "List applications installed on the user's computer whose names match a search term. Use an empty string to list everything.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "open_website",
        "description": "Open a website in the user's default web browser.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full URL starting with https:// or http://"}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_memory",
        "description": "Search all previous conversations with the user for a topic, name or phrase. Returns matching lines with their dates.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Keywords to look for."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remember",
        "description": "Save an important, lasting fact about the user to long-term memory so you know it in every future conversation. Write it as a short standalone sentence.",
        "input_schema": {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
            "additionalProperties": False,
        },
    },
    {
        "name": "forget",
        "description": "Delete a saved fact from long-term memory by its number, when the user asks you to forget it or it is no longer true.",
        "input_schema": {
            "type": "object",
            "properties": {"fact_id": {"type": "integer"}},
            "required": ["fact_id"],
            "additionalProperties": False,
        },
    },
]

TOOL_STATUS = {
    "open_app": "Launching application",
    "list_apps": "Scanning applications",
    "open_website": "Opening browser",
    "search_memory": "Searching memory",
    "remember": "Updating memory",
    "forget": "Updating memory",
}

Emit = Callable[[dict], None]


class Brain:
    def __init__(self, settings: Settings, memory: Memory, apps: AppCatalog):
        self.settings = settings
        self.memory = memory
        self.apps = apps
        self.history: list[dict] = []  # text-only user/assistant turns
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._minimal_mode = False
        self.load_previous_conversations()

    def load_previous_conversations(self) -> None:
        if not self.settings.remember_conversations:
            self.history = []
            return
        recent = self.memory.recent_messages(limit=30)
        if recent:
            when = datetime.fromtimestamp(recent[0]["created_at"]).strftime("%A %d %B %Y")
            recent[0]["content"] = f"[Earlier conversation, starting {when}]\n{recent[0]['content']}"
        self.history = [{"role": m["role"], "content": m["content"]} for m in recent]

    def new_session(self) -> None:
        self.cancel()
        self.history = []
        self.memory.start_conversation()

    def cancel(self) -> None:
        self._cancel.set()

    def ask(self, text: str, context: dict, emit: Emit) -> None:
        self.cancel()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._cancel = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(text, context, emit, self._cancel), daemon=True, name="brain"
        )
        self._thread.start()

    # ---- worker thread ------------------------------------------------------

    def _run(self, text: str, context: dict, emit: Emit, cancel: threading.Event) -> None:
        key = self.settings.api_key()
        if not key:
            emit({"type": "error", "code": "no_api_key", "message": "No Anthropic API key is configured."})
            emit({"type": "done"})
            return
        client = anthropic.Anthropic(api_key=key)
        emit({"type": "status", "status": "thinking"})
        reply = ""
        try:
            reply = self._converse(client, text, context, emit, cancel, minimal=self._minimal_mode)
        except anthropic.BadRequestError as exc:
            if cancel.is_set() or self._minimal_mode:
                self._report(exc, emit, cancel)
            else:
                # A newer feature (web search, fallbacks, mid-conversation system
                # messages) may not be enabled for this account: retry plainly.
                print(f"[brain] request rejected, retrying with a minimal request: {exc}")
                self._minimal_mode = True
                try:
                    reply = self._converse(client, text, context, emit, cancel, minimal=True)
                except Exception as retry_exc:  # noqa: BLE001
                    self._report(retry_exc, emit, cancel)
        except Exception as exc:  # noqa: BLE001
            self._report(exc, emit, cancel)

        if reply.strip():
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            del self.history[:-MAX_SESSION_MESSAGES]
            if self.settings.remember_conversations:
                self.memory.add_message("user", text)
                self.memory.add_message("assistant", reply)
        emit({"type": "done", "cancelled": cancel.is_set()})

    def _system_blocks(self) -> list[dict]:
        blocks = [{"type": "text", "text": PERSONA}]
        notes = self.memory.notes()
        if notes:
            lines = "\n".join(f"{n.id}. {n.text}" for n in notes)
            blocks.append({"type": "text", "text": f"Facts you have saved about the user (numbered):\n{lines}"})
        return blocks

    def _context_note(self, context: dict) -> str:
        s = self.settings
        local_time = context.get("local_time") or datetime.now().strftime("%A %d %B %Y, %H:%M")
        parts = [f"Current local date and time: {local_time}."]
        if s.user_name:
            parts.append(f"The user's name is {s.user_name}.")
        if s.address_as:
            parts.append(f'Address the user as "{s.address_as}" where it sounds natural.')
        if s.location:
            parts.append(f"The user is in {s.location}.")
        if context.get("weather"):
            parts.append(f"Current weather there: {context['weather']}.")
        if context.get("input_mode") == "voice":
            parts.append("This message came from speech recognition.")
        if not s.allow_open_apps:
            parts.append("Opening apps and websites is switched off in settings.")
        return " ".join(parts)

    def _tools(self, minimal: bool) -> list[dict]:
        tools = [dict(t, eager_input_streaming=True) for t in CLIENT_TOOLS]
        if not minimal and self.settings.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
        return tools

    def _converse(self, client, text, context, emit, cancel, minimal: bool) -> str:
        note = self._context_note(context)
        if minimal:
            messages = [*self.history, {"role": "user", "content": f"{text}\n\n({note})"}]
        else:
            # Volatile details sit in a system message right after the new user
            # turn, so the persona and earlier turns stay identical (cacheable).
            messages = [*self.history, {"role": "user", "content": text}, {"role": "system", "content": note}]

        params = dict(
            model=self.settings.model or "claude-opus-5-5",
            max_tokens=4096,
            system=self._system_blocks(),
            tools=self._tools(minimal),
            output_config={"effort": self.settings.effort or "low"},
        )
        if not minimal:
            params.update(cache_control={"type": "ephemeral"}, betas=["server-side-fallback-2026-07-01"], fallbacks="default")

        spoken = ""
        json_retries = 0
        rounds = 0
        while rounds < MAX_TOOL_ROUNDS:
            if cancel.is_set():
                break
            rounds += 1
            stream_fn = client.messages.stream if minimal else client.beta.messages.stream
            try:
                with stream_fn(messages=messages, **params) as stream:
                    for event in stream:
                        if cancel.is_set():
                            break
                        if event.type == "content_block_start":
                            block = event.content_block
                            if block.type == "server_tool_use":
                                emit({"type": "status", "status": "Searching the web"})
                            elif block.type == "tool_use":
                                emit({"type": "status", "status": TOOL_STATUS.get(block.name, "Working")})
                        elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                            spoken += event.delta.text
                            emit({"type": "text", "text": event.delta.text})
                    if cancel.is_set():
                        break
                    message = stream.get_final_message()
            except ValueError:
                # A tool input arrived as JSON the SDK couldn't parse: re-issue.
                json_retries += 1
                if json_retries > 2:
                    raise
                rounds -= 1
                continue

            if message.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": message.content})
                continue
            if message.stop_reason == "refusal":
                if not spoken.strip():
                    line = "I'm afraid that's not something I can help with."
                    spoken = line
                    emit({"type": "text", "text": line})
                break
            tool_uses = [b for b in message.content if b.type == "tool_use"]
            if message.stop_reason != "tool_use" or not tool_uses:
                break  # end_turn, or max_tokens (never run a possibly truncated tool call)

            messages.append({"role": "assistant", "content": message.content})
            results = []
            for block in tool_uses:
                content, is_error = self._run_tool(block.name, block.input)
                emit({"type": "tool", "name": block.name, "input": block.input, "result": content, "error": is_error})
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            if spoken and not spoken.endswith((" ", "\n")):
                spoken += " "
                emit({"type": "text", "text": " "})
        return spoken

    # ---- tools ----------------------------------------------------------------

    def _run_tool(self, name: str, args) -> tuple[str, bool]:
        if not isinstance(args, dict):
            return json.dumps({"INVALID_JSON": str(args)}), True
        try:
            if name in ("open_app", "list_apps", "open_website") and not self.settings.allow_open_apps:
                return "Opening apps and websites is switched off in JARVIS settings.", True
            if name == "open_app":
                return self._open_app(_require_str(args, "name"))
            if name == "list_apps":
                query = args.get("query", "")
                apps = self.apps.find(query, limit=40) if str(query).strip() else self.apps.apps()[:150]
                if not apps:
                    return "No matching applications found.", False
                return "Installed apps: " + ", ".join(a.name for a in apps), False
            if name == "open_website":
                url = _require_str(args, "url").strip()
                if not url.startswith(("https://", "http://")):
                    url = "https://" + url
                webbrowser.open(url)
                return f"Opened {url} in the browser.", False
            if name == "search_memory":
                hits = self.memory.search(_require_str(args, "query"))
                if not hits:
                    return "Nothing about that in previous conversations.", False
                lines = []
                for h in hits:
                    when = datetime.fromtimestamp(h["created_at"]).strftime("%d %b %Y")
                    who = "User" if h["role"] == "user" else "JARVIS"
                    lines.append(f"[{when}] {who}: {h['text'][:400]}")
                return "\n".join(lines), False
            if name == "remember":
                note_id = self.memory.add_note(_require_str(args, "fact"))
                return f"Saved as fact {note_id}.", False
            if name == "forget":
                fact_id = args.get("fact_id")
                if not isinstance(fact_id, int):
                    return "fact_id must be an integer.", True
                return ("Forgotten." if self.memory.delete_note(fact_id) else "No fact with that number."), False
        except ValueError as exc:
            return str(exc), True
        except Exception as exc:  # noqa: BLE001
            return f"Tool failed: {exc}", True
        return f"Unknown tool {name}.", True

    def _open_app(self, name: str) -> tuple[str, bool]:
        matches = self.apps.find(name)
        if not matches:
            return f"No installed app matches '{name}'.", True
        best = matches[0]
        exactish = len(matches) == 1 or best.name.lower() == name.lower() or name.lower() in best.name.lower()
        if not exactish and len(matches) > 1:
            return "Several close matches: " + ", ".join(m.name for m in matches) + ". Ask which one.", False
        self.apps.launch(best)
        return f"Launched {best.name}.", False

    # ---- errors ---------------------------------------------------------------

    def _report(self, exc: Exception, emit: Emit, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        print(f"[brain] request failed: {exc!r}")
        if isinstance(exc, anthropic.AuthenticationError):
            code, message = "auth", "The API key was rejected."
        elif isinstance(exc, anthropic.NotFoundError):
            code, message = "not_found", "That model was not found."
        elif isinstance(exc, anthropic.RateLimitError):
            code, message = "rate_limit", "Rate limited by the API."
        elif isinstance(exc, anthropic.APIConnectionError):
            code, message = "network", "Could not reach the Claude API."
        elif isinstance(exc, anthropic.APIStatusError):
            code, message = f"http_{exc.status_code}", str(exc)
        else:
            code, message = "unknown", str(exc)
        emit({"type": "error", "code": code, "message": message})


def _require_str(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{key}' must be a non-empty string.")
    return value

