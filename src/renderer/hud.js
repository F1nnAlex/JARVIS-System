// The animated visuals: the arc reactor in the centre and the backdrop.
// Everything is drawn on 2D canvases every animation frame.

const TAU = Math.PI * 2;

const PALETTE = {
  standby: { main: [63, 216, 255], accent: [63, 216, 255] },
  listening: { main: [120, 230, 255], accent: [217, 247, 255] },
  transcribing: { main: [63, 216, 255], accent: [255, 182, 72] },
  thinking: { main: [63, 216, 255], accent: [255, 182, 72] },
  speaking: { main: [90, 224, 255], accent: [217, 247, 255] },
  error: { main: [255, 77, 90], accent: [255, 140, 120] },
  boot: { main: [63, 216, 255], accent: [63, 216, 255] },
  muted: { main: [70, 120, 150], accent: [110, 150, 170] },
};

const rgba = ([r, g, b], a) => `rgba(${r | 0},${g | 0},${b | 0},${a})`;
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);

function fitCanvas(canvas) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const { width, height } = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(width * dpr));
  const h = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  return { w, h, dpr };
}

export class Reactor {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.state = 'boot';
    this.colorMain = [...PALETTE.boot.main];
    this.colorAccent = [...PALETTE.boot.accent];
    this.level = 0; // smoothed 0..1 audio level
    this.targetLevel = 0;
    this.spectrum = new Float32Array(96);
    this.power = 0; // 0..1 spin-up during boot
    this.spin = 0;
    this.spinSpeed = 0.2;
    this.levelSource = () => ({ level: 0, spectrum: null });
    this.last = performance.now();
  }

  setState(state) {
    this.state = state;
  }

  setLevelSource(fn) {
    this.levelSource = fn;
  }

  frame(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    const { w, h } = fitCanvas(this.canvas);
    const ctx = this.ctx;
    const cx = w / 2;
    const cy = h / 2;
    const R = Math.min(w, h) * 0.46;

    // Ease colours, power and spin toward the current state.
    const palette = PALETTE[this.state] || PALETTE.standby;
    this.colorMain = mix(this.colorMain, palette.main, Math.min(1, dt * 4));
    this.colorAccent = mix(this.colorAccent, palette.accent, Math.min(1, dt * 4));
    const targetPower = this.state === 'boot' ? this.power : this.state === 'muted' ? 0.55 : 1;
    this.power += (targetPower - this.power) * Math.min(1, dt * 1.5);
    const targetSpin = { thinking: 2.6, transcribing: 1.8, listening: 0.6, speaking: 0.8 }[this.state] ?? 0.25;
    this.spinSpeed += (targetSpin - this.spinSpeed) * Math.min(1, dt * 3);
    this.spin += this.spinSpeed * dt;

    const { level, spectrum } = this.levelSource();
    this.targetLevel = level || 0;
    const k = this.targetLevel > this.level ? 18 : 5;
    this.level += (this.targetLevel - this.level) * Math.min(1, dt * k);
    this.#updateSpectrum(spectrum, dt, now);

    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.globalCompositeOperation = 'lighter';

    const p = this.power;
    const main = this.colorMain;
    const accent = this.colorAccent;
    const t = now / 1000;

    // Soft ambient halo.
    const halo = ctx.createRadialGradient(0, 0, R * 0.1, 0, 0, R * 1.1);
    halo.addColorStop(0, rgba(main, 0.16 * p + this.level * 0.12));
    halo.addColorStop(0.5, rgba(main, 0.05 * p));
    halo.addColorStop(1, rgba(main, 0));
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(0, 0, R * 1.1, 0, TAU);
    ctx.fill();

    this.#tickRing(R * 0.985, main, p, this.spin * 0.15);
    this.#segmentRing(R * 0.92, R * 0.012, main, p, -this.spin * 0.35, [0.0, 0.22, 0.3, 0.47, 0.55, 0.8, 0.86, 0.97]);
    this.#textRing(R * 0.86, main, p, this.spin * 0.08);

    if (this.state === 'thinking' || this.state === 'transcribing') {
      this.#sweep(R * 0.86, accent, t);
    }

    this.#spectrumRing(R * 0.6, R * 0.2, main, accent, p);
    this.#dashedRing(R * 0.58, main, p * 0.6, this.spin * 0.6);
    this.#segmentRing(R * 0.535, R * 0.01, accent, p * 0.8, this.spin * 1.2, [0.05, 0.2, 0.3, 0.45, 0.55, 0.7, 0.8, 0.95]);
    this.#coil(R * 0.32, R * 0.48, main, p);
    this.#core(R * 0.3, main, accent, p, t);

    ctx.restore();
  }

  #updateSpectrum(source, dt, now) {
    const n = this.spectrum.length;
    for (let i = 0; i < n; i++) {
      let target;
      if (source && source.length) {
        // Mirror the low half of the spectrum around the ring.
        const half = n / 2;
        const j = i < half ? i : n - 1 - i;
        const idx = Math.floor((j / half) * source.length * 0.7);
        target = source[idx] || 0;
      } else {
        // No real spectrum (e.g. while speaking): synthesise a lively one.
        const wobble = Math.sin(i * 0.9 + now * 0.011) * Math.sin(i * 0.37 - now * 0.007);
        target = this.level * (0.55 + 0.45 * Math.abs(wobble));
      }
      const idle = 0.04 + 0.03 * Math.sin(i * 0.5 + now * 0.002);
      target = Math.max(idle * this.power, target);
      const cur = this.spectrum[i];
      this.spectrum[i] = cur + (target - cur) * Math.min(1, dt * (target > cur ? 20 : 6));
    }
  }

  #tickRing(r, color, p, rot) {
    const ctx = this.ctx;
    const count = 144;
    ctx.save();
    ctx.rotate(rot);
    ctx.lineWidth = Math.max(1, r * 0.004);
    for (let i = 0; i < count; i++) {
      const a = (i / count) * TAU;
      const long = i % 12 === 0;
      const len = long ? r * 0.05 : r * 0.022;
      ctx.strokeStyle = rgba(color, (long ? 0.8 : 0.35) * p);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
      ctx.lineTo(Math.cos(a) * (r - len), Math.sin(a) * (r - len));
      ctx.stroke();
    }
    ctx.restore();
  }

  #segmentRing(r, width, color, p, rot, stops) {
    const ctx = this.ctx;
    ctx.save();
    ctx.rotate(rot);
    ctx.lineWidth = width;
    ctx.lineCap = 'butt';
    ctx.shadowColor = rgba(color, 0.9);
    ctx.shadowBlur = width * 2.5;
    ctx.strokeStyle = rgba(color, 0.75 * p);
    for (let i = 0; i < stops.length; i += 2) {
      ctx.beginPath();
      ctx.arc(0, 0, r, stops[i] * TAU, stops[i + 1] * TAU);
      ctx.stroke();
    }
    ctx.restore();
  }

  #dashedRing(r, color, alpha, rot) {
    const ctx = this.ctx;
    ctx.save();
    ctx.rotate(rot);
    ctx.setLineDash([r * 0.02, r * 0.03]);
    ctx.lineWidth = Math.max(1, r * 0.006);
    ctx.strokeStyle = rgba(color, alpha);
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, TAU);
    ctx.stroke();
    ctx.restore();
  }

  #textRing(r, color, p, rot) {
    const ctx = this.ctx;
    const label = 'J.A.R.V.I.S. • NEURAL INTERFACE ONLINE • VOICE CORE ACTIVE • ';
    ctx.save();
    ctx.rotate(rot);
    ctx.font = `${Math.max(9, r * 0.034)}px Bahnschrift, "Segoe UI", sans-serif`;
    ctx.fillStyle = rgba(color, 0.45 * p);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const chars = label.split('');
    const span = TAU * 0.42; // text arc occupies part of the ring
    for (let side = 0; side < 2; side++) {
      for (let i = 0; i < chars.length; i++) {
        const a = side * Math.PI + (i / chars.length) * span - span / 2;
        ctx.save();
        ctx.rotate(a);
        ctx.translate(0, -r);
        ctx.fillText(chars[i], 0, 0);
        ctx.restore();
      }
    }
    ctx.restore();
  }

  #sweep(r, color, t) {
    const ctx = this.ctx;
    const start = (t * 3.2) % TAU;
    ctx.save();
    ctx.lineWidth = r * 0.03;
    ctx.shadowColor = rgba(color, 1);
    ctx.shadowBlur = r * 0.08;
    for (let i = 0; i < 2; i++) {
      const s = start + i * Math.PI;
      const grad = ctx.createConicGradient(s, 0, 0);
      grad.addColorStop(0, rgba(color, 0));
      grad.addColorStop(0.18, rgba(color, 0.9));
      grad.addColorStop(0.181, rgba(color, 0));
      grad.addColorStop(1, rgba(color, 0));
      ctx.strokeStyle = grad;
      ctx.beginPath();
      ctx.arc(0, 0, r, s, s + TAU * 0.18);
      ctx.stroke();
    }
    ctx.restore();
  }

  #spectrumRing(r, maxLen, main, accent, p) {
    const ctx = this.ctx;
    const n = this.spectrum.length;
    ctx.save();
    ctx.lineWidth = Math.max(1.5, ((TAU * r) / n) * 0.45);
    ctx.lineCap = 'round';
    for (let i = 0; i < n; i++) {
      const v = Math.min(1, this.spectrum[i]);
      const a = (i / n) * TAU - Math.PI / 2;
      const len = r * 0.02 + v * maxLen;
      const color = mix(main, accent, Math.min(1, v * 1.4));
      ctx.strokeStyle = rgba(color, (0.35 + v * 0.65) * p);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
      ctx.lineTo(Math.cos(a) * (r + len), Math.sin(a) * (r + len));
      ctx.stroke();
    }
    ctx.restore();
  }

  #coil(r0, r1, color, p) {
    // The ten copper-wound segments of the classic arc reactor.
    const ctx = this.ctx;
    const segments = 10;
    const gap = 0.06;
    ctx.save();
    ctx.rotate(-Math.PI / 2 + this.spin * 0.05);
    for (let i = 0; i < segments; i++) {
      const a0 = (i / segments) * TAU + gap;
      const a1 = ((i + 1) / segments) * TAU - gap;
      const grad = ctx.createRadialGradient(0, 0, r0, 0, 0, r1);
      grad.addColorStop(0, rgba(color, 0.55 * p + this.level * 0.3));
      grad.addColorStop(1, rgba(color, 0.12 * p));
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(0, 0, r1, a0 + 0.015, a1 - 0.015);
      ctx.arc(0, 0, r0, a1, a0, true);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = rgba(color, 0.8 * p);
      ctx.lineWidth = Math.max(1, r1 * 0.012);
      ctx.stroke();
      // Winding lines across each segment.
      ctx.strokeStyle = rgba(color, 0.22 * p);
      ctx.lineWidth = Math.max(1, r1 * 0.006);
      for (let k = 1; k < 6; k++) {
        const a = a0 + ((a1 - a0) * k) / 6;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * (r0 + 2), Math.sin(a) * (r0 + 2));
        ctx.lineTo(Math.cos(a) * (r1 - 2), Math.sin(a) * (r1 - 2));
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  #core(r, main, accent, p, t) {
    const ctx = this.ctx;
    const pulse = 0.5 + 0.5 * Math.sin(t * (this.state === 'speaking' ? 0 : 1.8));
    const glow = Math.min(1, 0.55 * p + this.level * 0.7 + pulse * 0.08 * p);
    const coreColor = mix(main, accent, 0.5);

    ctx.save();
    ctx.shadowColor = rgba(main, 1);
    ctx.shadowBlur = r * (0.6 + this.level);
    ctx.strokeStyle = rgba(main, 0.9 * p);
    ctx.lineWidth = r * 0.07;
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.92, 0, TAU);
    ctx.stroke();
    ctx.restore();

    const grad = ctx.createRadialGradient(0, 0, 0, 0, 0, r * 0.85);
    grad.addColorStop(0, rgba([255, 255, 255], glow));
    grad.addColorStop(0.35, rgba(mix(coreColor, [255, 255, 255], 0.5), glow * 0.85));
    grad.addColorStop(0.75, rgba(main, glow * 0.35));
    grad.addColorStop(1, rgba(main, 0));
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(0, 0, r * 0.85, 0, TAU);
    ctx.fill();

    // Inner ring of small lights.
    ctx.save();
    ctx.rotate(-this.spin * 0.4);
    for (let i = 0; i < 18; i++) {
      const a = (i / 18) * TAU;
      ctx.fillStyle = rgba(accent, (0.35 + 0.65 * ((i + Math.floor(t * 6)) % 18 === 0 ? 1 : 0.3)) * p);
      ctx.beginPath();
      ctx.arc(Math.cos(a) * r * 0.62, Math.sin(a) * r * 0.62, r * 0.025, 0, TAU);
      ctx.fill();
    }
    ctx.restore();
  }
}

