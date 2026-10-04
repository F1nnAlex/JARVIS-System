// JARVIS front end: wires the ears, brain, voice and HUD together.

import { Reactor, Backdrop } from './hud.js';
import { Ears, cleanTranscript } from './ears.js';
import { Voice } from './voice.js';
import { sfx } from './sfx.js';
import { startClock, startSystemStats, startBattery, Weather } from './panels.js';

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const WAKE_WORD = /\b(?:hey|hi|okay|ok|yo)?[\s,]*\b(jarvis|jarvas|jervis|jarvus|javis|jarvi)\b[\s,.!?]*/i;
const FOLLOW_UP_MS = 8000;
const ACKNOWLEDGEMENTS = ['Yes?', 'At your service.', 'Listening.', 'Go ahead.', 'How can I help?'];

const STATUS_TEXT = {
  boot: 'Booting',
  standby: 'Standby',
  listening: 'Listening',
  transcribing: 'Processing speech',
  thinking: 'Thinking',
  speaking: 'Responding',
  error: 'Fault detected',
};

const reactor = new Reactor($('reactor'));
const backdrop = new Backdrop($('backdrop'));
const ears = new Ears();
const voice = new Voice();
const weather = new Weather();

let settings = null;
let state = 'boot';
let muted = false;
let followUpUntil = 0;
let turnId = 0;
let turn = null; // { id, entry, text, brainDone }
let modelProgress = 0;
let earsError = '';
let transcribing = Promise.resolve();

// ---------- State & HUD ----------

function setState(next, statusOverride) {
  state = next;
  document.body.dataset.state = next;
  reactor.setState(next === 'standby' && muted ? 'muted' : next);
  let text = statusOverride || STATUS_TEXT[next] || next;
  if (next === 'standby' && !statusOverride) {
    if (muted) text = 'Microphone muted';
    else if (inFollowUp()) text = 'Listening';
    else if (settings?.wakeWord) text = 'Standby · say “Jarvis”';
  }
  $('status-text').textContent = text;
}

function inFollowUp() {
  return performance.now() < followUpUntil;
}

function caption(text, who = 'jarvis') {
  const el = $('caption');
  el.classList.toggle('user', who === 'user');
  el.textContent = who === 'user' && text ? `“${text}”` : text;
}

function setTag(id, text, kind = '') {
  const el = $(id);
  el.textContent = text;
  el.className = `tag ${kind}`;
}

function updateSubsystems() {
  if (!settings) return;
  setTag('sub-brain', settings.hasApiKey ? 'Online' : 'No API key', settings.hasApiKey ? 'ok' : 'bad');
  if (earsError) setTag('sub-ears', 'Offline', 'bad');
  else if (ears.modelReady) setTag('sub-ears', 'Online', 'ok');
  else setTag('sub-ears', modelProgress > 0 ? `Loading ${Math.round(modelProgress * 100)}%` : 'Loading', 'warn');
  if (voice.available) setTag('sub-voice', voice.voice ? voice.voice.name.replace(/^Microsoft\s+/, '').split(/\s+-\s+/)[0] : 'Online', 'ok');
  else setTag('sub-voice', 'No voices', 'warn');
  if (!ears.micReady) setTag('sub-mic', 'Unavailable', 'bad');
  else if (muted) setTag('sub-mic', 'Muted', 'warn');
  else setTag('sub-mic', 'Live', 'ok');

  const wake = $('wake-toggle');
  wake.classList.toggle('on', settings.wakeWord);
  $('wake-label').textContent = settings.wakeWord ? 'Wake word on' : 'Always listening';
  const mic = $('mic-toggle');
  mic.classList.toggle('on', !muted && ears.micReady);
  mic.classList.toggle('off', muted || !ears.micReady);
  $('mic-label').textContent = muted ? 'Mic muted' : ears.micReady ? 'Mic live' : 'No mic';
}

function addLog(kind, text) {
  const log = $('log');
  const entry = document.createElement('div');
  entry.className = `log-entry ${kind}`;
  const who = { user: settings?.userName || 'You', jarvis: 'J.A.R.V.I.S.', system: 'System', error: 'Fault' }[kind];
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  entry.innerHTML = '<div class="who"><b></b><span></span></div><div class="text"></div>';
  entry.querySelector('.who b').textContent = who;
  entry.querySelector('.who span').textContent = time;
  entry.querySelector('.text').textContent = text;
  log.appendChild(entry);
  while (log.children.length > 200) log.firstChild.remove();
  log.scrollTop = log.scrollHeight;
  return entry;
}

