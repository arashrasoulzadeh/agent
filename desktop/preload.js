'use strict';

/**
 * The only bridge between the sandboxed renderer and Node/Electron APIs
 * (contextIsolation is on, nodeIntegration is off — the renderer itself
 * never gets `require`). Exposes exactly four things:
 *
 *   - `agentComponents` — interpreter code only, not data: the
 *     Rich-style-string -> CSS parser (components/js/richStyle.js) and
 *     its color-table setter. The actual UI vocabulary (style tokens,
 *     exit commands, spinner frames, connection-state labels, the color
 *     table itself) is never bundled here — renderer.js fetches it live
 *     from the server (`/ui/spec`, right after connecting) and calls
 *     setRichColors() with the result. Same principle ui/app.py follows
 *     on the Python side: a client ships the ability to interpret the
 *     server's UI vocabulary, never a local copy of the vocabulary
 *     itself, so a change to it needs no client rebuild.
 *   - `agentMarkdown` — the markdown renderer + syntax highlighter
 *     (desktop/markdown.js), another pure-logic module kept out of
 *     renderer.js specifically so it's independently unit tested
 *     (desktop/markdown.test.js).
 *   - `agentEnv` — where to find the agent server (same
 *     AGENT_WS_HOST/AGENT_WS_PORT env vars wire/config.py reads).
 *   - `agentNative` — native capabilities the renderer needs that the
 *     web platform can't give it: a real folder picker, clipboard
 *     writes (Electron's `clipboard` module, not the web Clipboard API —
 *     more reliable than `navigator.clipboard` from a `file://`-loaded
 *     page, and needs no permission prompt), and menu-triggered actions
 *     from the native application menu (main.js's buildMenu()).
 */

const { contextBridge, ipcRenderer, clipboard } = require('electron');
const { parseRichStyle, setRichColors } = require('../components/js/richStyle.js');
const markdown = require('./markdown.js');

contextBridge.exposeInMainWorld('agentComponents', {
  parseRichStyle,
  setRichColors,
});

contextBridge.exposeInMainWorld('agentMarkdown', markdown);

contextBridge.exposeInMainWorld('agentEnv', {
  wsHost: process.env.AGENT_WS_HOST || '127.0.0.1',
  wsPort: process.env.AGENT_WS_PORT || '8765',
});

contextBridge.exposeInMainWorld('agentNative', {
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  copyText: (text) => clipboard.writeText(text),
  // A native menu item (main.js's buildMenu()) that needs the renderer
  // to act — currently just "Settings…" — sends its action string this
  // way rather than reaching into renderer state directly from main.
  onMenuAction: (callback) => {
    ipcRenderer.on('menu-action', (_event, action) => callback(action));
  },
});
