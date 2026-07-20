'use strict';

/**
 * Unit tests for markdown.js's pure logic — no DOM, no Electron, run
 * directly under plain Node (`node --test desktop/markdown.test.js`).
 * Asserts on the rendered HTML string shape, not on visual output — a
 * real render is what desktop/README.md's manual verification steps
 * (and this repo's own use of the app) are for.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { escapeHtml, renderInlineMarkdown, renderMarkdown, highlightCode } = require('./markdown.js');

test('escapeHtml escapes the five HTML-significant characters', () => {
  assert.equal(escapeHtml('<script>alert("hi") & run</script>'),
    '&lt;script&gt;alert(&quot;hi&quot;) &amp; run&lt;/script&gt;');
});

test('escapeHtml leaves plain text untouched', () => {
  assert.equal(escapeHtml('just words, no markup'), 'just words, no markup');
});

test('renderInlineMarkdown: bold, italic, and inline code', () => {
  assert.equal(renderInlineMarkdown('**bold** and *italic* and `code`'),
    '<strong>bold</strong> and <em>italic</em> and <code>code</code>');
});

test('renderInlineMarkdown: an http(s) link becomes a safe external anchor', () => {
  const out = renderInlineMarkdown('see [the docs](https://example.com/x)');
  assert.match(out, /<a href="https:\/\/example\.com\/x" target="_blank" rel="noopener noreferrer">the docs<\/a>/);
});

test('renderInlineMarkdown: a non-http(s) link is left as literal escaped text, not an anchor', () => {
  // javascript:/data: URIs must never become a clickable <a> — the whole
  // point of restricting the link pattern to https?:// in the first place.
  const out = renderInlineMarkdown('[click me](javascript:alert(1))');
  assert.doesNotMatch(out, /<a /);
  assert.match(out, /\[click me\]\(javascript:alert\(1\)\)/);
});

test('renderInlineMarkdown: model/user text can never inject raw HTML', () => {
  const out = renderInlineMarkdown('<img src=x onerror=alert(1)>');
  assert.doesNotMatch(out, /<img/);
  assert.match(out, /&lt;img/);
});

test('renderMarkdown: headings h1 through h3', () => {
  const out = renderMarkdown('# One\n## Two\n### Three');
  assert.match(out, /<h1>One<\/h1>/);
  assert.match(out, /<h2>Two<\/h2>/);
  assert.match(out, /<h3>Three<\/h3>/);
});

test('renderMarkdown: a bare paragraph is wrapped in <p>', () => {
  assert.equal(renderMarkdown('hello world'), '<p>hello world</p>');
});

test('renderMarkdown: consecutive unordered list items share one <ul>', () => {
  const out = renderMarkdown('- a\n- b\n- c');
  assert.equal(out, '<ul>\n<li>a</li>\n<li>b</li>\n<li>c</li>\n</ul>');
});

test('renderMarkdown: consecutive ordered list items share one <ol>', () => {
  const out = renderMarkdown('1. a\n2. b');
  assert.equal(out, '<ol>\n<li>a</li>\n<li>b</li>\n</ol>');
});

test('renderMarkdown: switching list type closes the previous list', () => {
  const out = renderMarkdown('- a\n1. b');
  assert.equal(out, '<ul>\n<li>a</li>\n</ul>\n<ol>\n<li>b</li>\n</ol>');
});

test('renderMarkdown: a blockquote line', () => {
  assert.equal(renderMarkdown('> quoted'), '<blockquote>quoted</blockquote>');
});

test('renderMarkdown: blank lines never produce empty paragraphs', () => {
  const out = renderMarkdown('a\n\n\nb');
  assert.equal(out, '<p>a</p>\n<p>b</p>');
});

test('renderMarkdown: a fenced code block gets a language label and a copy button', () => {
  const out = renderMarkdown('```python\nx = 1\n```');
  assert.match(out, /<span class="code-lang">python<\/span>/);
  assert.match(out, /<button type="button" class="code-copy-btn">Copy<\/button>/);
  assert.match(out, /<pre><code>/);
});

test('renderMarkdown: a fenced code block with no language falls back to "text"', () => {
  const out = renderMarkdown('```\nplain\n```');
  assert.match(out, /<span class="code-lang">text<\/span>/);
});

test('renderMarkdown: code fence content is escaped, never executed as markup', () => {
  const out = renderMarkdown('```\n<script>evil()</script>\n```');
  assert.doesNotMatch(out, /<script>evil/);
  assert.match(out, /&lt;script&gt;evil/);
});

test('highlightCode: keywords, strings, numbers, and function calls are tagged', () => {
  const out = highlightCode('def resolve(path="."):\n    return 1', 'python');
  assert.match(out, /<span class="tok-keyword">def<\/span>/);
  assert.match(out, /<span class="tok-function">resolve<\/span>/);
  assert.match(out, /<span class="tok-string">&quot;\.&quot;<\/span>/);
  assert.match(out, /<span class="tok-keyword">return<\/span>/);
  assert.match(out, /<span class="tok-number">1<\/span>/);
});

test('highlightCode: a line comment is tagged, and nothing after it is treated as code', () => {
  const out = highlightCode('x = 1  # a real comment, not code', 'python');
  assert.match(out, /<span class="tok-comment"># a real comment, not code<\/span>/);
});

test('highlightCode: a language alias resolves to its canonical grammar', () => {
  // "js" isn't a key in LANG_KEYWORDS itself — only "javascript" is;
  // this proves the alias table actually gets consulted.
  const out = highlightCode('const x = 1;', 'js');
  assert.match(out, /<span class="tok-keyword">const<\/span>/);
});

test('highlightCode: an unrecognized language degrades to escaped plain text', () => {
  const out = highlightCode('<weird> stuff', 'brainfuck');
  assert.equal(out, '&lt;weird&gt; stuff');
});

test('highlightCode: no language tag at all still returns safely escaped text', () => {
  assert.equal(highlightCode('<b>x</b>', ''), '&lt;b&gt;x&lt;/b&gt;');
});
