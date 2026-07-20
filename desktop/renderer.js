'use strict';

/**
 * The desktop client — a generic server-driven UI renderer, same role
 * as ui/app.py, just DOM-based instead of Textual-based. Has zero
 * built-in knowledge of any screen (no header layout, no modal shapes,
 * no command list): everything drawable is a Node (models/ui.py's
 * shape, delivered as plain JSON over the same WebSocket protocol
 * docs/PROTOCOL.md describes) built server-side by
 * service/ui_builder.py. Mounts the full tree once (/session/create's
 * or /session/resume's "tree"), then applies incremental ui.update ops
 * (replace/append/remove) from then on.
 *
 * The same three things stay client-local here as in ui/app.py, for
 * the same reasons (see that module's docstring): connection status,
 * the spinner glyph's animation, and command-popup filtering /
 * "exit"/"quit"/"q" interception. Every other interaction becomes one
 * /ui/event request; the server decides what it means.
 *
 * No framework, no bundler — plain DOM APIs, loaded as a classic
 * script. That keeps this app's startup and interaction latency close
 * to Electron's floor, and keeps the whole client small enough to read
 * start to finish alongside ui/app.py when adding a feature to both.
 *
 * This client's own local UI vocabulary (style tokens, exit words,
 * spinner frames, reply placeholders, connection-state labels, the Rich
 * color table) is never bundled — see preload.js's docstring. main()
 * fetches it fresh from the server (/ui/spec) as its first request,
 * before rendering anything, and fetchUiSpec() below assigns it into
 * these `let` bindings; every function below reads them at call time,
 * so nothing here needs to know or care that they started out empty.
 */

const { parseRichStyle, setRichColors } = window.agentComponents;
// The markdown renderer (desktop/markdown.js, which also holds the
// syntax highlighter renderMarkdown calls internally) — pure
// string-in/string-out logic with no DOM dependency, split out so it's
// independently unit tested (desktop/markdown.test.js) rather than
// living inline here untested.
const { renderMarkdown } = window.agentMarkdown;

let STYLE_TOKENS = {};
let EXIT_COMMANDS = [];
let REPLY_PLACEHOLDERS = [];
let SPINNER_FRAMES = '';
let CONNECTION_STATES = {};

async function fetchUiSpec() {
  const spec = await request('/ui/spec', {});
  STYLE_TOKENS = spec.styleTokens;
  EXIT_COMMANDS = spec.exitCommands;
  REPLY_PLACEHOLDERS = spec.replyPlaceholders;
  SPINNER_FRAMES = spec.spinnerFrames;
  CONNECTION_STATES = spec.connectionStates;
  setRichColors(spec.richColors);
}

// ---- tiny DOM helpers ------------------------------------------------

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function applyRichStyleTo(node, styleString) {
  const css = parseRichStyle(styleString);
  for (const [prop, value] of Object.entries(css)) node.style[prop] = value;
}

function makeId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ---- generic renderer state --------------------------------------------

const appScreen = document.getElementById('app-screen');
const modalSlot = document.getElementById('modal-slot');

const widgets = new Map(); // node id -> HTMLElement
const nodeTypeById = new Map(); // node id -> node.type

let connectionState = 'connecting';
let headerStatusProps = null;
let spinnerFrame = 0;

let commandOptions = []; // full [[value, text], ...] from the initial tree
let currentMatches = []; // currently displayed (filtered) options
let popupHighlighted = 0;

// ---- building widgets from nodes ---------------------------------------

