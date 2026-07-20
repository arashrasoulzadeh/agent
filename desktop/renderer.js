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
 */

const { STYLE_TOKENS, EXIT_COMMANDS, REPLY_PLACEHOLDERS, SPINNER_FRAMES, CONNECTION_STATES, parseRichStyle } =
  window.agentComponents;

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

// ---- a compact, escape-first markdown renderer ------------------------
// Answers are LLM output rendered as markdown (props.format === "markdown").
// Escapes everything first, then only ever re-introduces a small, fixed
// set of closed HTML tags — never raw user/model text as markup — so an
// answer can't inject arbitrary HTML. Covers what LLM answers actually
// use: paragraphs, headings, code (fenced + inline), bold/italic, links
// (http(s) only), lists, and blockquotes. Not a full CommonMark parser.

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderInlineMarkdown(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_m, label, href) => {
    return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });
  return out;
}

function renderMarkdown(text) {
  const lines = (text || '').replace(/\r\n/g, '\n').split('\n');
  const html = [];
  let i = 0;
  let listTag = null;

  const closeList = () => {
    if (listTag) {
      html.push(`</${listTag}>`);
      listTag = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      closeList();
      const codeLines = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i += 1;
      }
      html.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}</code></pre>`);
      i += 1;
      continue;
    }

    const heading = /^(#{1,3})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      closeList();
      html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      i += 1;
      continue;
    }

    const unordered = /^[-*]\s+(.*)$/.exec(line);
    const ordered = /^\d+\.\s+(.*)$/.exec(line);
    if (unordered || ordered) {
      const tag = unordered ? 'ul' : 'ol';
      if (listTag !== tag) {
        closeList();
        html.push(`<${tag}>`);
        listTag = tag;
      }
      html.push(`<li>${renderInlineMarkdown((unordered || ordered)[1])}</li>`);
      i += 1;
      continue;
    }

    closeList();
    if (line.trim() === '') {
      i += 1;
      continue;
    }
    html.push(`<p>${renderInlineMarkdown(line)}</p>`);
    i += 1;
  }
  closeList();
  return html.join('\n');
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
  } else {
    throw new Error(`unknown node: ${JSON.stringify(node)}`);
  }

  node_el.id = id;
  widgets.set(id, node_el);
  return node_el;
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
    const panel = el('div', 'node-text node-panel');
    const [padV, padH] = props.padding || [0, 0];
    panel.style.padding = `${padV * 6 + 6}px ${padH * 8 + 10}px`;
    const borderColor = parseRichStyle(props.border_style || '').color;
    if (borderColor) panel.style.borderColor = borderColor;
    if (props.panel_title) {
      const title = el('div', 'node-panel-title');
      title.textContent = props.panel_title;
      panel.appendChild(title);
    }
    panel.appendChild(inner);
    return panel;
  }

  inner.classList.add('node-text');
  return inner;
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
  const [label, styleName] = CONNECTION_STATES[connectionState];
  node_el.textContent = `  ${label}`;
  applyRichStyleTo(node_el, styleName);
}

function setConnectionStatus(state) {
  connectionState = state;
  const node_el = widgets.get('connection-status');
  if (node_el) renderConnectionStatus(node_el);
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
  container.appendChild(build(node));
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
  }
});

function handleDelegatedClick(e) {
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
  const url = `ws://${window.agentEnv.wsHost}:${window.agentEnv.wsPort}`;

  let socket;
  try {
    socket = await connectSocket(url);
  } catch (err) {
    showStartError(
      `Could not reach the agent server at ${url}.\n\nStart one in a terminal first, then retry.`
    );
    return;
  }
  ws = socket;
  attachSocketHandlers(socket);

  try {
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
