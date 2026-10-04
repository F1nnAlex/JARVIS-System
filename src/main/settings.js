'use strict';

// Persists JARVIS settings as JSON in Electron's per-user data folder.
// The Anthropic API key is encrypted with the OS keychain (safeStorage)
// whenever the platform supports it.

const fs = require('fs');
const path = require('path');
const { app, safeStorage } = require('electron');

const DEFAULTS = {
  apiKey: '',
  model: 'claude-opus-5-5',
  effort: 'low',
  webSearch: true,
  userName: '',
  addressAs: '',
  location: '',
  voiceName: '',
  voiceRate: 1.0,
  voicePitch: 0.9,
  voiceVolume: 1.0,
  wakeWord: true,
  whisperModel: 'onnx-community/whisper-base.en',
  micSensitivity: 0.5,
};

function settingsFile() {
  return path.join(app.getPath('userData'), 'settings.json');
}

function encryptKey(key) {
  if (!key) return '';
  if (safeStorage.isEncryptionAvailable()) {
    return 'enc:' + safeStorage.encryptString(key).toString('base64');
  }
  return 'raw:' + key;
}

function decryptKey(stored) {
  if (!stored) return '';
  try {
    if (stored.startsWith('enc:')) {
      return safeStorage.decryptString(Buffer.from(stored.slice(4), 'base64'));
    }
    if (stored.startsWith('raw:')) return stored.slice(4);
  } catch (err) {
    console.error('[settings] could not decrypt stored API key:', err.message);
  }
  return '';
}

let cache = null;

function load() {
  if (cache) return cache;
  let stored = {};
  try {
    stored = JSON.parse(fs.readFileSync(settingsFile(), 'utf8'));
  } catch {
    // First run, or unreadable file: fall back to defaults.
  }
  const { apiKeyStored, ...rest } = stored;
  cache = { ...DEFAULTS, ...rest, apiKey: decryptKey(apiKeyStored) };
  return cache;
}

function save(patch) {
  const next = { ...load(), ...patch };
  for (const key of Object.keys(next)) {
    if (!(key in DEFAULTS)) delete next[key];
  }
  cache = next;
  const { apiKey, ...rest } = next;
  fs.mkdirSync(path.dirname(settingsFile()), { recursive: true });
  fs.writeFileSync(settingsFile(), JSON.stringify({ ...rest, apiKeyStored: encryptKey(apiKey) }, null, 2));
  return next;
}

// The key JARVIS should use: the one saved in the app, else the environment.
function apiKey() {
  return load().apiKey || process.env.ANTHROPIC_API_KEY || '';
}

// What the renderer is allowed to see: everything except the key itself.
function publicView() {
  const { apiKey: _key, ...rest } = load();
  return {
    ...rest,
    hasApiKey: Boolean(apiKey()),
    apiKeySource: load().apiKey ? 'app' : process.env.ANTHROPIC_API_KEY ? 'environment' : 'none',
  };
}

module.exports = { load, save, apiKey, publicView, DEFAULTS };
