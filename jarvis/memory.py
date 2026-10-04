"""Long-term memory: every conversation is saved to a local SQLite database.

Three things make JARVIS remember:
  * the tail of recent conversations is replayed at the start of each session,
  * facts JARVIS decides to remember (via the `remember` tool) are always in
    its instructions,
  * older conversations can be searched with full-text search (the
    `search_memory` tool).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Note:
    id: int
    text: str
    created_at: float


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._fts = True
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )
            try:
                self._db.executescript(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
                        USING fts5(text, content='messages', content_rowid='id');
                    CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                        INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                        INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.id, old.text);
                    END;
                    """
                )
            except sqlite3.OperationalError:
                self._fts = False  # SQLite built without FTS5: fall back to LIKE
        self.conversation_id: int | None = None

    # ---- conversations ---------------------------------------------------

    def start_conversation(self) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO conversations (started_at) VALUES (?)", (time.time(),))
            self.conversation_id = cur.lastrowid
        return self.conversation_id

    def add_message(self, role: str, text: str) -> None:
        if not text.strip():
            return
        if self.conversation_id is None:
            self.start_conversation()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO messages (conversation_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                (self.conversation_id, role, text, time.time()),
            )

    def recent_messages(self, limit: int = 30) -> list[dict]:
        """The last `limit` messages from earlier conversations, oldest first,
        trimmed so the list starts with a user turn and alternates roles."""
        with self._lock:
            rows = self._db.execute(
                "SELECT role, text, created_at FROM messages WHERE conversation_id IS NOT ? "
                "ORDER BY id DESC LIMIT ?",
                (self.conversation_id, limit),
            ).fetchall()
        rows = list(reversed(rows))
        merged: list[dict] = []
        for row in rows:
            if merged and merged[-1]["role"] == row["role"]:
                merged[-1]["content"] += "\n" + row["text"]
            else:
                merged.append({"role": row["role"], "content": row["text"], "created_at": row["created_at"]})
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        if merged and merged[-1]["role"] == "user":
            merged.pop()  # an unanswered question would break alternation
        return merged

    def search(self, query: str, limit: int = 8) -> list[dict]:
        query = query.strip()
        if not query:
            return []
        with self._lock:
            if self._fts:
                terms = " OR ".join(f'"{w}"' for w in query.replace('"', " ").split() if len(w) > 1)
                if not terms:
                    return []
                rows = self._db.execute(
                    "SELECT m.role, m.text, m.created_at FROM messages_fts f "
                    "JOIN messages m ON m.id = f.rowid WHERE messages_fts MATCH ? "
                    "ORDER BY bm25(messages_fts) LIMIT ?",
                    (terms, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT role, text, created_at FROM messages WHERE text LIKE ? ORDER BY id DESC LIMIT ?",
                    (f"%{query}%", limit),
                ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> tuple[int, int, int]:
        with self._lock:
            conversations = self._db.execute(
                "SELECT COUNT(DISTINCT conversation_id) FROM messages"
            ).fetchone()[0]
            messages = self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            notes = self._db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        return conversations, messages, notes

    # ---- notes (facts JARVIS chose to remember) -----------------------------

    def add_note(self, text: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO notes (text, created_at) VALUES (?, ?)", (text.strip(), time.time()))
            return cur.lastrowid

    def delete_note(self, note_id: int) -> bool:
        with self._lock, self._db:
            return self._db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount > 0

    def notes(self) -> list[Note]:
        with self._lock:
            rows = self._db.execute("SELECT id, text, created_at FROM notes ORDER BY id").fetchall()
        return [Note(r["id"], r["text"], r["created_at"]) for r in rows]

    def wipe(self) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM messages")
            self._db.execute("DELETE FROM notes")
            self._db.execute("DELETE FROM conversations")
            if self._fts:
                self._db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
        self.conversation_id = None
