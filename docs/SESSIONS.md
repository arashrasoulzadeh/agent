# Sessions and project metadata

A **session** is a named directory (under `~/.agent-session-root/` by
default) that tracks one or more attached **projects** — real source
directories on disk. Each attached project gets a lightweight, always-
up-to-date metadata mirror: per-file path, size, mtime, content hash,
detected language, and a derived-data slot — for a language with a
registered extractor (`workspace/signatures.py`; Python today, via the
stdlib `ast` module), automatically filled with that file's top-level
function/class/variable *signatures* and a one-line docstring summary,
never a full function/method body or the surrounding statements. A
background watcher keeps the whole mirror in sync as files change, so a
later step (an LLM prompt, a search, anything) can work from cheap
structural metadata instead of re-reading — or re-summarizing — a
project's files from scratch every time.

`service/rooms.py` wires this into the agent's own conversation flow at
bootstrap time — see "Room bootstrap integration" below for how a room's
project maps onto a session, how a cached prior analysis lets a room skip
the LLM entirely, and how every bootstrap seeds the agent from a
lightweight, two-tier view of this metadata rather than dumping every
file's full signatures into one prompt.

## Quickstart

```bash
agent-session create test_session
agent-session attach test_session ~/code/project-one --name p1
agent-session attach test_session ~/code/project-two --name p2
agent-session status test_session
agent-session load test_session      # foreground; Ctrl-C to stop
```

In another terminal, while `load` is running:

```bash
agent-session serialize test_session --project p1 --glob "*.py"
```

## Package layout