function setLogText(entry, text) {
  entry.querySelector('.text').textContent = text;
  const log = $('log');
  log.scrollTop = log.scrollHeight;
}

function animate(now) {
  backdrop.frame(now);
  reactor.frame(now);
  requestAnimationFrame(animate);
}

reactor.setLevelSource(() => (voice.speaking ? voice.visuals() : ears.visuals()));

// ---------- Conversation ----------

function context(inputMode) {
  return {
    localTime: new Date().toLocaleString([], {
      weekday: 'long', year: 'numeric', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
    }),
    weather: weather.summary || undefined,
    inputMode,
  };
}

function ask(text, inputMode) {
  interrupt({ silent: true });
  const id = ++turnId;
  followUpUntil = 0;
  addLog('user', text);
  caption(text, 'user');
  turn = { id, entry: addLog('jarvis', '…'), text: '', brainDone: false, followUp: true };
  setState('thinking');
  sfx.processing();
  ears.setPaused(true);
  voice.beginStream();
  window.jarvis.ask(id, text, context(inputMode));
}

// Speak a fixed line. Only lines that invite an answer open the follow-up window.
function speakLine(text, { log = true, followUp = false } = {}) {
  const id = ++turnId;
  turn = { id, entry: log ? addLog('jarvis', text) : null, text, brainDone: true, followUp };
  voice.say(text);
}

function brainErrorLine(evt) {
  switch (evt.code) {
    case 'no_api_key':
      return "I'm afraid my neural uplink is offline. Please add your Anthropic API key in settings.";
    case 'auth':
      return 'My credentials were rejected. Please check the API key in settings.';
    case 'network':
      return "I can't reach my servers at the moment. Please check the internet connection.";
    case 'rate_limit':
      return "I'm being rate limited. Give me a moment and try again.";
    case 'not_found':
      return "That model doesn't appear to exist. Please check the model name in settings.";
    default:
      return 'I encountered a problem reaching my servers. Details are in the log.';
  }
}

window.jarvis.onBrain((evt) => {
  if (!turn || evt.id !== turn.id) return;
  if (evt.type === 'status') {
    if (evt.status === 'searching') setState('thinking', 'Searching the web');
  } else if (evt.type === 'text') {
    turn.text += evt.text;
    setLogText(turn.entry, turn.text);
    voice.pushText(evt.text);
  } else if (evt.type === 'error') {
    const line = brainErrorLine(evt);
    addLog('error', evt.message || evt.code);
    sfx.error();
    turn.text = line;
    setLogText(turn.entry, line);
    voice.pushText(line);
    setState('error');
    if (evt.code === 'no_api_key' || evt.code === 'auth') openSettings();
  } else if (evt.type === 'done') {
    turn.brainDone = true;
    if (!turn.text.trim()) turn.entry.remove();
    voice.endStream();
  }
});

voice.addEventListener('start', () => {
  ears.setPaused(true);
  if (state !== 'error') setState('speaking');
});

voice.addEventListener('sentence', (e) => caption(e.detail.text));

voice.addEventListener('idle', (e) => {
  if (e.detail.cancelled) return;
  if (turn && !turn.brainDone) return;
  finishTurn({ followUp: Boolean(turn && turn.followUp) });
});

function finishTurn({ followUp = false } = {}) {
  turn = null;
  if (followUp && !muted) followUpUntil = performance.now() + FOLLOW_UP_MS;
  ears.setPaused(muted);
  setState('standby');
  setTimeout(() => {
    if (state === 'standby') {
      if (!inFollowUp()) setState('standby');
      if (!voice.speaking) caption('');
    }
  }, FOLLOW_UP_MS + 50);
}

function interrupt({ silent = false } = {}) {
  const busy = turn || voice.speaking || state === 'thinking';
  if (!busy) return false;
  turnId++;
  window.jarvis.cancel();
  voice.cancel();
  if (turn && turn.entry && !turn.text.trim()) turn.entry.remove();
  if (!silent) {
    sfx.cancel();
    caption('');
    finishTurn({ followUp: false });
  } else {
    turn = null;
  }
  return true;
}

