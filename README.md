# J.A.R.V.I.S.

A voice-controlled desktop assistant inspired by JARVIS from the Iron Man films.
Talk to it, and it talks back, with an animated arc-reactor HUD.

- **Desktop app** built with Electron (Windows, macOS and Linux).
- **Speech recognition** runs locally with OpenAI's Whisper model. Your voice never leaves your computer.
- **Brain** is Claude (Anthropic API), with optional live web search.
- **Voice** uses your operating system's built-in voices, preferring a British male voice.
- **HUD** with a live arc reactor that reacts to your voice and JARVIS's, plus clock, weather and real system diagnostics.

## Quick start

You need [Node.js](https://nodejs.org) 20 or newer and an [Anthropic API key](https://console.anthropic.com/settings/keys).

```bash
git clone https://github.com/F1nnAlex/JARVIS-System.git
cd JARVIS-System
npm install
npm start
```

On first launch JARVIS opens the settings panel. Paste your API key and press **Save**. The key is encrypted with your operating system's keychain and stored only on your computer. (You can also set `ANTHROPIC_API_KEY` in your environment instead.)

The first launch also downloads the speech recognition model (about 80 MB). After that it is cached and works offline.

## Talking to JARVIS

| Action | How |
| --- | --- |
| Wake JARVIS | Say **"Jarvis"**, optionally followed by your request: *"Jarvis, what's the weather like?"* |
| Follow up | For 8 seconds after JARVIS answers you can keep talking without saying "Jarvis" |
| Talk without the wake word | Click the reactor or press **Space** |
| Interrupt JARVIS | Click the reactor, or press **Space** or **Esc** |
| Type instead | Just start typing; press **Enter** to send |
| Mute the microphone | Press **M** or use the **Mic** button |
| Summon from anywhere | **Ctrl+Shift+J** (**Cmd+Shift+J** on macOS) |
| Full screen | **F11** |
| Settings | **Ctrl+,** or the gear icon |

Turn the wake word off in settings (or with the **Wake word** button) to have JARVIS respond to everything you say.

## Settings

- **Address me as**: set this to "sir" for the full movie experience, or anything you like.
- **Location**: shows local weather on the HUD and lets JARVIS answer weather questions instantly.
- **Voice, speed and pitch**: pick any voice installed on your system. On Windows, "Microsoft George" sounds the most like JARVIS. On macOS, try "Daniel". You can install more voices in your OS settings (Windows: *Settings › Time & language › Speech*; macOS: *System Settings › Accessibility › Spoken Content*).
- **Microphone sensitivity**: raise it if JARVIS misses quiet speech, lower it if background noise sets it off.
- **Speech recognition model**: Tiny is fastest, Small is most accurate.
- **Model and effort**: defaults to `claude-opus-5-5` at low effort for snappy spoken replies.
- **Web search**: lets JARVIS look up live information such as news and sports results.

## Building an installer

```bash
npm run dist:win    # Windows installer (.exe)
npm run dist:mac    # macOS disk image (.dmg)
npm run dist:linux  # Linux AppImage
```

Installers are written to `dist/`. Build each one on its own platform.

## How it works

```
 Microphone ──► voice activity detection ──► Whisper (local, in a worker)
                                                    │ text
                                                    ▼
 HUD ◄── streamed text ◄── Claude API (main process, key stays there)
  │
  └──► sentences spoken as they arrive (OS speech synthesis)
```

| File | Role |
| --- | --- |
| `src/main/main.js` | Electron main process: window, secure `app://` protocol, IPC, system stats |
| `src/main/brain.js` | Streams replies from Claude; JARVIS's personality lives here |
| `src/main/settings.js` | Settings storage with the API key encrypted via the OS keychain |
| `src/preload.js` | The narrow bridge between the window and the main process |
| `src/renderer/app.js` | Conversation flow: wake word, listening, thinking, speaking |
| `src/renderer/ears.js` | Microphone capture and voice activity detection |
| `src/renderer/stt-worker.js` | Whisper speech-to-text with transformers.js |
| `src/renderer/voice.js` | Sentence-by-sentence text-to-speech |
| `src/renderer/hud.js` | Arc reactor and backdrop animations |
| `src/renderer/panels.js` | Clock, weather and system diagnostics panels |

## Troubleshooting

- **JARVIS doesn't hear me**: check the Microphone row in *Subsystems* says *Live*, and that your OS allows the app to use the microphone. Try raising the sensitivity.
- **JARVIS hears me but ignores me**: with the wake word on, start with "Jarvis". Whisper sometimes hears it as something else; click the reactor to skip the wake word.
- **No voice**: on Linux, install `speech-dispatcher` and `espeak-ng`. JARVIS still shows captions without a voice.
- **"Neural uplink" errors**: check your API key and internet connection in settings.