function build(node) {
  const { id, type, props = {}, children = [] } = node;
  nodeTypeById.set(id, type);

  let node_el;
  if (id === 'connection-status') {
    // Reserved, client-owned slot — the server always sends this empty;
    // only setConnectionStatus() ever writes its content.
    node_el = el('div', 'node-connection-status');
    node_el.id = id;
    widgets.set(id, node_el);
    renderConnectionStatus(node_el);
    return node_el;
  } else if (type === 'container') {
    node_el = el('div', `node-container ${props.direction === 'horizontal' ? 'row' : 'col'}`);
    for (const child of children) node_el.appendChild(build(child));
    // A bordered/titled container — e.g. service/ui_builder.py's
    // agent_ui_node(), the show_ui tool's rendering — reuses the exact
    // same chrome a panel-wrapped text node gets (applyPanelChrome,
    // below), just applied to a container instead of a single text div.
    if (props.panel) applyPanelChrome(node_el, props);
  } else if (type === 'text') {
    if (id === 'header-status') {
      headerStatusProps = props;
      node_el = el('div', 'node-text header-status');
      node_el.id = id;
      widgets.set(id, node_el);
      renderHeaderStatus(node_el);
      return node_el;
    }
    node_el = buildText(props);
  } else if (type === 'input') {
    node_el = el('input', 'node-input');
    node_el.type = props.password ? 'password' : 'text';
    node_el.placeholder = props.placeholder || '';
    node_el.value = props.value || '';
    node_el.autocomplete = 'off';
    node_el.spellcheck = false;
  } else if (type === 'button') {
    node_el = el('button', 'node-button');
    node_el.type = 'button';
    node_el.textContent = props.label || '';
  } else if (type === 'list' && props.kind === 'options') {
    node_el = buildOptionsList(node);
  } else if (type === 'list') {
    // kind === "log" — the content transcript.
    node_el = el('div', 'node-list-log');
    for (const child of children) node_el.appendChild(build(child));
  } else if (type === 'table') {
    node_el = buildTable(props);
  } else {
    throw new Error(`unknown node: ${JSON.stringify(node)}`);
  }

  node_el.id = id;
  widgets.set(id, node_el);
  return node_el;
}

// Shared by a panel-wrapped text node (below) and a panel-wrapped
// container (build()'s container branch, above) — same chrome either
// way: a border/padding/optional title on whichever element already
// holds the real content, inserted at that element's front so the
// title always reads first regardless of what kind of node it is.
function applyPanelChrome(target, props) {
  target.classList.add('node-panel');
  const [padV, padH] = props.padding || [0, 0];
  target.style.padding = `${padV * 6 + 6}px ${padH * 8 + 10}px`;
  const borderColor = parseRichStyle(props.border_style || '').color;
  if (borderColor) {
    // Left edge deliberately untouched — styles.css's
    // .node-panel:not(.error-panel) rule owns it (an accent stripe
    // marking "elevated surface"); setting all four sides here would
    // win over that CSS rule via inline-style precedence and flatten
    // every panel back to a single flat border color.
    target.style.borderTopColor = borderColor;
    target.style.borderRightColor = borderColor;
    target.style.borderBottomColor = borderColor;
  }
  if (props.panel_title) {
    const title = el('div', 'node-panel-title');
    title.textContent = props.panel_title;
    target.insertBefore(title, target.firstChild);
  }
}

function buildText(props) {
  let inner;
  if (props.format === 'markdown') {
    inner = el('div', 'markdown-body');
    inner.innerHTML = renderMarkdown(props.text || '');
  } else if (Array.isArray(props.spans)) {
    inner = el('div', 'span-line');
    for (const span of props.spans) {
      const spanEl = el('span');
      spanEl.textContent = span.text || '';
      applyRichStyleTo(spanEl, span.style);
      inner.appendChild(spanEl);
    }
  } else {
    inner = el('div', 'plain-text');
    inner.textContent = props.text || '';
    applyRichStyleTo(inner, props.style);
  }

  if (props.panel) {
    const panel = el('div', 'node-text');
    panel.appendChild(inner);
    applyPanelChrome(panel, props);
    return panel;
  }

  inner.classList.add('node-text');
  return inner;
}

