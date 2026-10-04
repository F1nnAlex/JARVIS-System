// Speech-to-text worker: runs OpenAI's Whisper model locally with
// transformers.js (WebAssembly). Audio never leaves the computer.
// The model downloads once on first launch and is cached afterwards.

import { pipeline, env } from './vendor/transformers/transformers.js';

env.allowLocalModels = false;
env.useBrowserCache = true;
env.useWasmCache = false; // the runtime files are bundled with the app
env.backends.onnx.wasm.wasmPaths = {
  mjs: new URL('./vendor/ort/ort-wasm-simd-threaded.asyncify.mjs', self.location.href).href,
  wasm: new URL('./vendor/ort/ort-wasm-simd-threaded.asyncify.wasm', self.location.href).href,
};
env.backends.onnx.wasm.numThreads = Math.max(1, Math.min(4, (self.navigator.hardwareConcurrency || 2) - 1));

let current = { model: null, promise: null };

function load(model) {
  if (current.model === model) return current.promise;
  const files = new Map();
  const promise = pipeline('automatic-speech-recognition', model, {
    dtype: 'q8',
    device: 'wasm',
    progress_callback: (p) => {
      if (p.status !== 'progress' || !p.total) return;
      files.set(p.file, { loaded: p.loaded, total: p.total });
      let loaded = 0;
      let total = 0;
      for (const f of files.values()) { loaded += f.loaded; total += f.total; }
      self.postMessage({ type: 'progress', loaded, total });
    },
  });
  current = { model, promise };
  promise.catch(() => { if (current.promise === promise) current = { model: null, promise: null }; });
  return promise;
}

self.onmessage = async ({ data }) => {
  try {
    if (data.type === 'load') {
      await load(data.model);
      self.postMessage({ type: 'ready', model: data.model });
    } else if (data.type === 'transcribe') {
      const pipe = await load(data.model);
      const started = performance.now();
      const result = await pipe(data.audio);
      self.postMessage({
        type: 'result',
        id: data.id,
        text: (result.text || '').trim(),
        ms: Math.round(performance.now() - started),
      });
    }
  } catch (err) {
    self.postMessage({ type: 'error', id: data.id, message: err?.message || String(err) });
  }
};