function listenNow() {
  if (!ears.micReady) {
    caption('No microphone is available. You can still type commands below.');
    return;
  }
  if (!ears.modelReady) {
    caption(earsError ? 'Speech recognition is offline.' : `Speech recognition is still loading (${Math.round(modelProgress * 100)}%).`);
    return;
  }
  if (muted) setMuted(false);
  ears.listenNow();
  setState('listening');
  sfx.listen();
}

function toggleTalk() {
  if (interrupt()) {
    listenNow();
    return;
  }
  if (state === 'listening' && ears.manual) {
    ears.cancelManual();
    setState('standby');
    return;
  }
  listenNow();
}

function stripWakeWord(text) {
  return text.replace(WAKE_WORD, ' ').replace(/^[\s,.!?]+|[\s,]+$/g, '').trim();
}

ears.addEventListener('speech-start', (e) => {
  if (state !== 'standby' && state !== 'listening') return;
  if (e.detail.manual || !settings.wakeWord || inFollowUp()) setState('listening');
});

ears.addEventListener('speech-discarded', () => {
  if (state === 'listening') setState('standby');
});

ears.addEventListener('manual-timeout', () => {
  if (state === 'listening') setState('standby');
});

ears.addEventListener('utterance', (e) => {
  const { audio, manual } = e.detail;
  const expecting = manual || !settings.wakeWord || inFollowUp();
  transcribing = transcribing.then(() => handleUtterance(audio, manual, expecting));
});

async function handleUtterance(audio, manual, expecting) {
  if (state === 'thinking' || state === 'speaking') return;
  if (expecting) setState('transcribing');
  let text = '';
  try {
    text = cleanTranscript(await ears.transcribe(audio));
  } catch (err) {
    addLog('error', `Speech recognition failed: ${err.message}`);
  }
  if (state === 'thinking' || state === 'speaking') return; // typed command won the race

  const hasWake = WAKE_WORD.test(text);
  if (!text || (!expecting && !hasWake)) {
    if (state === 'transcribing' || state === 'listening') setState('standby');
    return;
  }
  const command = hasWake ? stripWakeWord(text) : text;
  if (command.replace(/[^a-z0-9]/gi, '').length < 2) {
    // Just "Jarvis": acknowledge and wait for the actual request.
    sfx.wake();
    caption(text, 'user');
    speakLine(ACKNOWLEDGEMENTS[Math.floor(Math.random() * ACKNOWLEDGEMENTS.length)], { log: false, followUp: true });
    return;
  }
  ask(command, 'voice');
}

// ---------- Settings ----------

function openSettings() {
  fillSettingsForm();
  $('settings').classList.add('open');
  $('settings').setAttribute('aria-hidden', 'false');
}

function closeSettings() {
  $('settings').classList.remove('open');
  $('settings').setAttribute('aria-hidden', 'true');
  voice.setPreferences(settings); // drop any unsaved test values
}

function fillVoiceSelect() {
  const select = $('voice-select');
  const current = settings?.voiceName || '';
  select.innerHTML = '';
  const auto = document.createElement('option');
  auto.value = '';
  auto.textContent = 'Automatic (best British voice)';
  select.appendChild(auto);
  const voices = [...voice.voices].sort((a, b) => {
    const ae = /^en/i.test(a.lang) ? 0 : 1;
    const be = /^en/i.test(b.lang) ? 0 : 1;
    return ae - be || a.name.localeCompare(b.name);
  });
  for (const v of voices) {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = `${v.name} (${v.lang})`;
    select.appendChild(opt);
  }
  select.value = current;
  $('voice-hint').textContent = voice.available
    ? `Currently using: ${voice.voice ? voice.voice.name : 'system default'}`
    : 'No system voices were found. On Linux, install speech-dispatcher or espeak-ng.';
}