// A show_ui table block (service/ui_builder.py's _table_node()) — a
// real HTML <table>, not formatted text; the CLI equivalent is a real
// rich.table.Table (ui/app.py's _build()). Text content only (no
// markdown, no spans) — a table cell is exactly one string, matching
// what content_entry_node's other block kinds already assume.
function buildTable(props) {
  const wrap = el('div', 'node-table-wrap');
  const table = el('table', 'node-table');
  const headers = props.headers || [];
  if (headers.length) {
    const thead = el('thead');
    const row = el('tr');
    for (const header of headers) {
      const th = el('th');
      th.textContent = header;
      row.appendChild(th);
    }
    thead.appendChild(row);
    table.appendChild(thead);
  }
  const tbody = el('tbody');
  for (const row of props.rows || []) {
    const tr = el('tr');
    for (const cell of row) {
      const td = el('td');
      td.textContent = cell;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function buildOptionsList(node) {
  const popup = el('div', 'node-options-list');
  commandOptions = (node.children || []).map((c) => [c.props.value, c.props.text]);
  popup.hidden = node.props.display === false;
  renderCommandOptions(commandOptions, popup);
  return popup;
}

function renderCommandOptions(matches, popupEl) {
  currentMatches = matches;
  popupEl.innerHTML = '';
  matches.forEach(([value, text], index) => {
    const row = el('div', 'popup-row' + (index === popupHighlighted ? ' highlighted' : ''));
    row.dataset.value = value;
    row.textContent = text;
    popupEl.appendChild(row);
  });
}

function highlightPopupRow() {
  const popup = widgets.get('command-popup');
  if (!popup) return;
  [...popup.children].forEach((row, index) => {
    row.classList.toggle('highlighted', index === popupHighlighted);
  });
}

// ---- client-local cosmetics: connection status + spinner --------------

function renderConnectionStatus(node_el) {
  // CONNECTION_STATES is empty until fetchUiSpec() resolves — reachable
  // for real the moment main() calls setConnectionStatus('connecting')
  // right after the socket opens, before that fetch's own response has
  // arrived yet. Leaving the readout blank for that brief window (or,
  // on a connection that fails outright, for good — we never got the
  // server's own labels to show) is the honest behavior: this client
  // has no local fallback copy of that vocabulary to fall back to.
  const entry = CONNECTION_STATES[connectionState];
  if (!entry) return;
  const [label, styleName] = entry;
  node_el.textContent = `  ${label}`;
  node_el.dataset.state = connectionState; // styles.css's connected/disconnected glow
  applyRichStyleTo(node_el, styleName);
}

function setConnectionStatus(state) {
  connectionState = state;
  const node_el = widgets.get('connection-status');
  if (node_el) renderConnectionStatus(node_el);
  // The start screen has its own persistent status readout in the same
  // spot #connection-status occupies once a session is mounted (see
  // index.html's #start-topbar-status) — painting both from one call
  // keeps the two screens' chrome visually continuous across the swap.
  const startTopbarStatus = document.getElementById('start-topbar-status');
  if (startTopbarStatus) renderConnectionStatus(startTopbarStatus);
}

function renderHeaderStatus(node_el) {
  const frame = SPINNER_FRAMES[spinnerFrame % SPINNER_FRAMES.length];
  const label = ((headerStatusProps && headerStatusProps.text) || '').trim();
  node_el.textContent = `  ${frame} ${label}`;
  applyRichStyleTo(node_el, headerStatusProps && headerStatusProps.style);
}

setInterval(() => {
  if (!headerStatusProps) return;
  spinnerFrame += 1;
  const node_el = widgets.get('header-status');
  if (node_el) renderHeaderStatus(node_el);
}, 100);

// ---- applying server-driven UI ops -------------------------------------

let rootMounted = false;
const uiQueue = [];

function queueOps(ops) {
  uiQueue.push(ops);
  drainQueue();
}

function drainQueue() {
  if (!rootMounted) return;
  while (uiQueue.length) {
    const ops = uiQueue.shift();
    try {
      applyOps(ops);
    } catch (err) {
      console.error('failed to apply ui.update ops', err);
    }
  }
}

function applyOps(ops) {
  for (const op of ops) {
    if (op.op === 'replace') replaceNode(op.target, op.node);
    else if (op.op === 'append') appendNode(op.target, op.node);
    else if (op.op === 'remove') removeNode(op.target);
  }
}

function forgetChildren(node_el) {
  // Purges every descendant's id from widgets/nodeTypeById — a replace
  // or remove only pops the top-level target's own id; without this, a
  // child that disappears when a container's shape changes (e.g.
  // header-status once a turn finishes) leaves a stale entry behind.
  for (const descendant of node_el.querySelectorAll('[id]')) {
    widgets.delete(descendant.id);
    nodeTypeById.delete(descendant.id);
  }
}

function replaceNode(target, node) {
  // footer-input's live, not-yet-submitted typed value must survive a
  // replace that only changed the placeholder/password mode — see
  // service/ui_builder.py's module docstring for why.
  if (target === 'footer-input') {
    const input = widgets.get('footer-input');
    if (input instanceof HTMLInputElement) {
      const props = node.props || {};
      input.placeholder = props.placeholder || '';
      input.type = props.password ? 'password' : 'text';
      return;
    }
  }

  if (target === 'header') headerStatusProps = null; // build() repopulates if present

  const existing = widgets.get(target);
  let parent = null;
  let nextSibling = null;
  if (existing) {
    parent = existing.parentElement;
    nextSibling = existing.nextSibling;
    forgetChildren(existing);
    widgets.delete(target);
    nodeTypeById.delete(target);
  }

  const newEl = build(node);
  if (existing) existing.remove();

  if (target === 'modal') {
    modalSlot.appendChild(newEl);
    modalSlot.hidden = false;
    return;
  }

  if (parent) parent.insertBefore(newEl, nextSibling);
}

function appendNode(target, node) {
  const container = widgets.get(target);
  if (!container) return;
  const entryEl = build(node);
  // Only a live-arriving entry animates in (styles.css's .enter) — the
  // initial tree's bulk-built children (build()'s own recursive pass,
  // never appendNode) mount instantly, so a resumed session with a long
  // transcript doesn't replay every past line fading in at once.
  entryEl.classList.add('enter');
  container.appendChild(entryEl);
  if (target === 'content') container.scrollTop = container.scrollHeight;
}

function removeNode(target) {
  const existing = widgets.get(target);
  widgets.delete(target);
  nodeTypeById.delete(target);
  if (!existing) return;
  forgetChildren(existing);
  existing.remove();
  if (target === 'modal') {
    modalSlot.hidden = true;
    modalSlot.innerHTML = '';
  }
}

function appendLocalError(message) {
  const container = widgets.get('content');
  if (!container) return;
  const panel = el('div', 'node-text node-panel error-panel');
  panel.style.padding = '6px 10px';
  const title = el('div', 'node-panel-title');
  title.textContent = 'error';
  const body = el('div', 'plain-text');
  body.textContent = message;
  applyRichStyleTo(body, STYLE_TOKENS.ERROR);
  panel.append(title, body);
  container.appendChild(panel);
  container.scrollTop = container.scrollHeight;
}

// ---- mounting the initial tree ------------------------------------------

function mountRoot(tree) {
  appScreen.innerHTML = '';
  const root = build(tree);
  appScreen.appendChild(root);
  document.getElementById('start-screen').hidden = true;
  appScreen.hidden = false;
  // A client-owned status line, mounted once as an extra sibling inside
  // the server's own "footer" container — safe because nothing in the
  // protocol ever replaces "footer" itself, only "footer-info"/
  // "footer-input" individually by their own id (service/rooms.py's
  // _state_ops()), so this element is never touched by an incoming
  // ui.update op. Same trick as #modal-slot: client-local cosmetics
  // live outside the tree the server actually rebuilds.
  const footer = widgets.get('footer');
  if (footer) {
    const hint = el('div', 'token-hint');
    hint.id = 'token-hint';
    footer.appendChild(hint);
    widgets.set('token-hint', hint);
  }
  setConnectionStatus('connected');
  rootMounted = true;
  drainQueue();

  const content = widgets.get('content');
  if (content) {
    content.scrollTop = content.scrollHeight;
    // A resumed session's tree arrives with its whole transcript already
    // replayed — a tall/rich reply (markdown, a panel) can shift layout
    // height slightly after the first paint, so a short second pass
    // catches any late reflow the first one landed just before (mirrors
    // ui/app.py's own two-pass scroll_end).
    setTimeout(() => {
      content.scrollTop = content.scrollHeight;
    }, 200);
  }
  const footerInput = widgets.get('footer-input');
  if (footerInput) footerInput.focus();
}

// ---- command popup (client-local filtering only) -----------------------

function updateCommandPopup(value) {
  const popup = widgets.get('command-popup');
  const footerInput = widgets.get('footer-input');
  if (!popup || !footerInput) return;
  const replyMode = REPLY_PLACEHOLDERS.includes(footerInput.placeholder);
  const firstToken = value.split(' ', 1)[0];
  const matches = commandOptions.filter(([v]) => v.startsWith(firstToken));
  const exactAndPastIt = matches.length === 1 && matches[0][0] === firstToken && value.includes(' ');

  if (replyMode || !value.startsWith('/') || matches.length === 0 || exactAndPastIt) {
    popup.hidden = true;
    return;
  }
  popup.hidden = false;
  popupHighlighted = 0;
  renderCommandOptions(matches, popup);
}

function acceptCommandPopup(inputEl, value) {
  const popup = widgets.get('command-popup');
  if (!popup) return false;
  if (REPLY_PLACEHOLDERS.includes(inputEl.placeholder)) return false;
  if (commandOptions.some(([v]) => v === value)) return false;
  if (popup.hidden || currentMatches.length === 0) return false;
  const index = popupHighlighted || 0;
  const [cmdValue] = currentMatches[index];
  inputEl.value = `${cmdValue} `;
  inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  return true;
}

function popupPrev() {
  const popup = widgets.get('command-popup');
  if (popup && !popup.hidden && currentMatches.length) {
    popupHighlighted = (popupHighlighted - 1 + currentMatches.length) % currentMatches.length;
    highlightPopupRow();
    return true;
  }
  return false;
}

function popupNext() {
  const popup = widgets.get('command-popup');
  if (popup && !popup.hidden && currentMatches.length) {
    popupHighlighted = (popupHighlighted + 1) % currentMatches.length;
    highlightPopupRow();
    return true;
  }
  return false;
}

function dismissOverlay() {
  const popup = widgets.get('command-popup');
  if (popup && !popup.hidden) {
    popup.hidden = true;
    return;
  }
  if (!modalSlot.hidden) modalSlot.hidden = true;
}

// ---- forwarding interactions to the server -----------------------------

async function handleInputSubmit(inputEl) {
  const componentId = inputEl.id;
  if (!componentId) return;
  const value = inputEl.value.trim();

  if (componentId === 'footer-input') {
    if (acceptCommandPopup(inputEl, value)) return;
    if (EXIT_COMMANDS.includes(value.toLowerCase())) {
      window.close();
      return;
    }
    inputEl.value = '';
  } else if (componentId.startsWith('setting-') && inputEl.type === 'password') {
    // Optimistic local clear for a just-submitted secret field — the
    // server's next modal replace also sends it back blank, but this
    // avoids a stale-looking value in the meantime.
    inputEl.value = '';
  }

  await sendUiEvent(componentId, 'submit', value);
}

async function sendUiEvent(componentId, eventName, value) {
  const data = { component_id: componentId, event: eventName };
  if (value !== undefined) data.value = value;
  try {
    await request('/ui/event', data);
  } catch (err) {
    appendLocalError(err.message);
  }
}

appScreen.addEventListener('keydown', (e) => {
  const target = e.target;
  if (!(target instanceof HTMLInputElement) || !target.classList.contains('node-input')) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    handleInputSubmit(target);
  }
});

appScreen.addEventListener('input', (e) => {
  const target = e.target;
  if (target instanceof HTMLInputElement && target.id === 'footer-input') {
    updateCommandPopup(target.value);
    updateTokenHint(target.value);
  }
});

// ---- token estimate (client-local cosmetic, like the spinner) ---------

// A fast, dependency-free approximation shown before sending — not a
// real tokenizer, ~4 characters per token (the commonly-cited rule of
// thumb for English text), rounded up. ui/app.py's _estimate_tokens()
// uses the identical formula so the number reads the same on both
// clients; duplicated rather than shared via components/ since it's a
// trivial, stateless algorithm, not server-owned UI data.
function estimateTokens(text) {
  if (!text) return 0;
  return Math.max(1, Math.ceil(text.length / 4));
}

function updateTokenHint(value) {
  const hint = widgets.get('token-hint');
  if (!hint) return;
  const count = estimateTokens(value);
  hint.textContent = count ? `~${count} token${count === 1 ? '' : 's'}` : '';
}

function handleDelegatedClick(e) {
  const copyBtn = e.target.closest('.code-copy-btn');
  if (copyBtn) {
    // textContent (not the highlighted innerHTML) — the .tok-* spans
    // wrap runs of the same text without adding/removing characters,
    // so this always yields the exact original code, highlighted or not.
    const code = copyBtn.closest('.code-block-wrap').querySelector('code').textContent;
    window.agentNative.copyText(code);
    copyBtn.textContent = 'Copied';
    copyBtn.classList.add('copied');
    setTimeout(() => {
      copyBtn.textContent = 'Copy';
      copyBtn.classList.remove('copied');
    }, 1200);
    return;
  }
  const row = e.target.closest('.popup-row');
  if (row) {
    const footerInput = widgets.get('footer-input');
    if (footerInput instanceof HTMLInputElement) {
      footerInput.value = `${row.dataset.value} `;
      footerInput.focus();
      footerInput.setSelectionRange(footerInput.value.length, footerInput.value.length);
    }
    const popup = widgets.get('command-popup');
    if (popup) popup.hidden = true;
    return;
  }
  const button = e.target.closest('button.node-button');
  if (button && button.id) sendUiEvent(button.id, 'click');
}

appScreen.addEventListener('click', handleDelegatedClick);
modalSlot.addEventListener('click', handleDelegatedClick);

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowUp') {
    if (popupPrev()) e.preventDefault();
  } else if (e.key === 'ArrowDown') {
    if (popupNext()) e.preventDefault();
  } else if (e.key === 'Escape') {
    dismissOverlay();
  }
});

