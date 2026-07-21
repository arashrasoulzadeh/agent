'use strict';

/**
 * Electron main process. Owns exactly one window, the native
 * application menu, and a couple of native capabilities the renderer
 * can't reach on its own (a folder picker, safe external-link opening)
 * — everything else (the WebSocket connection to the agent server,
 * rendering the server-driven UI tree) happens in the renderer, same
 * division of labor as ui/app.py: this process never talks to the
 * agent server itself, it only hosts the window that does. A menu item
 * that needs the renderer to act (Settings) sends a 'menu-action' IPC
 * message instead of reaching into its state directly; everything else
 * (reload, zoom, devtools, quit, ...) is a plain Electron role this
 * process handles entirely on its own.
 */

const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path = require('path');

function sendMenuAction(action) {
  const win = BrowserWindow.getFocusedWindow() || BrowserWindow.getAllWindows()[0];
  if (win) win.webContents.send('menu-action', action);
}

// A real, native menu bar — File/Edit/View/Window/Help, with a macOS
// app menu prepended on darwin (Electron's own default menu already
// has all of this, but generically labeled "Electron"; this is the
// same shape, tailored to this app, plus the two things the generic
// default can't know about: "New Room" and "Settings"). Edit's roles
// matter beyond convenience — without them, Cmd/Ctrl+C/V/X/A silently
// do nothing in every text input, since Electron doesn't wire those
// shortcuts to the DOM on its own.
function buildMenu() {
  const isMac = process.platform === 'darwin';

  const template = [
    ...(isMac
      ? [
          {
            label: app.name,
            submenu: [
              { role: 'about' },
              { type: 'separator' },
              { label: 'Settings…', accelerator: 'Cmd+,', click: () => sendMenuAction('settings') },
              { type: 'separator' },
              { role: 'services' },
              { type: 'separator' },
              { role: 'hide' },
              { role: 'hideOthers' },
              { role: 'unhide' },
              { type: 'separator' },
              { role: 'quit' },
            ],
          },
        ]
      : []),
    {
      label: 'File',
      submenu: [
        {
          label: 'New Room…',
          accelerator: 'CmdOrCtrl+N',
          click: (_item, win) => win && win.reload(),
        },
        ...(isMac
          ? []
          : [
              { label: 'Settings…', accelerator: 'Ctrl+,', click: () => sendMenuAction('settings') },
            ]),
        { type: 'separator' },
        isMac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        ...(isMac ? [{ role: 'pasteAndMatchStyle' }, { role: 'delete' }, { role: 'selectAll' }] : [{ role: 'delete' }, { type: 'separator' }, { role: 'selectAll' }]),
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(isMac ? [{ type: 'separator' }, { role: 'front' }] : [{ role: 'close' }]),
      ],
    },
    {
      role: 'help',
      submenu: [{ role: 'about' }],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

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

app.setAboutPanelOptions({
  applicationName: 'Agent',
  applicationVersion: app.getVersion(),
});

app.whenReady().then(() => {
  buildMenu();
  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});
