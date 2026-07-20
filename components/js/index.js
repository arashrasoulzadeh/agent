'use strict';

/**
 * The UI vocabulary every renderer shares — the CLI (ui/app.py), the
 * server (service/ui_builder.py, core/style.py), and this desktop app
 * (desktop/renderer.js). ../spec.json is the single source of truth;
 * components/__init__.py is Python's exporter of it, this is JS's. A new
 * style token, exit word, or client-local UI constant is added to
 * spec.json once and every renderer picks it up — it should never be
 * hardcoded separately in ui/app.py or desktop/renderer.js again.
 *
 * CommonJS (not ESM) so it loads the same way from Electron's main
 * process, a preload script, or a plain Node script with no build step.
 */

const fs = require('fs');
const path = require('path');

const spec = JSON.parse(
  fs.readFileSync(path.join(__dirname, '..', 'spec.json'), 'utf8')
);

module.exports = {
  spec,
  STYLE_TOKENS: spec.styleTokens,
  RICH_COLORS: spec.richColors,
  NODE_TYPES: spec.nodeTypes,
  RESERVED_IDS: spec.reservedIds,
  EXIT_COMMANDS: spec.exitCommands,
  REPLY_PLACEHOLDERS: spec.replyPlaceholders,
  SPINNER_FRAMES: spec.spinnerFrames,
  CONNECTION_STATES: spec.connectionStates,
};
