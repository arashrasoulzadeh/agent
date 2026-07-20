'use strict';

/**
 * Every Node a client renders carries Rich style strings in its props
 * (e.g. "bold bright_cyan", "grey62") — server/ui_builder.py builds them
 * assuming a Rich/Textual console on the other end. ui/app.py gets that
 * for free; a DOM-based renderer (desktop/renderer.js) doesn't, so this
 * is the one place a Rich style string is turned into CSS.
 *
 * The color table isn't required from ../spec.json here — it's injected
 * via setRichColors(), called once with the `richColors` field of the
 * server's /ui/spec response (see wire/routes.py's ui_spec()). Nothing
 * in this file reads a local file or bundles server config: it's pure
 * interpreter code, the one thing that does have to ship with the
 * client (there's no way to "fetch" the ability to parse a string), but
 * the data it interprets against always comes from the live connection.
 *
 * Deliberately approximate, not a full Rich-compatible parser: covers
 * every color/modifier actually used by service/ui_builder.py plus a
 * plain "greyNN"/"grayNN" ramp (linear, not Rich's real 24-step
 * non-linear scale — close enough for a GUI, not meant to be a terminal
 * emulator). Unknown tokens are silently ignored rather than throwing,
 * so a future style string the table doesn't know yet degrades to
 * default styling instead of breaking the renderer.
 */

let richColors = {};

/** Called once with /ui/spec's `richColors` field before any node renders. */
function setRichColors(colors) {
  richColors = colors || {};
}

function greyHex(percent) {
  const clamped = Math.max(0, Math.min(100, percent));
  const value = Math.round((clamped / 100) * 255);
  const hex = value.toString(16).padStart(2, '0');
  return `#${hex}${hex}${hex}`;
}

function colorToHex(name) {
  if (!name) return null;
  const greyMatch = /^gr[ae]y(\d{1,3})$/.exec(name);
  if (greyMatch) return greyHex(parseInt(greyMatch[1], 10));
  return richColors[name] || null;
}

/** styleString -> a plain object of camelCase CSS properties. */
function parseRichStyle(styleString) {
  const css = {};
  if (!styleString) return css;
  for (const token of styleString.trim().split(/\s+/).filter(Boolean)) {
    switch (token) {
      case 'bold':
        css.fontWeight = '600';
        break;
      case 'italic':
        css.fontStyle = 'italic';
        break;
      case 'underline':
        css.textDecoration = 'underline';
        break;
      case 'strike':
        css.textDecoration = 'line-through';
        break;
      case 'dim':
        css.opacity = '0.6';
        break;
      default: {
        const hex = colorToHex(token);
        if (hex) css.color = hex;
      }
    }
  }
  return css;
}

/** Applies a Rich style string directly to an element's inline style. */
function applyRichStyle(el, styleString) {
  const css = parseRichStyle(styleString);
  for (const [prop, value] of Object.entries(css)) {
    el.style[prop] = value;
  }
}

module.exports = { parseRichStyle, applyRichStyle, colorToHex, setRichColors };
