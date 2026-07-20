'use strict';

/**
 * A compact, escape-first markdown renderer plus a small syntax
 * highlighter — pure string-in, string-out functions with no DOM
 * dependency, split out of renderer.js so they're independently unit
 * testable (desktop/markdown.test.js) without an Electron/browser
 * environment. Exposed to renderer.js via preload.js's contextBridge,
 * same pattern components/js/richStyle.js already uses for its own
 * pure interpreter code.
 *
 * Answers are LLM output rendered as markdown (props.format ===
 * "markdown"). Escapes everything first, then only ever re-introduces a
 * small, fixed set of closed HTML tags — never raw user/model text as
 * markup — so an answer can't inject arbitrary HTML. Covers what LLM
 * answers actually use: paragraphs, headings, code (fenced + inline),
 * bold/italic, links (http(s) only), lists, and blockquotes. Not a full
 * CommonMark parser. A fenced code block additionally gets a language
 * label + one-click copy (renderer.js's handleDelegatedClick) and
 * best-effort syntax highlighting (highlightCode, below).
 */

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ---- a small, honest syntax highlighter --------------------------------
// Not a real tokenizer/parser (no per-language grammar, no nesting rules)
// — one shared regex per language classifying comments/strings/numbers/
// identifiers by pattern alone. Good enough to make a code block
// scannable; wrong on edge cases a real lexer wouldn't be (e.g. a `#`
// inside a JS regex literal). ui/app.py gets real Pygments highlighting
// for free from Rich; this is what closes that gap for desktop without
// pulling in a highlighting library and its grammar files.

const LANG_ALIASES = {
  js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', py3: 'python',
  sh: 'bash', shell: 'bash', zsh: 'bash', bash: 'bash', console: 'bash',
  rb: 'ruby',
  golang: 'go',
  rs: 'rust',
  yml: 'yaml',
  c: 'c', h: 'c', cpp: 'c', 'c++': 'c', cc: 'c',
};

