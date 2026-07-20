'use strict';

/**
 * Electron main process. Owns exactly one window and two bits of native
 * capability the renderer can't reach on its own (a folder picker, and
 * safe external-link opening) — everything else (the WebSocket
 * connection to the agent server, rendering the server-driven UI tree)
 * happens in the renderer, same division of labor as ui/app.py: this
 * process never talks to the agent server itself, it only hosts the
 * window that does.
 */

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('path');

function createWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 760,
    minHeight: 480,
    backgroundColor: '#0d1117',
    title: 'Agent',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // The safe baseline: the renderer itself gets no Node access
      // (nodeIntegration: false) and no shared JS scope with main
      // (contextIsolation: true) — only preload.js runs with Node
      // access, and it exposes just a few narrow bridges (see
      // preload.js). `sandbox: false` (not the default) is what lets
      // that preload script `require('../components/js/richStyle.js')`
      // at all — Electron's sandboxed preload context only resolves a
      // small built-in module allowlist (electron, events, timers, url)
      // and can't require any local relative file, regardless of what
      // that file itself does or doesn't import.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.loadFile(path.join(__dirname, 'index.html'));

  // Any target="_blank"/window.open from rendered markdown (an answer's
  // links) opens in the user's real browser, never a second app window.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  // Surfaces renderer-side errors/warnings (a bad ui.update op, a failed
  // request) in this process's own stdout — the one place a user running
  // from a terminal can see them, matching the server's own "log
  // everything unconditionally" precedent (see README's Logging section).
  win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    if (level < 2) return; // 0=verbose/log, 1=info — only warn(2)/error(3)
    const tag = level === 3 ? 'error' : 'warn';
    console.error(`[renderer:${tag}] ${message} (${sourceId}:${line})`);
  });

  return win;
}

ipcMain.handle('pick-folder', async (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  const result = await dialog.showOpenDialog(win, {
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled || result.filePaths.length === 0) return null;
  return result.filePaths[0];
});

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