| Module | Does |
| --- | --- |
| `config.py` | `SESSION_ROOT` — `AGENT_SESSION_ROOT` env var, default `~/.agent-session-root/`. |
| `ignore.py` | `IgnoreRules`: `.gitignore` (via `pathspec`) + a configurable extra pattern list + binary-extension/oversized-file/symlink exclusion, plus a hardcoded, non-overridable `.env`/`.env.*` exclusion (`core.guard.is_secret()`). |
| `indexer.py` | `ProjectIndexer`: `build()` (full walk), `reconcile(existing)` (full walk, cheap mtime+size pre-check before ever rehashing), `update_paths()`/`resync_subtree()`/`rename_subtree()` (incremental, watcher-driven). Calls into `signatures.py` for any new/changed file whose language has a registered extractor. |
| `signatures.py` | `extract_signatures(language, source)`: parses source (Python via `ast`, never executes it) into top-level function/class/variable signatures, each with a one-line docstring summary, plus the file's own module-level docstring first line (`module_summary`) — never bodies. Returns `None` for an unregistered language, a syntax error, or a file with nothing at all to report; never raises. |
| `index_repository.py` | `IndexRepository`: atomic `index.json` read/write, one file per attached project. |
| `manifest_repository.py` | `ManifestRepository`: atomic `manifest.json` read/write, one file per session. |
| `synthesis_repository.py` | `SynthesisRepository`: atomic `synthesis.json` read/write, one file per attached project — the cached bootstrap answer `service/rooms.py` checks before spending an LLM call. |
| `watcher.py` | `ProjectWatcher`: wraps a `watchdog` `Observer`, debounces bursts of events, flushes to disk. |
| `manager.py` | `SessionManager`: `create`/`load`/`attach`/`detach`/`list_sessions`/`list_projects`/`status`/`set_derived`. |
| `serialize.py` | Two-tier metadata serialization for an LLM prompt: `to_lightweight_context()` (path + one-line description per file, no signatures — tier 1, what every bootstrap seeds from) and `to_prompt_context()` (the above plus every file's full signatures — tier 2, used by the `agent-session serialize` CLI and by `render_file_signatures()` for one file at a time). |
| `cli.py` | The `agent-session` console script. |

Pure data shapes (`FileMetadata`, `ProjectIndex`, `ProjectAttachment`,
`SessionManifest`, `ProjectSynthesis`) live in the project's shared
`models/` package, not in `workspace/` itself — `models/` is "pure data
shapes, no behavior," and these are exactly that; the behavior that
builds and maintains them is everything above.

## On-disk layout

```
~/.agent-session-root/
  test_session/
    manifest.json
    p1/
      index.json
    p2/
      index.json
```

`manifest.json` holds the session's identity and its attached projects
(name, absolute root path, when it was attached, extra ignore patterns).
Each project's `index.json` is its full metadata mirror, keyed by
relative, forward-slash-normalized path:

```json
{
  "project_name": "p1",
  "project_root": "/home/user/work/project-one",
  "last_sync": "2026-07-16T10:05:12+00:00",
  "files": {
    "src/foo/bar.py": {
      "size": 4213,
      "mtime": 1737000000.123456,
      "sha256": "e3b0c44298fc1c14...",
      "language": "python",
      "binary": false,
      "derived": {
        "summary": "Small arithmetic helpers.",
        "signatures": {
          "functions": [
            {
              "name": "add",
              "async": false,
              "params": [
                {"name": "a", "annotation": "int", "default": null},
                {"name": "b", "annotation": "int", "default": "0"}
              ],
              "returns": "int",
              "decorators": [],
              "summary": "Add two numbers."
            }
          ],
          "classes": [],
          "variables": []
        }
      }
    }
  }
}
```

`derived["summary"]` is populated automatically (`workspace/indexer.py`'s
`_extract_derived()`), free and deterministic, no LLM call: a file's own
module docstring first line when it has one, else a synthesized blurb
from declaration counts (`"2 functions, 1 class"`), else nothing.
`SessionManager.set_derived()` can still overwrite this manually (e.g. an
LLM-generated summary) — that write path is untouched; a real content
change always recomputes fresh, same as `signatures` itself.
`to_lightweight_context()` renders only this `summary` per file; the
full `signatures` block is tier 2, rendered by `to_prompt_context()`
(every file) or `render_file_signatures()` (one file, on demand).

A single `index.json` per project, not one file per mirrored source
file: cheap prompt serialization (one read, already-parsed) and cheap
incremental updates (the index stays in memory for the life of a loaded
session; a burst of edits costs one debounced flush, not one per file) —
at the cost of every flush rewriting the whole file. See
`workspace/watcher.py`'s module docstring for the full reasoning, and
the section below for why that's safe.

## Two invariants worth understanding

**`index.json` is a cache, never a ledger.** Correctness always comes
from the next full `reconcile()` — a `ProjectWatcher` is a liveness/
performance optimization for a session that stays loaded, not a
durability mechanism. Killing the process at any point leaves, at worst,
an old-but-valid `index.json` plus a harmless orphaned `.tmp` file
(atomic `tmp`-then-`os.replace`, the same pattern
`service/room_repository.py` uses).

**Reconcile happens before a watcher ever starts, synchronously.**
`SessionManager.attach()` and `SessionManager.load()` both build/
reconcile a project's index and persist it *before* constructing that
project's `ProjectWatcher` — this sequencing, not locking, is what keeps
a fresh watcher from racing a concurrent startup scan. A `ProjectWatcher`
never does its own initial reconcile; it assumes the index it's handed
is already a valid starting point.

Derived data is recomputed from scratch whenever a file's content hash
changes — structural signatures (cheap and deterministic, so they're
simply regenerated) for a language with a registered extractor, else
`None` — and preserved untouched when only mtime/size changed with
identical content (a `touch`, or an editor rewriting the same bytes).
Stale derived data must never survive a real content change, which
includes a manually-set `"summary"`: it described the old content, so it
does not survive either. `SessionManager.set_derived()` is the one write
path for anything beyond auto-extracted signatures (an LLM-generated
summary, say), and is itself a no-op if the hash it's given no longer
matches, so a slow external writer can never attach stale data to
content that's since changed.

## Room bootstrap integration

Every room (`service/rooms.py`) has exactly one project, attached into
this store under a session named after the room's own id — already an
md5 of the resolved project path (`room_id_for_path()`) — with the
project itself always named `"project"` (`WORKSPACE_PROJECT_NAME`), so
the two ids line up without a second lookup table:

```
~/.agent-session-root/
  <room_id>/
    manifest.json
    project/
      index.json
      synthesis.json
```

`Room._ensure_workspace_project()` attaches (idempotently) and
synchronously reconciles this project every time a room becomes active —
freshly created, resumed, or re-bootstrapped — and starts a background
`ProjectWatcher` for it, tracked for the life of the server process
(`service/rooms.py`'s `ROOM_WATCHERS`, stopped by
`stop_all_room_watchers()` in `wire/app.py`'s shutdown). This is what
keeps per-file signatures fresh regardless of what the bootstrap
decision below does.

**Every bootstrap seeds from the lightweight (tier-1) index first**
(`Room._collect_and_start()`, via `_workspace_context()` /
`to_lightweight_context()`) — regardless of whether a cached answer
exists, since the reconcile above already guarantees the index is built
by this point. `agent/`'s own `ContextCollector`/`tool.metadata` path
(a shallow, non-recursive single-directory listing) is no longer used by
Room at all; the compact, per-file-description map is what every
analysis — first-ever, cached, or a confirmed resync — starts from. Full
per-file structural detail (functions/classes/variables) is never sent
up front; the agent fetches it on demand, one file at a time, via the
`describe` tool (`tool/describe.py`) before falling back to `cat` for
real source — see "Two-tier metadata" below.

After that seeding, look up a cached `ProjectSynthesis` (`synthesis.json`)
to decide whether an LLM call is needed at all:

- **No cache** — the session is seeded, but there's no cached answer to
  reuse; the caller still runs a real `ask()`.
- **Cache hit, drift below `RESYNC_CHANGE_THRESHOLD`** (20% of tracked
  files added/removed/content-changed since the cache was made) —
  answer with the cached text directly. No LLM call happens at all —
  the actual token-saving payoff, and also what lets the *next* room for
  the same path skip the LLM even if that room's own `rooms/{id}.json`
  was reset (there's no resumable conversation, but the workspace-level
  cache survives independently).
- **Cache hit, drift at/above the threshold** — the cached answer is
  flagged instead of trusted silently: the room sets
  `resync_suggested = True` and emits a `resync.suggested` event
  (`wire/events.py`) with the change counts, deferring to the user via
  `/resync` (`wire/routes.py`) rather than silently serving stale data
  or silently re-spending tokens. `confirm: true` re-runs a real
  analysis (seeded the same way, via `_collect_and_start_for_resync()`)
  and refreshes `synthesis.json` (`Room.run_resync()`); `confirm: false`
  just clears the flag. See `docs/PROTOCOL.md` for the wire-level shape
  of both.

## Two-tier metadata: lightweight map + on-demand signatures

`to_prompt_context()`'s full-signature dump (every file's functions/
classes/variables, inline, every turn) doesn't scale — for anything but
a small project it's simply too much of the token budget spent on files
the agent never ends up needing. Two tiers instead, matching how the
agent already treats real file content (map first, read on demand):

1. **Always-on, cheap** — `to_lightweight_context()`: path + one-line
   `derived["summary"]` per file, nothing else. This is what every
   bootstrap seeds the analyst's session with.
2. **On-demand, one file at a time** — the `describe` tool
   (`tool/describe.py`), an agent-visible LangChain tool (auto-
   discovered by `tool/registry.py`, no registration step) that returns
   one file's full `render_file_signatures()` output — cheaper than
   reading the real file, richer than the map's one-line description.
   It needs to know which room's workspace project to look up without
   `service/rooms.py` injecting anything (tools are bare
   `@tool`-decorated functions, nothing to inject into) — `core/
   room_context.py` is a contextvar for exactly that, set at the same
   worker-thread call sites `core/guard.py`'s project root already is
   (`Room._ask_blocking()`, `_collect_and_start()`,
   `_collect_and_start_for_resync()`), for the same isolation reason:
   `asyncio.to_thread` copies the calling context into the new thread,
   so a room id set inside one room's worker thread stays invisible to
   every other concurrently running room's thread.
3. **On-demand, real source** (unchanged) — `cat`, for when structure
   alone isn't enough.

`agent/prompts.py`'s `SYSTEM_PROMPT` spells out the escalation order:
the map's description first, `describe` for shape, `cat` for real
content — never skip straight to `cat` when `describe` would answer the
question.

A fresh analysis (whether the first ever, or a confirmed resync) caches
its result afterward as a deliberately fire-and-forget tail step
(`Room._cache_synthesis()`, run after `turn_active` is already cleared
and the `answer` event already sent) — caching is an enhancement, not
part of the turn's own correctness, so it must never hold a client's
next `/prompt` waiting on a disk write.

## Not (yet) part of this

- **No config file.** `SESSION_ROOT` is env-var-only
  (`AGENT_SESSION_ROOT`), matching `wire/config.py`'s own precedent.
- **Signature extraction is Python-only.** Other languages get no
  extractor yet (`workspace/signatures.py`'s `EXTRACTORS` registry is
  built to add more without a redesign, but nothing else is registered
  today) — their files' `derived` stays `None` unless something calls
  `set_derived()` for them. There's also no import-graph extraction —
  signatures (what a function/class/variable *declares*, not what it
  does at runtime) plus a module's own docstring are all that's
  automatic; an LLM-generated one-line summary is still only possible
  manually, via `set_derived()`.
- **A running `load` doesn't hot-reload manifest changes.** `attach`/
  `detach` from a separate invocation won't affect an already-running
  foreground `load`'s watcher set; restart it to pick up changes.

## Testing

`tests/test_workspace_ignore.py`, `test_workspace_indexer.py`,
`test_workspace_signatures.py`, `test_workspace_watcher.py` (a real
`watchdog` `Observer` against a temp directory), `test_workspace_manager.py`,
`test_workspace_serialize.py`, and `test_workspace_synthesis_repository.py`
cover ignore rules, reconciliation/invalidation logic, signature
extraction (including module-docstring capture and the synthesized-blurb
fallback), the watcher's debounce/flush/graceful-stop behavior,
session/project lifecycle, prompt serialization (both tiers —
`to_prompt_context()` and `to_lightweight_context()` — plus
`render_file_signatures()`), and the synthesis cache's atomic save/load,
respectively — none of them touch a real LLM or spend an API token.

The room-bootstrap integration itself is covered from the `service/`
side: `tests/test_rooms_cache.py` unit-tests the change-fraction/
resync-threshold boundary logic in isolation, and
`tests/test_server.py`'s `TestWorkspaceCacheIntegration` exercises the
full flow end to end over a real (test) WebSocket connection — a
bootstrap populating the cache, a later room for the same path skipping
the stub pipeline's `.ask()` entirely on a cache hit, a drifted project
getting `resync.suggested` instead of a silent stale answer, a confirmed
`/resync` re-running the (stub) analysis and refreshing the cache, and a
first-ever bootstrap proven to seed from the lightweight tier (no
rendered function signature in the seeded context) rather than the old
shallow listing or the full-signature tier — all through
`tests/stubs.py`'s `StubPipeline`, never a real LLM call. `tests/
test_core_room_context.py` and `tests/test_tool_describe.py` cover the
new per-room contextvar (including thread isolation via
`asyncio.to_thread`, mirroring `core/guard.py`'s own) and the `describe`
tool (full detail, the no-signatures fallback, path confinement, and
graceful errors for an untracked path or no active room) respectively.