const LANG_KEYWORDS = {
  python: ['def', 'return', 'if', 'elif', 'else', 'for', 'while', 'in', 'not', 'and', 'or', 'import', 'from', 'as', 'class', 'try', 'except', 'finally', 'with', 'lambda', 'yield', 'pass', 'break', 'continue', 'None', 'True', 'False', 'self', 'raise', 'async', 'await', 'global', 'nonlocal', 'is', 'del'],
  javascript: ['function', 'return', 'if', 'else', 'for', 'while', 'in', 'of', 'const', 'let', 'var', 'class', 'try', 'catch', 'finally', 'import', 'export', 'from', 'as', 'new', 'this', 'typeof', 'instanceof', 'null', 'undefined', 'true', 'false', 'async', 'await', 'yield', 'switch', 'case', 'break', 'continue', 'default', 'extends', 'super', 'static', 'throw', 'delete', 'void'],
  bash: ['if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'do', 'done', 'case', 'esac', 'function', 'return', 'local', 'export', 'in', 'echo', 'exit', 'set'],
  json: ['true', 'false', 'null'],
  go: ['func', 'return', 'if', 'else', 'for', 'range', 'package', 'import', 'var', 'const', 'type', 'struct', 'interface', 'map', 'chan', 'go', 'defer', 'select', 'switch', 'case', 'default', 'break', 'continue', 'nil', 'true', 'false'],
  rust: ['fn', 'return', 'if', 'else', 'for', 'while', 'loop', 'match', 'let', 'mut', 'const', 'struct', 'enum', 'impl', 'trait', 'pub', 'use', 'mod', 'self', 'Self', 'true', 'false', 'None', 'Some', 'Ok', 'Err', 'async', 'await'],
  sql: ['select', 'from', 'where', 'insert', 'into', 'values', 'update', 'set', 'delete', 'create', 'table', 'drop', 'alter', 'join', 'left', 'right', 'inner', 'outer', 'on', 'group', 'by', 'order', 'having', 'limit', 'and', 'or', 'not', 'null', 'as', 'distinct'],
  ruby: ['def', 'end', 'return', 'if', 'elsif', 'else', 'unless', 'for', 'while', 'in', 'do', 'class', 'module', 'require', 'nil', 'true', 'false', 'self', 'yield', 'begin', 'rescue', 'ensure', 'raise'],
  c: ['int', 'char', 'float', 'double', 'void', 'struct', 'return', 'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'break', 'continue', 'static', 'const', 'sizeof', 'typedef', 'enum', 'union', 'unsigned', 'signed', 'null', 'NULL'],
};
LANG_KEYWORDS.typescript = [...LANG_KEYWORDS.javascript, 'interface', 'type', 'implements', 'enum', 'namespace', 'public', 'private', 'protected', 'readonly', 'is', 'declare'];

const LANG_COMMENTS = {
  python: '#.*', bash: '#.*', ruby: '#.*', yaml: '#.*',
  javascript: '//.*|/\\*[\\s\\S]*?\\*/',
  typescript: '//.*|/\\*[\\s\\S]*?\\*/',
  go: '//.*|/\\*[\\s\\S]*?\\*/',
  rust: '//.*|/\\*[\\s\\S]*?\\*/',
  c: '//.*|/\\*[\\s\\S]*?\\*/',
  css: '/\\*[\\s\\S]*?\\*/',
  sql: '--.*',
  html: '<!--[\\s\\S]*?-->',
  xml: '<!--[\\s\\S]*?-->',
};

function highlightCode(code, langTag) {
  const language = LANG_ALIASES[langTag] || langTag;
  const keywords = new Set(LANG_KEYWORDS[language] || []);
  const commentSource = LANG_COMMENTS[language];
  if (!keywords.size && !commentSource) return escapeHtml(code); // unrecognized language: still safe, just plain

  const combined = new RegExp(
    `(?<comment>${commentSource || '(?!)'})` +
      `|(?<string>"(?:[^"\\\\]|\\\\.)*"|'(?:[^'\\\\]|\\\\.)*'|\`(?:[^\`\\\\]|\\\\.)*\`)` +
      `|(?<number>\\b\\d+(?:\\.\\d+)?\\b)` +
      `|(?<word>[A-Za-z_$][A-Za-z0-9_$]*)`,
    'g'
  );

  const out = [];
  let lastIndex = 0;
  let match;
  while ((match = combined.exec(code)) !== null) {
    if (match.index > lastIndex) out.push(escapeHtml(code.slice(lastIndex, match.index)));
    const { comment, string, number, word } = match.groups;
    const token = match[0];
    if (comment !== undefined) out.push(`<span class="tok-comment">${escapeHtml(token)}</span>`);
    else if (string !== undefined) out.push(`<span class="tok-string">${escapeHtml(token)}</span>`);
    else if (number !== undefined) out.push(`<span class="tok-number">${escapeHtml(token)}</span>`);
    else if (word !== undefined && keywords.has(word)) out.push(`<span class="tok-keyword">${escapeHtml(token)}</span>`);
    else if (word !== undefined && code[combined.lastIndex] === '(') out.push(`<span class="tok-function">${escapeHtml(token)}</span>`);
    else out.push(escapeHtml(token));
    lastIndex = combined.lastIndex;
  }
  out.push(escapeHtml(code.slice(lastIndex)));
  return out.join('');
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
      const langTag = line.trim().slice(3).trim().toLowerCase();
      const codeLines = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i += 1;
      }
      const code = codeLines.join('\n');
      html.push(
        '<div class="code-block-wrap">' +
          '<div class="code-block-bar">' +
          `<span class="code-lang">${escapeHtml(langTag || 'text')}</span>` +
          '<button type="button" class="code-copy-btn">Copy</button>' +
          '</div>' +
          `<pre><code>${highlightCode(code, langTag)}</code></pre>` +
          '</div>'
      );
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

module.exports = { escapeHtml, renderInlineMarkdown, renderMarkdown, highlightCode };
