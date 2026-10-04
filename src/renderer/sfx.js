// Small synthesised interface sounds (no audio files needed).

let ctx = null;

function audio() {
  if (!ctx) ctx = new AudioContext();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

function tone({ freq, to = freq, start = 0, duration = 0.12, type = 'sine', gain = 0.08 }) {
  const ac = audio();
  const t0 = ac.currentTime + start;
  const osc = ac.createOscillator();
  const amp = ac.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t0);
  osc.frequency.exponentialRampToValueAtTime(to, t0 + duration);
  amp.gain.setValueAtTime(0.0001, t0);
  amp.gain.exponentialRampToValueAtTime(gain, t0 + 0.012);
  amp.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  osc.connect(amp).connect(ac.destination);
  osc.start(t0);
  osc.stop(t0 + duration + 0.02);
}

export const sfx = {
  enabled: true,
  boot() {
    if (!this.enabled) return;
    tone({ freq: 110, to: 880, duration: 1.4, type: 'sawtooth', gain: 0.025 });
    tone({ freq: 220, to: 1320, duration: 1.4, type: 'sine', gain: 0.04 });
    tone({ freq: 1320, start: 1.35, duration: 0.25, gain: 0.05 });
  },
  wake() {
    if (!this.enabled) return;
    tone({ freq: 880, duration: 0.09, gain: 0.06 });
    tone({ freq: 1320, start: 0.08, duration: 0.14, gain: 0.06 });
  },
  listen() {
    if (!this.enabled) return;
    tone({ freq: 660, to: 990, duration: 0.12, gain: 0.05 });
  },
  processing() {
    if (!this.enabled) return;
    tone({ freq: 1200, duration: 0.05, type: 'triangle', gain: 0.03 });
    tone({ freq: 1500, start: 0.06, duration: 0.05, type: 'triangle', gain: 0.03 });
  },
  cancel() {
    if (!this.enabled) return;
    tone({ freq: 700, to: 350, duration: 0.18, gain: 0.05 });
  },
  error() {
    if (!this.enabled) return;
    tone({ freq: 220, duration: 0.18, type: 'square', gain: 0.03 });
    tone({ freq: 165, start: 0.2, duration: 0.28, type: 'square', gain: 0.03 });
  },
};