// ---- wire protocol: connect, request/response, events -------------------

let ws = null;
let roomId = null;
const pendingRequests = new Map();

function connectSocket(url, timeoutMs = 4000) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url);
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      socket.close();
      reject(new Error('timed out connecting to the agent server'));
    }, timeoutMs);
    socket.addEventListener('open', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(socket);
    });
    socket.addEventListener('error', () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`could not reach the agent server at ${url}`));
    });
  });
}

function request(route, data) {
  return new Promise((resolve, reject) => {
    if (!ws) {
      reject(new Error('not connected'));
      return;
    }
    const id = makeId();
    const timer = setTimeout(() => {
      if (pendingRequests.has(id)) {
        pendingRequests.delete(id);
        reject(new Error('request timed out'));
      }
    }, 30000);
    pendingRequests.set(id, {
      resolve: (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      reject: (err) => {
        clearTimeout(timer);
        reject(err);
      },
    });
    const payload = { id, route, data };
    if (roomId) payload.room = roomId;
    ws.send(JSON.stringify(payload));
  });
}

function handleMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch {
    return;
  }

  if (!('route' in msg) && 'id' in msg) {
    const pending = pendingRequests.get(msg.id);
    if (!pending) return;
    pendingRequests.delete(msg.id);
    if (msg.ok) pending.resolve(msg.data || {});
    else pending.reject(new Error(msg.error || 'request failed'));
    return;
  }

  // Every other event (session.state, message, tool.call, ...) still
  // fires server-side for any other purpose, but this renderer only
  // ever draws from ui.update — see this module's docstring.
  if (msg.event === 'ui.update') {
    queueOps((msg.data && msg.data.ops) || []);
  }
}

