'use strict';

/**
 * The only bridge between the sandboxed renderer and Node/Electron APIs
 * (contextIsolation is on, nodeIntegration is off — the renderer itself
 * never gets `require`). Exposes exactly three things:
 *
 *   - `agentComponents` — the shared UI vocabulary from components/js/
 *     (see that folder's docstring): style tokens, exit commands, the
 *     spinner/connection-state constants, and the Rich-style-string ->
 *     CSS parser. The single source of truth ui/app.py also reads, via
 *     components/__init__.py.
 *   - `agentEnv` — where to find the agent server (same
 *     AGENT_WS_HOST/AGENT_WS_PORT env vars wire/config.py reads).
 *   - `agentNative` — native capabilities the renderer needs that the
 *     web platform can't give it: a real folder picker, and clipboard
 *     writes (Electron's `clipboard` module, not the web Clipboard API —
 *     more reliable than `navigator.clipboard` from a `file://`-loaded
 *     page, and needs no permission prompt).
 */

const { contextBridge, ipcRenderer, clipboard } = require('electron');
const components = require('../components/js/index.js');
const { parseRichStyle } = require('../components/js/richStyle.js');

contextBridge.exposeInMainWorld('agentComponents', {
  ...components,
  parseRichStyle,
});

contextBridge.exposeInMainWorld('agentEnv', {
  wsHost: process.env.AGENT_WS_HOST || '127.0.0.1',
  wsPort: process.env.AGENT_WS_PORT || '8765',
});

contextBridge.exposeInMainWorld('agentNative', {
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  copyText: (text) => clipboard.writeText(text),
});
