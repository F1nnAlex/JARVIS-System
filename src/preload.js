'use strict';

// The only bridge between the HUD window and the main process.

const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel, callback) {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('jarvis', {
  getSettings: () => ipcRenderer.invoke('jarvis:settings:get'),
  saveSettings: (patch) => ipcRenderer.invoke('jarvis:settings:save', patch),
  systemStats: () => ipcRenderer.invoke('jarvis:system'),

  ask: (id, text, context) => ipcRenderer.send('jarvis:ask', { id, text, context }),
  cancel: () => ipcRenderer.send('jarvis:cancel'),
  resetConversation: () => ipcRenderer.send('jarvis:reset'),
  onBrain: (callback) => subscribe('jarvis:brain', callback),

  windowControl: (action) => ipcRenderer.send('jarvis:window:control', action),
  onWindow: (callback) => subscribe('jarvis:window', callback),
  onSummon: (callback) => subscribe('jarvis:summon', callback),
  openExternal: (url) => ipcRenderer.send('jarvis:open-external', url),
});