function attachSocketHandlers(socket) {
  socket.addEventListener('message', (event) => handleMessage(event.data));
  socket.addEventListener('close', () => {
    if (rootMounted) {
      appendLocalError('Lost connection to the agent server.');
      setConnectionStatus('disconnected');
    }
  });
}

// ---- start screen: reachability, path prompt, resume picker -------------

const startConnecting = document.getElementById('start-connecting');
const startError = document.getElementById('start-error');
const startErrorText = document.getElementById('start-error-text');
const startReady = document.getElementById('start-ready');
const startRetry = document.getElementById('start-retry');
const startForm = document.getElementById('start-form');
const startPathLabel = document.getElementById('start-path-label');
const startPathInput = document.getElementById('start-path');
const startBrowseBtn = document.getElementById('start-browse');
const startSubmitBtn = document.getElementById('start-submit');
const startRoomsWrap = document.getElementById('start-rooms-wrap');
const startRoomsList = document.getElementById('start-rooms');

function showStartState(name) {
  startConnecting.hidden = name !== 'connecting';
  startError.hidden = name !== 'error';
  startReady.hidden = name !== 'ready';
}

function showStartError(message) {
  startErrorText.textContent = message;
  showStartState('error');
}

function formatUpdatedAt(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function renderRoomsList(rooms) {
  startRoomsList.innerHTML = '';
  if (!rooms.length) {
    startRoomsWrap.hidden = true;
    return;
  }
  startRoomsWrap.hidden = false;
  for (const room of rooms) {
    const li = el('li', 'room-row');
    const info = el('div', 'room-info');
    const pathLine = el('div', 'room-path');
    pathLine.textContent = room.path;
    const metaLine = el('div', 'room-meta');
    metaLine.textContent = `updated ${formatUpdatedAt(room.updated_at)}`;
    info.append(pathLine, metaLine);
    const resumeBtn = el('button', 'btn');
    resumeBtn.type = 'button';
    resumeBtn.textContent = 'Resume';
    resumeBtn.addEventListener('click', () => resumeRoom(room.id));
    li.append(info, resumeBtn);
    startRoomsList.appendChild(li);
  }
}

async function createSession(path) {
  startSubmitBtn.disabled = true;
  try {
    const data = await request('/session/create', { path });
    roomId = data.room;
    mountRoot(data.tree);
  } catch (err) {
    showStartError(err.message);
  } finally {
    startSubmitBtn.disabled = false;
  }
}

async function resumeRoom(id) {
  roomId = id;
  try {
    const data = await request('/session/resume', { room: id });
    mountRoot(data.tree);
  } catch (err) {
    roomId = null;
    showStartError(err.message);
  }
}

startForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const value = startPathInput.value.trim() || startPathInput.dataset.default || '.';
  createSession(value);
});

