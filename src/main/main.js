'use strict';

const path = require('path');
const os = require('os');
const { pathToFileURL } = require('url');
const {
  app, BrowserWindow, ipcMain, protocol, net, shell, session, globalShortcut, systemPreferences,
} = require('electron');

const settings = require('./settings');
const { Brain } = require('./brain');

const ROOT = path.join(__dirname, '..', '..');

// The window is served from app://jarvis/ rather than file:// so it gets a
// secure origin: the speech model can then cache itself between launches.
const ROUTES = [
  ['/vendor/transformers/', path.join(ROOT, 'node_modules', '@huggingface', 'transformers', 'dist')],
  ['/vendor/ort/', path.join(ROOT, 'node_modules', 'onnxruntime-web', 'dist')],
  ['/', path.join(ROOT, 'src', 'renderer')],
];

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'app',
    privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
  },
]);

function resolveAppUrl(url) {
  const { pathname } = new URL(url);
  const decoded = decodeURIComponent(pathname);
  for (const [prefix, dir] of ROUTES) {
    if (!decoded.startsWith(prefix)) continue;
    const target = path.normalize(path.join(dir, decoded.slice(prefix.length) || 'index.html'));
    if (target !== dir && !target.startsWith(dir + path.sep)) return null; // no escaping the folder
    return target;
  }
  return null;
}

const MIME = {
  '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
  '.wasm': 'application/wasm', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
};

async function handleAppProtocol(request) {
  const file = resolveAppUrl(request.url);
  if (!file) return new Response('Not found', { status: 404 });
  const upstream = await net.fetch(pathToFileURL(file).toString());
  if (!upstream.ok) return new Response('Not found', { status: 404 });
  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': MIME[path.extname(file)] || 'application/octet-stream',
      // Cross-origin isolation lets the speech model use multiple CPU threads.
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp',
      'Cross-Origin-Resource-Policy': 'same-origin',
    },
  });
}

let win = null;
const brain = new Brain({ getApiKey: settings.apiKey, getSettings: settings.load });

function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    frame: false,
    backgroundColor: '#02060c',
    title: 'J.A.R.V.I.S.',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
      autoplayPolicy: 'no-user-gesture-required',
    },
  });

  win.once('ready-to-show', () => win.show());
  win.loadURL('app://jarvis/index.html');

  // Links open in the user's browser, never inside JARVIS.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event) => event.preventDefault());

  win.on('enter-full-screen', () => win.webContents.send('jarvis:window', { fullscreen: true }));
  win.on('leave-full-screen', () => win.webContents.send('jarvis:window', { fullscreen: false }));
  win.on('closed', () => { win = null; });
}

// Rolling CPU usage from os.cpus() deltas.
let lastCpu = os.cpus();
function cpuUsage() {
  const now = os.cpus();
  let idle = 0;
  let total = 0;
  now.forEach((cpu, i) => {
    const prev = lastCpu[i] ? lastCpu[i].times : { user: 0, nice: 0, sys: 0, idle: 0, irq: 0 };
    for (const key of Object.keys(cpu.times)) total += cpu.times[key] - prev[key];
    idle += cpu.times.idle - prev.idle;
  });
  lastCpu = now;
  return total > 0 ? 1 - idle / total : 0;
}

function registerIpc() {
  ipcMain.handle('jarvis:settings:get', () => settings.publicView());
  ipcMain.handle('jarvis:settings:save', (_event, patch) => {
    const allowed = {};
    for (const [key, value] of Object.entries(patch || {})) {
      if (key in settings.DEFAULTS) allowed[key] = value;
    }
    settings.save(allowed);
    return settings.publicView();
  });

  ipcMain.handle('jarvis:system', () => ({
    cpu: cpuUsage(),
    cores: os.cpus().length,
    cpuModel: (os.cpus()[0] || {}).model || 'Unknown CPU',
    memTotal: os.totalmem(),
    memFree: os.freemem(),
    uptime: os.uptime(),
    hostname: os.hostname(),
    platform: `${os.type()} ${os.release()}`,
    arch: os.arch(),
    appVersion: app.getVersion(),
  }));

  ipcMain.on('jarvis:ask', (event, { id, text, context }) => {
    const sender = event.sender;
    brain.ask(String(text || ''), context || {}, (payload) => {
      if (!sender.isDestroyed()) sender.send('jarvis:brain', { id, ...payload });
    });
  });
  ipcMain.on('jarvis:cancel', () => brain.cancel());
  ipcMain.on('jarvis:reset', () => brain.reset());

  ipcMain.on('jarvis:window:control', (_event, action) => {
    if (!win) return;
    if (action === 'minimize') win.minimize();
    if (action === 'maximize') (win.isMaximized() ? win.unmaximize() : win.maximize());
    if (action === 'fullscreen') win.setFullScreen(!win.isFullScreen());
    if (action === 'close') win.close();
  });
  ipcMain.on('jarvis:open-external', (_event, url) => {
    if (typeof url === 'string' && url.startsWith('https://')) shell.openExternal(url);
  });
}

app.whenReady().then(async () => {
  protocol.handle('app', handleAppProtocol);

  if (process.platform === 'darwin') {
    await systemPreferences.askForMediaAccess('microphone').catch(() => false);
  }

  // Only the microphone (and audio playback) is ever granted.
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    callback(permission === 'media' || permission === 'speaker-selection');
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media');

  registerIpc();
  createWindow();

  // Summon JARVIS from anywhere: Ctrl+Shift+J (Cmd+Shift+J on macOS).
  globalShortcut.register('CommandOrControl+Shift+J', () => {
    if (!win) createWindow();
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    win.webContents.send('jarvis:summon');
  });

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