function fillSettingsForm() {
  const form = $('settings-form');
  for (const [key, value] of Object.entries(settings)) {
    const field = form.elements.namedItem(key);
    if (!field || key === 'apiKey') continue;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else field.value = value;
  }
  form.elements.namedItem('apiKey').value = '';
  const status = $('api-key-status');
  if (settings.apiKeySource === 'app') {
    status.textContent = 'A key is saved. Leave blank to keep it.';
    status.className = 'hint ok';
  } else if (settings.apiKeySource === 'environment') {
    status.textContent = 'Using ANTHROPIC_API_KEY from the environment.';
    status.className = 'hint ok';
  } else {
    status.textContent = 'No key yet. JARVIS needs one to think.';
    status.className = 'hint warn';
  }
  fillVoiceSelect();
  updateRangeOutputs();
}

function updateRangeOutputs() {
  const form = $('settings-form');
  $('rate-out').textContent = `${Number(form.elements.voiceRate.value).toFixed(2)}×`;
  $('pitch-out').textContent = Number(form.elements.voicePitch.value).toFixed(2);
  $('sens-out').textContent = `${Math.round(form.elements.micSensitivity.value * 100)}%`;
}

function readSettingsForm() {
  const form = $('settings-form');
  const patch = {
    model: form.elements.model.value.trim() || 'claude-opus-5-5',
    effort: form.elements.effort.value,
    webSearch: form.elements.webSearch.checked,
    userName: form.elements.userName.value.trim(),
    addressAs: form.elements.addressAs.value.trim(),
    location: form.elements.location.value.trim(),
    voiceName: form.elements.voiceName.value,
    voiceRate: Number(form.elements.voiceRate.value),
    voicePitch: Number(form.elements.voicePitch.value),
    wakeWord: form.elements.wakeWord.checked,
    micSensitivity: Number(form.elements.micSensitivity.value),
    whisperModel: form.elements.whisperModel.value,
  };
  const key = form.elements.apiKey.value.trim();
  if (key) patch.apiKey = key;
  return patch;
}

function applySettings(prev = {}) {
  voice.setPreferences(settings);
  ears.sensitivity = settings.micSensitivity;
  if (settings.whisperModel !== prev.whisperModel && prev.whisperModel) {
    modelProgress = 0;
    earsError = '';
    ears.loadModel(settings.whisperModel);
  }
  weather.setLocation(settings.location);
  updateSubsystems();
  if (state === 'standby') setState('standby');
}

async function saveSettings(patch) {
  const prev = settings;
  settings = await window.jarvis.saveSettings(patch);
  applySettings(prev);
}

$('settings-form').addEventListener('input', (e) => {
  updateRangeOutputs();
  if (['voiceName', 'voiceRate', 'voicePitch'].includes(e.target.name)) {
    voice.setPreferences(readSettingsForm());
  }
});

$('settings-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const hadKey = settings.hasApiKey;
  await saveSettings(readSettingsForm());
  closeSettings();
  addLog('system', 'Configuration saved.');
  if (!hadKey && settings.hasApiKey) speakLine('Neural uplink established. I am fully operational.');
});

$('voice-test').addEventListener('click', () => {
  voice.setPreferences(readSettingsForm());
  speakLine('Good day. All systems are functioning within normal parameters.', { log: false });
});

$('settings-close').addEventListener('click', closeSettings);

// ---------- Controls ----------

function setMuted(next) {
  muted = next;
  ears.setMuted(muted);
  if (muted) followUpUntil = 0;
  updateSubsystems();
  if (state === 'standby') setState('standby');
}

$('reactor').addEventListener('click', toggleTalk);

$('wake-toggle').addEventListener('click', () => saveSettings({ wakeWord: !settings.wakeWord }));
$('mic-toggle').addEventListener('click', () => setMuted(!muted));

$('clear-log').addEventListener('click', () => {
  interrupt({ silent: true });
  window.jarvis.resetConversation();
  $('log').innerHTML = '';
  addLog('system', 'Conversation memory cleared.');
  finishTurn({ followUp: false });
});

$('command-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('command-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  ask(text, 'text');
});

document.querySelectorAll('.win-btn[data-action]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const action = btn.dataset.action;
    if (action === 'settings') openSettings();
    else window.jarvis.windowControl(action);
  });
});

