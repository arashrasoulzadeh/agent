"""CLI for the session/project metadata store.

Console script `agent-session` (or `python -m workspace.cli`). Every
subcommand except `load` is one-shot; `load` runs in the foreground,
starting a watcher per attached project, until SIGINT/SIGTERM —
mirroring `agent-server`'s (wire/__main__.py) foreground-process shape —
at which point every watcher stops cleanly (flushing any pending
debounced changes first, see workspace/watcher.py's stop()) before the
process exits. This is a separate console script, not a subcommand of
`agent`, matching this project's one-script-per-concern convention
(`agent` vs `agent-server`).
"""

import argparse
import signal
import sys
import threading
from pathlib import Path

from workspace.manager import (
    ProjectNameConflict,
    ProjectNotFound,
    SessionAlreadyExists,
    SessionManager,
    SessionNotFound,
)
from workspace.serialize import to_prompt_context


def _cmd_create(args: argparse.Namespace, manager: SessionManager) -> None:
    manager.create(args.name)
    print(f"created session {args.name!r}")


def _cmd_attach(args: argparse.Namespace, manager: SessionManager) -> None:
    attachment = manager.attach(
        args.name,
        args.project_path,
        project_name=args.project_name,
        ignore_extra=args.ignore,
    )
    print(
        f"attached {attachment.root!r} as {attachment.name!r} in session {args.name!r}"
    )


def _cmd_detach(args: argparse.Namespace, manager: SessionManager) -> None:
    manager.detach(args.name, args.project_name)
    print(f"detached {args.project_name!r} from session {args.name!r}")


def _cmd_list_sessions(args: argparse.Namespace, manager: SessionManager) -> None:
    for name in manager.list_sessions():
        print(name)


def _cmd_list_projects(args: argparse.Namespace, manager: SessionManager) -> None:
    for attachment in manager.list_projects(args.name):
        print(
            f"{attachment.name}\t{attachment.root}\tattached {attachment.attached_at}"
        )


def _cmd_status(args: argparse.Namespace, manager: SessionManager) -> None:
    status = manager.status(args.name, project_name=args.project_name)
    for project in status["projects"]:
        print(
            f"{project['name']}: {project['file_count']} files, "
            f"last synced {project['last_sync']}"
        )


def _cmd_serialize(args: argparse.Namespace, manager: SessionManager) -> None:
    text = to_prompt_context(
        args.name,
        project=args.project_name,
        subtree=args.subtree,
        glob=args.glob,
        session_root=manager.session_root,
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


def _cmd_load(args: argparse.Namespace, manager: SessionManager) -> None:
    loaded = manager.load(args.name)
    loaded.start_watchers()
    print(f"session {args.name!r} loaded — watching {len(loaded.watchers)} project(s):")
    for project_name in loaded.watchers:
        print(f"  - {project_name}")

    stop_requested = threading.Event()

    def _handle_signal(signum, frame) -> None:
        stop_requested.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError):
            # Not every platform/thread context supports this (mirrors
            # wire/app.py's own NotImplementedError guard for the asyncio
            # equivalent); Ctrl+C still raises KeyboardInterrupt below as
            # a fallback.
            pass

    try:
        stop_requested.wait()
    except KeyboardInterrupt:
        pass

    print("shutdown requested, stopping watchers...")
    loaded.stop_watchers()
    print("stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-session",
        description="Manage sessions and their attached project metadata mirrors.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_create = subparsers.add_parser("create", help="Create a new empty session.")
    p_create.add_argument("name")
    p_create.set_defaults(func=_cmd_create)

    p_load = subparsers.add_parser(
        "load",
        help="Open a session and watch all its attached projects (foreground).",
    )
    p_load.add_argument("name")
    p_load.set_defaults(func=_cmd_load)

    p_attach = subparsers.add_parser(
        "attach", help="Attach a project root to a session."
    )
    p_attach.add_argument("name")
    p_attach.add_argument("project_path")
    p_attach.add_argument(
        "--name",
        dest="project_name",
        default=None,
        help="Short name for the project (default: its directory name).",
    )
    p_attach.add_argument(
        "--ignore",
        dest="ignore",
        action="append",
        default=[],
        help="Extra gitignore-style pattern to exclude (repeatable).",
    )
    p_attach.set_defaults(func=_cmd_attach)

    p_detach = subparsers.add_parser("detach", help="Detach a project from a session.")
    p_detach.add_argument("name")
    p_detach.add_argument("project_name")
    p_detach.set_defaults(func=_cmd_detach)

    p_list_sessions = subparsers.add_parser("list-sessions", help="List all sessions.")
    p_list_sessions.set_defaults(func=_cmd_list_sessions)

    p_list_projects = subparsers.add_parser(
        "list-projects", help="List a session's attached projects."
    )
    p_list_projects.add_argument("name")
    p_list_projects.set_defaults(func=_cmd_list_projects)

    p_status = subparsers.add_parser(
        "status", help="Show file counts and last-sync time for a session."
    )
    p_status.add_argument("name")
    p_status.add_argument("--project", dest="project_name", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_serialize = subparsers.add_parser(
        "serialize", help="Serialize metadata into a compact LLM context block."
    )
    p_serialize.add_argument("name")
    p_serialize.add_argument("--project", dest="project_name", default=None)
    p_serialize.add_argument("--subtree", default=None)
    p_serialize.add_argument("--glob", default=None)
    p_serialize.add_argument(
        "--out", default=None, help="Write to a file instead of stdout."
    )
    p_serialize.set_defaults(func=_cmd_serialize)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = SessionManager()
    try:
        args.func(args, manager)
    except (
        SessionNotFound,
        SessionAlreadyExists,
        ProjectNotFound,
        ProjectNameConflict,
    ) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
