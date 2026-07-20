'use strict';

/**
 * Unit tests for richStyle.js's Rich-style-string -> CSS parser. Run
 * directly under plain Node (`node --test components/js/richStyle.test.js`)
 * — no DOM needed: applyRichStyle only ever does `el.style[prop] = value`,
 * so a plain `{ style: {} }` object stands in for a real element.
 *
 * The last group loads the *real* ../spec.json and feeds its actual
 * richColors table through setRichColors() — the cross-language check
 * components/__init__.py's own docstring promises: every color literal
 * service/ui_builder.py actually emits (grep it if this list ever goes
 * stale) must resolve to a real hex value through this exact parser, not
 * just through some hand-picked test fixture.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parseRichStyle, applyRichStyle, colorToHex, setRichColors } = require('./richStyle.js');

test.afterEach(() => {
  setRichColors({}); // tests must not leak state into each other
});

test('parseRichStyle: empty/null/undefined style strings are safe no-ops', () => {
  assert.deepEqual(parseRichStyle(''), {});
  assert.deepEqual(parseRichStyle(null), {});
  assert.deepEqual(parseRichStyle(undefined), {});
});

test('parseRichStyle: each modifier maps to its own CSS property', () => {
  assert.deepEqual(parseRichStyle('bold'), { fontWeight: '600' });
  assert.deepEqual(parseRichStyle('italic'), { fontStyle: 'italic' });
  assert.deepEqual(parseRichStyle('underline'), { textDecoration: 'underline' });
  assert.deepEqual(parseRichStyle('strike'), { textDecoration: 'line-through' });
  assert.deepEqual(parseRichStyle('dim'), { opacity: '0.6' });
});

test('parseRichStyle: a color token sets color, unaffected by injected table state', () => {
  setRichColors({ bright_cyan: '#00ffff' });
  assert.deepEqual(parseRichStyle('bright_cyan'), { color: '#00ffff' });
});

test('parseRichStyle: modifier + color combine into one object', () => {
  setRichColors({ bright_yellow: '#ffff00' });
  assert.deepEqual(parseRichStyle('bold bright_yellow'), {
    fontWeight: '600',
    color: '#ffff00',
  });
});

test('parseRichStyle: an unknown token is silently ignored, never throws', () => {
  assert.deepEqual(parseRichStyle('some_totally_unknown_token'), {});
});

test('parseRichStyle: a color unknown to the current table degrades to no color, not a crash', () => {
  setRichColors({}); // nothing registered
  assert.deepEqual(parseRichStyle('bright_cyan'), {});
});

test('colorToHex: a bare "greyNN"/"grayNN" resolves via the linear ramp, no table needed', () => {
  assert.equal(colorToHex('grey0'), '#000000');
  assert.equal(colorToHex('grey100'), '#ffffff');
  assert.equal(colorToHex('gray50'), colorToHex('grey50')); // both spellings accepted
});

test('colorToHex: grey ramp clamps out-of-range percentages instead of producing garbage', () => {
  assert.equal(colorToHex('grey150'), '#ffffff');
});

test('colorToHex: null/empty input returns null, not a throw', () => {
  assert.equal(colorToHex(null), null);
  assert.equal(colorToHex(''), null);
});

test('applyRichStyle: writes each CSS property onto the target object', () => {
  setRichColors({ red: '#800000' });
  const fakeEl = { style: {} };
  applyRichStyle(fakeEl, 'bold red');
  assert.equal(fakeEl.style.fontWeight, '600');
  assert.equal(fakeEl.style.color, '#800000');
});

test('applyRichStyle: an empty style string touches nothing', () => {
  const fakeEl = { style: {} };
  applyRichStyle(fakeEl, '');
  assert.deepEqual(fakeEl.style, {});
});

test('cross-check against the real components/spec.json: every color service/ui_builder.py actually emits resolves', () => {
  const spec = JSON.parse(
    fs.readFileSync(path.join(__dirname, '..', 'spec.json'), 'utf8')
  );
  setRichColors(spec.richColors);

  // Pulled from service/ui_builder.py's literal style/border_style
  // strings — grep for "grey" and the bright_* names there if this ever
  // needs updating; grey35/50/62 use the ramp (no table entry needed),
  // the rest must be real entries in spec.json's richColors.
  const usedTokens = [
    'grey35', 'grey50', 'grey62',
    'bright_cyan', 'bright_green', 'bright_yellow', 'bright_white', 'red',
  ];
  for (const token of usedTokens) {
    const hex = colorToHex(token);
    assert.match(hex, /^#[0-9a-f]{6}$/, `expected ${token} to resolve to a hex color, got ${hex}`);
  }
});
