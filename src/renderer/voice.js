// JARVIS's voice: text-to-speech with the operating system's voices.
// Text streams in from Claude; each finished sentence is spoken as soon as
// it arrives, so JARVIS starts talking before the full reply is written.

// Preferred voices, best first: British male voices that suit JARVIS.
const PREFERRED = [
  /Ryan.*(Natural|Online)/i,
  /Thomas.*(Natural|Online)/i,
  /George/i, // Windows: Microsoft George - English (United Kingdom)
  /Daniel/i, // macOS British voice
  /Arthur/i,
  /Oliver/i,
  /UK English Male/i,
  /Ryan/i,
];

export function cleanForSpeech(text) {
  return text
    .replace(/https?:\/\/\S+/g, '')
    .replace(/[*_#`>~|]/g, '')
    .replace(/\[(.*?)\]\(.*?\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

// Split off complete sentences from a growing buffer.
export function takeSentences(buffer, final = false) {
  const sentences = [];
  let rest = buffer;
  const boundary = /([.!?…]+["')\]]?)(\s+)|(\n+)/g;
  let lastCut = 0;
  let match;
  while ((match = boundary.exec(buffer)) !== null) {
    const end = match.index + (match[1] ? match[1].length : 0);
    const candidate = buffer.slice(lastCut, end).trim();
    // Avoid cutting after abbreviations like "Mr." or "e.g.".
    if (match[1] && /\b(Mr|Mrs|Ms|Dr|St|vs|etc|e\.g|i\.e|approx|No)\.$/i.test(candidate)) continue;
    if (candidate.length >= 2) sentences.push(candidate);
    lastCut = match.index + match[0].length;
  }
  rest = buffer.slice(lastCut);
  if (final && rest.trim()) {
    sentences.push(rest.trim());
    rest = '';
  }
  return { sentences, rest };
}

export class Voice extends EventTarget {
  constructor() {
    super();
    this.synth = window.speechSynthesis;
    this.voices = [];
    this.voice = null;
    this.preferredName = '';
    this.rate = 1.0;
    this.pitch = 0.9;
    this.volume = 1.0;
    this.queue = [];
    this.buffer = '';
    this.streamOpen = false;
    this.speaking = false;
    this.current = null;
    this.watchdog = null;
    this.pulse = 0;
    this.pulseAt = 0;

    this.#loadVoices();
    if (this.synth) this.synth.addEventListener('voiceschanged', () => this.#loadVoices());
  }

  #emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  get available() {
    return Boolean(this.synth) && this.voices.length > 0;
  }

  #loadVoices() {
    if (!this.synth) return;
    this.voices = this.synth.getVoices();
    this.#pickVoice();
    this.#emit('voices', { voices: this.voices });
  }

  setPreferences({ voiceName, voiceRate, voicePitch, voiceVolume }) {
    if (voiceName !== undefined) this.preferredName = voiceName;
    if (voiceRate !== undefined) this.rate = Number(voiceRate) || 1;
    if (voicePitch !== undefined) this.pitch = Number(voicePitch) || 1;
    if (voiceVolume !== undefined) this.volume = Number(voiceVolume);
    this.#pickVoice();
  }

  #pickVoice() {
    const voices = this.voices;
    if (!voices.length) {
      this.voice = null;
      return;
    }
    const exact = voices.find((v) => v.name === this.preferredName);
    if (exact) {
      this.voice = exact;
      return;
    }
    const english = voices.filter((v) => /^en/i.test(v.lang));
    for (const pattern of PREFERRED) {
      const found = english.find((v) => pattern.test(v.name)) || voices.find((v) => pattern.test(v.name));
      if (found) {
        this.voice = found;
        return;
      }
    }
    this.voice = english.find((v) => /en[-_]GB/i.test(v.lang)) || english[0] || voices[0];
  }

  // Begin a new streamed reply.
  beginStream() {
    this.cancel();
    this.streamOpen = true;
    this.buffer = '';
  }

  pushText(text) {
    this.buffer += text;
    const { sentences, rest } = takeSentences(this.buffer);
    this.buffer = rest;
    sentences.forEach((s) => this.#enqueue(s));
  }

  endStream() {
    const { sentences } = takeSentences(this.buffer, true);
    this.buffer = '';
    sentences.forEach((s) => this.#enqueue(s));
    this.streamOpen = false;
    this.#checkIdle();
  }

  // Speak a complete line (greetings, acknowledgements, errors).
  say(text) {
    this.beginStream();
    this.pushText(text);
    this.endStream();
  }

  cancel() {
    this.queue = [];
    this.buffer = '';
    this.streamOpen = false;
    clearTimeout(this.watchdog);
    const wasSpeaking = this.speaking;
    this.speaking = false;
    this.current = null;
    if (this.synth) this.synth.cancel();
    if (wasSpeaking) this.#emit('idle', { cancelled: true });
  }

  // 0..1 loudness estimate for the HUD; speech synthesis exposes no audio,
  // so word-boundary events drive a decaying pulse.
  visuals() {
    if (!this.speaking) return { level: 0, spectrum: null };
    const since = (performance.now() - this.pulseAt) / 1000;
    const decay = Math.max(0, 1 - since * 3.5);
    const flutter = 0.25 + 0.15 * Math.sin(performance.now() / 70);
    return { level: Math.min(1, flutter + decay * 0.6), spectrum: null };
  }

  #enqueue(sentence) {
    const text = cleanForSpeech(sentence);
    if (!text) return;
    this.queue.push(text);
    if (!this.current) this.#next();
  }

  #next() {
    clearTimeout(this.watchdog);
    const text = this.queue.shift();
    if (!text) {
      this.current = null;
      this.#checkIdle();
      return;
    }
    if (!this.speaking) {
      this.speaking = true;
      this.#emit('start');
    }
    this.#emit('sentence', { text });

    if (!this.synth) {
      // No speech engine at all: pace the captions by reading speed.
      this.current = { text };
      this.watchdog = setTimeout(() => this.#next(), 400 + text.length * 55);
      return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    if (this.voice) utterance.voice = this.voice;
    utterance.rate = this.rate;
    utterance.pitch = this.pitch;
    utterance.volume = this.volume;
    utterance.onboundary = () => { this.pulseAt = performance.now(); };
    utterance.onstart = () => { this.pulseAt = performance.now(); };
    const done = () => {
      if (this.current !== utterance) return;
      this.current = null;
      this.#next();
    };
    utterance.onend = done;
    utterance.onerror = done;
    this.current = utterance;
    this.synth.speak(utterance);

    // Some engines never fire "end" (or have no voices): don't hang forever.
    const words = text.split(/\s+/).length;
    const expected = (1200 + words * 520) / Math.max(0.5, this.rate);
    this.watchdog = setTimeout(() => {
      if (this.current === utterance) {
        this.synth.cancel();
        done();
      }
    }, expected * 2 + 2000);
  }

  #checkIdle() {
    if (this.speaking && !this.current && !this.queue.length && !this.streamOpen) {
      this.speaking = false;
      this.#emit('idle', { cancelled: false });
    } else if (!this.speaking && !this.streamOpen && !this.current && !this.queue.length) {
      this.#emit('idle', { cancelled: false, silent: true });
    }
  }
}