export class Backdrop {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.grid = null;
    this.gridKey = '';
    this.particles = Array.from({ length: 70 }, () => ({
      x: Math.random(),
      y: Math.random(),
      vx: (Math.random() - 0.5) * 0.006,
      vy: (Math.random() - 0.5) * 0.006,
      r: Math.random() * 1.4 + 0.3,
      a: Math.random() * 0.5 + 0.1,
    }));
    this.last = performance.now();
  }

  #buildGrid(w, h, dpr) {
    const off = document.createElement('canvas');
    off.width = w;
    off.height = h;
    const g = off.getContext('2d');
    const bg = g.createRadialGradient(w / 2, h * 0.45, 0, w / 2, h * 0.45, Math.max(w, h) * 0.75);
    bg.addColorStop(0, '#06192a');
    bg.addColorStop(0.55, '#030c16');
    bg.addColorStop(1, '#010409');
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);

    // Hexagon grid, faded toward the edges.
    const size = 26 * dpr;
    const hexW = Math.sqrt(3) * size;
    const hexH = 1.5 * size;
    g.lineWidth = 1;
    for (let row = -1; row * hexH < h + size; row++) {
      for (let col = -1; col * hexW < w + hexW; col++) {
        const x = col * hexW + (row % 2 ? hexW / 2 : 0);
        const y = row * hexH;
        const dx = (x - w / 2) / (w / 2);
        const dy = (y - h * 0.45) / (h / 2);
        const fade = Math.max(0, 1 - Math.sqrt(dx * dx + dy * dy) * 0.8);
        g.strokeStyle = `rgba(63,216,255,${0.05 * fade + 0.012})`;
        g.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = (Math.PI / 3) * i + Math.PI / 6;
          const px = x + Math.cos(a) * size * 0.96;
          const py = y + Math.sin(a) * size * 0.96;
          if (i === 0) g.moveTo(px, py);
          else g.lineTo(px, py);
        }
        g.closePath();
        g.stroke();
      }
    }
    return off;
  }

  frame(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    const { w, h, dpr } = fitCanvas(this.canvas);
    const key = `${w}x${h}`;
    if (key !== this.gridKey) {
      this.grid = this.#buildGrid(w, h, dpr);
      this.gridKey = key;
    }
    const ctx = this.ctx;
    ctx.drawImage(this.grid, 0, 0);

    // Drifting motes.
    for (const pt of this.particles) {
      pt.x = (pt.x + pt.vx * dt + 1) % 1;
      pt.y = (pt.y + pt.vy * dt + 1) % 1;
      ctx.fillStyle = `rgba(120,225,255,${pt.a})`;
      ctx.beginPath();
      ctx.arc(pt.x * w, pt.y * h, pt.r * dpr, 0, TAU);
      ctx.fill();
    }

    // Slow scanning line.
    const y = ((now / 9000) % 1) * h;
    const scan = ctx.createLinearGradient(0, y - 60 * dpr, 0, y);
    scan.addColorStop(0, 'rgba(63,216,255,0)');
    scan.addColorStop(1, 'rgba(63,216,255,0.05)');
    ctx.fillStyle = scan;
    ctx.fillRect(0, y - 60 * dpr, w, 60 * dpr);
  }
}

export function drawSparkline(canvas, values, color = 'rgba(63,216,255,') {
  const { w, h } = fitCanvas(canvas);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, w, h);
  if (values.length < 2) return;
  const step = w / (values.length - 1);
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = i * step;
    const y = h - 2 - v * (h - 4);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = `${color}0.9)`;
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  const fill = ctx.createLinearGradient(0, 0, 0, h);
  fill.addColorStop(0, `${color}0.25)`);
  fill.addColorStop(1, `${color}0)`);
  ctx.fillStyle = fill;
  ctx.fill();
}