document.addEventListener('keydown', (e) => {
  const typing = e.target.closest('input, select, textarea');
  if (e.key === 'Escape') {
    if ($('settings').classList.contains('open')) closeSettings();
    else if (!interrupt() && state === 'listening') {
      ears.cancelManual();
      setState('standby');
    }
    return;
  }
  if (e.key === 'F11') {
    e.preventDefault();
    window.jarvis.windowControl('fullscreen');
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === ',') {
    e.preventDefault();
    openSettings();
    return;
  }
  if (typing || state === 'boot') return;
  if (e.code === 'Space' && !e.repeat) {
    e.preventDefault();
    toggleTalk();
  } else if (e.key.toLowerCase() === 'm' && !e.ctrlKey && !e.metaKey) {
    setMuted(!muted);
  } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
    $('command-input').focus(); // start typing anywhere
  }
});

window.jarvis.onSummon(() => {
  if (state !== 'boot') listenNow();
});

// ---------- Boot ----------

ears.addEventListener('model-progress', (e) => {
  modelProgress = e.detail.fraction;
  updateSubsystems();
});
ears.addEventListener('model-ready', () => {
  earsError = '';
  updateSubsystems();
  if (state !== 'boot') addLog('system', 'Speech recognition online.');
});
ears.addEventListener('model-error', (e) => {
  earsError = e.detail.message;
  addLog('error', `Speech recognition failed to load: ${earsError}`);
  updateSubsystems();
});
ears.addEventListener('mic-lost', () => {
  addLog('error', 'Microphone disconnected.');
  updateSubsystems();
});
voice.addEventListener('voices', () => {
  updateSubsystems();
  if ($('settings').classList.contains('open')) fillVoiceSelect();
});

async function bootLine(label, task) {
  const line = document.createElement('div');
  line.className = 'boot-line';
  line.innerHTML = '<span></span><b>…</b>';
  line.querySelector('span').textContent = label;
  $('boot-lines').appendChild(line);
  const result = await task();
  const b = line.querySelector('b');
  b.textContent = result.text;
  if (result.warn) b.classList.add('warn');
  await sleep(260);
}

function greeting() {
  const hour = new Date().getHours();
  const part = hour < 5 ? 'evening' : hour < 12 ? 'morning' : hour < 18 ? 'afternoon' : 'evening';
  const name = settings.addressAs || settings.userName;
  const hello = `Good ${part}${name ? `, ${name}` : ''}.`;
  if (!settings.hasApiKey) {
    return `${hello} JARVIS online. However, my neural uplink is not configured. Please add your Anthropic API key in settings.`;
  }
  if (!ears.micReady) return `${hello} JARVIS online. I can't find a microphone, so you'll have to type to me for now.`;
  return settings.wakeWord
    ? `${hello} JARVIS online and at your service. Just say my name when you need me.`
    : `${hello} JARVIS online and at your service.`;
}

async function boot() {
  requestAnimationFrame(animate);
  startClock();
  startSystemStats();
  startBattery();
  settings = await window.jarvis.getSettings();
  applySettings();
  sfx.boot();

  await bootLine('Initializing neural interface', async () => {
    await sleep(500);
    return { text: 'OK' };
  });
  await bootLine('Microphone array', async () => {
    try {
      await ears.start();
      return { text: 'OK' };
    } catch (err) {
      console.warn('microphone unavailable', err);
      return { text: 'NOT FOUND', warn: true };
    }
  });
  await bootLine('Speech recognition core', async () => {
    ears.loadModel(settings.whisperModel);
    await Promise.race([
      new Promise((r) => ears.addEventListener('model-ready', r, { once: true })),
      sleep(1500),
    ]);
    if (ears.modelReady) return { text: 'OK' };
    return { text: modelProgress > 0 ? 'DOWNLOADING' : 'LOADING', warn: true };
  });
  await bootLine('Voice synthesis', async () => {
    for (let i = 0; i < 10 && !voice.available; i++) await sleep(100);
    return voice.available ? { text: 'OK' } : { text: 'NO VOICES', warn: true };
  });
  await bootLine('Neural uplink (Claude)', async () => {
    await sleep(300);
    return settings.hasApiKey ? { text: 'OK' } : { text: 'NO API KEY', warn: true };
  });
  await bootLine('All systems', async () => ({ text: 'ONLINE' }));

  reactor.power = 0;
  $('boot').classList.add('done');
  setState('standby');
  updateSubsystems();
  addLog('system', 'JARVIS online.');
  await sleep(400);
  speakLine(greeting());
  if (!settings.hasApiKey) openSettings();
}

boot();
