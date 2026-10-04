# J.A.R.V.I.S.

A voice-controlled desktop assistant inspired by JARVIS from the Iron Man films, written in Python with a native Qt interface.

- **Talk to it**: say "Jarvis…" and it listens. Speech recognition runs locally with Whisper, so your voice never leaves your computer.
- **It talks back** in your operating system's voice, sentence by sentence as the answer arrives.
- **It remembers you.** Every conversation is saved on your computer. JARVIS recalls recent chats when it starts, keeps a list of facts about you, and can search everything you've ever talked about.
- **It opens your apps.** "Jarvis, open Spotify." It finds apps in your Start Menu (or /Applications, or your Linux app menu) and launches them. It can open websites too.
- **It knows things**: Claude is the brain, with optional live web search.
- **The HUD**: an animated arc reactor that reacts to your voice and JARVIS's, plus clock, weather, live CPU and memory, a comms log and subsystem status.

## Quick start

You need [Python](https://www.python.org/downloads/) 3.10 or newer and an [Anthropic API key](https://console.anthropic.com/settings/keys).

**Windows**: double-click `JARVIS.bat`. The first run sets everything up; after that it starts straight away.

**macOS / Linux**: run `./jarvis.sh`.

Or by hand:

```bash
python -m venv .venv
.venv/Scripts/activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m jarvis
```

On first launch JARVIS opens its settings. Paste your API key and press **Save**. The key goes into your system's credential store (Windows Credential Manager or macOS Keychain). You can also set `ANTHROPIC_API_KEY` instead.

The first launch also downloads the speech recognition model (about 145 MB). After that it works offline.

## Talking to JARVIS

| Action | How |
| --- | --- |
| Wake JARVIS | Say **"Jarvis"**, optionally followed by the request: *"Jarvis, what's the weather like?"* |
| Follow up | For 8 seconds after JARVIS answers you can keep talking without saying "Jarvis" |
| Talk without the wake word | Click the reactor or press **Space** |
| Interrupt JARVIS | Click the reactor, or press **Space** or **Esc** |
| Type instead | Just start typing; press **Enter** to send |
| Mute the microphone | Press **M** or use the **Mic** button |
| Full screen | **F11** |
| Settings | **Ctrl+,** or the gear icon |

Things to try:

- "Jarvis, open Chrome." / "Launch Spotify." / "Open YouTube."
- "Remember that my sister's birthday is the 12th of March."
- "What did we talk about yesterday?"
- "What's in the news today?"

## Memory

Everything stays in a local database on your computer:

- Windows: `%APPDATA%\JARVIS\memory.db`
- macOS: `~/Library/Application Support/JARVIS/memory.db`
- Linux: `~/.config/jarvis/memory.db`

JARVIS replays the last few exchanges when it starts, so it picks up where you left off. It saves important facts by itself (or when you ask it to remember something), and searches older conversations when you mention something from the past. In settings you can see and delete saved facts, turn conversation memory off, or wipe it all. **Reset** in the comms log starts a fresh conversation but keeps long-term memory.

## Opening apps

JARVIS can only launch apps that appear in your system's own app list: Start Menu shortcuts and Store apps on Windows, `/Applications` on macOS, and `.desktop` entries on Linux. It never runs arbitrary commands. You can switch this off in settings.

## Settings

- **Address me as**: set it to "sir" for the full movie experience.
- **Location**: shows local weather on the HUD and lets JARVIS answer weather questions instantly.
- **Voice and speed**: any voice installed on your system. On Windows, install the *English (United Kingdom)* speech pack (*Settings › Time & language › Speech*) for a British voice. On macOS, "Daniel" is a good choice.
- **Microphone sensitivity**: raise it if JARVIS misses quiet speech, lower it if background noise sets it off.
- **Speech model**: Tiny is fastest, Small is most accurate.
- **Model and effort**: defaults to `claude-opus-5-5` at low effort for snappy spoken replies.

## Building a standalone app

```bash
python build_exe.py
```

The app is created in `dist/JARVIS/`: `JARVIS.exe` on Windows, `JARVIS.app` on macOS. Build it on the platform you want to run it on.

## How it works

```
 Microphone ─► voice activity detection ─► Whisper (local) ─► text
                                                              │
 HUD ◄─ streamed reply ◄─ Claude ◄─ memory, app list, web ◄───┘
  │
  └─► sentences spoken as they arrive (SAPI / say / espeak)
```

| File | Role |
| --- | --- |
| `jarvis/app.py` | Main window and conversation flow: wake word, listening, thinking, speaking |
| `jarvis/brain.py` | Streams replies from Claude, runs its tools; JARVIS's personality lives here |
| `jarvis/memory.py` | SQLite conversation history, full-text search and saved facts |
| `jarvis/apps.py` | Finds installed apps and launches them |
| `jarvis/ears.py` | Microphone capture, voice activity detection, Whisper transcription |
| `jarvis/voice.py` | Text-to-speech with the OS voices |
| `jarvis/hud.py` | Arc reactor, backdrop and panel drawing (QPainter) |
| `jarvis/panels.py` | Clock, weather and system diagnostics |
| `jarvis/settings_dialog.py` | Configuration window |
| `jarvis/config.py` | Settings file and API key storage |

## Troubleshooting

- **JARVIS doesn't hear me**: check that the Microphone row in *Subsystems* says *Live*, and that Windows/macOS allows Python (or JARVIS.exe) to use the microphone. Try raising the sensitivity.
- **JARVIS hears me but ignores me**: with the wake word on, start with "Jarvis". Whisper sometimes hears it as something else; click the reactor to skip the wake word.
- **No voice**: on Windows make sure `pywin32` installed (it's in requirements.txt). On Linux install `espeak-ng`. JARVIS still shows captions without a voice.
- **"Couldn't open" an app**: ask "what apps do I have with Spotify in the name?" to see what JARVIS can find.
