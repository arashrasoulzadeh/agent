"""Command-line interface.

Builds the analysis pipeline and hands it to the full-screen agent TUI
(see ui/app.py), which collects the project's context, asks a bootstrap
question, then holds an interactive conversation.
"""

import argparse

from pipeline import ProjectPipeline
from ui.app import AgentApp
from ui.engine import set_verbose


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agent",
        description="Ask questions about a codebase.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Project to analyze. Prompts for one if omitted.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the raw LLM requests and responses in the transcript.",
    )
    args = parser.parse_args(argv)

    if args.verbose:
        set_verbose(True)

    # This happens before the TUI takes the screen, so a plain blocking
    # prompt is fine here.
    path = args.path or input("Project path [.]: ").strip() or "."

    AgentApp(ProjectPipeline(), path).run()


if __name__ == "__main__":
    main()
