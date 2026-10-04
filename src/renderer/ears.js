// JARVIS's ears: microphone capture, voice-activity detection, and
// speech-to-text with a local Whisper model (see stt-worker.js).

const SAMPLE_RATE = 16000;
const FRAME_MS = 32; // 512 samples per frame
const PRE_ROLL_FRAMES = 10; // keep ~320 ms before speech starts
const MIN_SPEECH_MS = 350;
const MAX_SPEECH_MS = 20000;

// Whisper sometimes "hears" these in silence or background noise.
const HALLUCINATIONS = new Set([
  '', 'you', 'thank you', 'thanks', 'thank you very much', 'thanks for watching', 'bye',
  'okay', 'oh', 'uh', 'um', 'hmm', 'so', 'the', 'i', 'a',
]);

export function cleanTranscript(text) {
  const stripped = String(text || '')
    .replace(/\[[^\]]*\]|\([^)]*\)|\*[^*]*\*/g, ' ') // [BLANK_AUDIO], (music), *cough*
    .replace(/\s+/g, ' ')
    .trim();
  const bare = stripped.toLowerCase().replace(/[^a-z' ]/g, '').trim();
  return HALLUCINATIONS.has(bare) ? '' : stripped;
}

export class Ears extends EventTarget {
  constructor() {
    super();
    this.model = 'onnx-community/whisper-base.en';
    this.sensitivity = 0.5;
    this.paused = false; // ignore the mic (e.g. while JARVIS speaks)
    this.muted = false;
    this.micReady = false;
    this.modelReady = false;
    this.inSpeech = false;
    this.manual = false;
    this.noiseFloor = 0.003;
    this.preRoll = [];
    this.segment = [];
    this.loudFrames = 0;
    this.silentFrames = 0;
    this.level = 0;
    this.freq = null;
    this.pending = new Map();
    this.nextId = 1;

    this.worker = new Worker(new URL('./stt-worker.js', import.meta.url), { type: 'module' });
    this.worker.onmessage = ({ data }) => this.#onWorkerMessage(data);
    this.worker.onerror = (e) => this.#emit('model-error', { message: e.message || 'Speech worker failed to start' });
  }

  #emit(type, detail = {}) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  loadModel(model) {
    if (model) this.model = model;
    this.modelReady = false;
    this.worker.postMessage({ type: 'load', model: this.model });
  }

  #onWorkerMessage(data) {
    if (data.type === 'progress') {
      this.#emit('model-progress', { fraction: data.total ? data.loaded / data.total : 0 });
    } else if (data.type === 'ready') {
      if (data.model === this.model) {
        this.modelReady = true;
        this.#emit('model-ready', { model: data.model });
      }
    } else if (data.type === 'result' || data.type === 'error') {
      const job = this.pending.get(data.id);
      if (data.id === undefined && data.type === 'error') {
        this.#emit('model-error', { message: data.message });
        return;
      }
      if (!job) return;
      this.pending.delete(data.id);
      if (data.type === 'result') job.resolve(data.text);
      else job.reject(new Error(data.message));
    }
  }

  transcribe(audio) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ type: 'transcribe', id, model: this.model, audio }, [audio.buffer]);
    });
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
    await this.ctx.audioWorklet.addModule(new URL('./capture-processor.js', import.meta.url));
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.6;
    this.freq = new Uint8Array(this.analyser.frequencyBinCount);
    const node = new AudioWorkletNode(this.ctx, 'capture-processor');
    node.port.onmessage = ({ data }) => this.#onFrame(data);
    source.connect(this.analyser);
    source.connect(node);
    // Keep the graph running without playing the mic back.
    const sink = this.ctx.createGain();
    sink.gain.value = 0;
    node.connect(sink).connect(this.ctx.destination);
    this.micReady = true;
    this.stream.getAudioTracks()[0].addEventListener('ended', () => {
      this.micReady = false;
      this.#emit('mic-lost');
    });
  }

  // Current input level and spectrum for the HUD (0..1).
  visuals() {
    if (!this.analyser || this.muted) return { level: 0, spectrum: null };
    this.analyser.getByteFrequencyData(this.freq);
    const spectrum = Array.from(this.freq, (v) => v / 255);
    return { level: Math.min(1, this.level * 9), spectrum };
  }

  setMuted(muted) {
    this.muted = muted;
    if (this.stream) this.stream.getAudioTracks().forEach((t) => { t.enabled = !muted; });
    if (muted) this.#reset();
  }

  setPaused(paused) {
    this.paused = paused;
    if (paused && !this.manual) this.#reset();
  }

  // Push-to-talk: record the next utterance regardless of the wake word.
  listenNow() {
    this.manual = true;
    this.paused = false;
    this.#reset();
    this.manualDeadline = performance.now() + 8000; // give up if nothing is said
  }

  cancelManual() {
    this.manual = false;
  }

  #reset() {
    this.inSpeech = false;
    this.segment = [];
    this.loudFrames = 0;
    this.silentFrames = 0;
  }

  #thresholds() {
    // Higher sensitivity = quieter speech triggers recording.
    const s = Math.min(1, Math.max(0, this.sensitivity));
    const ratio = 4.2 - s * 2.6; // 4.2x .. 1.6x the noise floor
    const minimum = 0.018 - s * 0.014; // absolute RMS floor
    return Math.max(minimum, this.noiseFloor * ratio);
  }

  #onFrame(frame) {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / frame.length);
    this.level = rms;
    if (this.muted) return;

    if (this.manual && !this.inSpeech && performance.now() > this.manualDeadline) {
      this.manual = false;
      this.#emit('manual-timeout');
    }
    if (this.paused && !this.manual) return;

    const threshold = this.#thresholds();
    const loud = rms > threshold;

    if (!this.inSpeech) {
      // Track background noise only while nobody is talking.
      this.noiseFloor = this.noiseFloor * 0.97 + Math.min(rms, 0.05) * 0.03;
      this.preRoll.push(frame);
      if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
      this.loudFrames = loud ? this.loudFrames + 1 : 0;
      if (this.loudFrames >= 3) {
        this.inSpeech = true;
        this.segment = [...this.preRoll];
        this.preRoll = [];
        this.silentFrames = 0;
        this.#emit('speech-start', { manual: this.manual });
      }
      return;
    }

    this.segment.push(frame);
    this.silentFrames = loud ? 0 : this.silentFrames + 1;
    const silenceLimit = (this.manual ? 1100 : 750) / FRAME_MS;
    const duration = this.segment.length * FRAME_MS;
    if (this.silentFrames >= silenceLimit || duration >= MAX_SPEECH_MS) {
      const frames = this.segment;
      const manual = this.manual;
      this.#reset();
      this.manual = false;
      const speechMs = duration - this.silentFrames * FRAME_MS;
      if (speechMs < MIN_SPEECH_MS) {
        this.#emit('speech-discarded', { manual });
        return;
      }
      const audio = new Float32Array(frames.length * 512);
      frames.forEach((f, i) => audio.set(f, i * 512));
      this.#emit('utterance', { audio, manual });
    }
  }
}