startBrowseBtn.addEventListener('click', async () => {
  const picked = await window.agentNative.pickFolder();
  if (picked) startPathInput.value = picked;
});

startRetry.addEventListener('click', () => {
  main();
});

async function main() {
  showStartState('connecting');
  setConnectionStatus('connecting');
  const url = `ws://${window.agentEnv.wsHost}:${window.agentEnv.wsPort}`;

  let socket;
  try {
    socket = await connectSocket(url);
  } catch (err) {
    setConnectionStatus('disconnected');
    showStartError(
      `Could not reach the agent server at ${url}.\n\nStart one in a terminal first, then retry.`
    );
    return;
  }
  ws = socket;
  attachSocketHandlers(socket);

  try {
    // Before anything else — every other request below assumes exit
    // words, spinner frames, reply placeholders, and connection-state
    // labels are already known.
    await fetchUiSpec();
    // A request/response round trip just succeeded, so the connection
    // genuinely is up now — reflect that in the topbar rather than
    // leaving it on whatever setConnectionStatus('connecting') (called
    // before the spec existed to render anything) left it at.
    setConnectionStatus('connected');
    const [promptData, roomsData] = await Promise.all([
      request('/session/prompt', {}),
      request('/rooms/list', {}),
    ]);
    startPathLabel.textContent = promptData.text;
    startPathInput.placeholder = promptData.default;
    startPathInput.dataset.default = promptData.default;
    renderRoomsList(roomsData.rooms || []);
    showStartState('ready');
    startPathInput.focus();
  } catch (err) {
    showStartError(err.message);
  }
}

main();
