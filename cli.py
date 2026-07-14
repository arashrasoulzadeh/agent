"""Command-line interface.

Collects a project's context, builds an initial understanding of it, then
opens an interactive prompt for follow-up questions about the project.
"""

import sys

from helpers import message, output, think
from pipeline import ProjectPipeline

EXIT_COMMANDS = {"exit", "quit", "q"}

BOOTSTRAP_QUERY = (
    "Give me a clear overview of this project: what it is, its purpose, "
    "its tech stack, and how it's organized. Read whatever files you need "
    "to be confident in your answer."
)


def prompt_for_path() -> str:
    path = input("Project path: ").strip()
    return path or "."


def run_session(pipeline: ProjectPipeline, path: str) -> None:
    think(f"Analyzing {path} ...")
    pipeline.start(path)
    print()
    message(BOOTSTRAP_QUERY)
    output(pipeline.ask(BOOTSTRAP_QUERY))

    print("\nAsk follow-up questions about the project ('exit' to quit).")
    while True:
        try:
            question = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question or question.lower() in EXIT_COMMANDS:
            break
        message(question)
        output(pipeline.ask(question))


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else prompt_for_path()
    run_session(ProjectPipeline(), path)


if __name__ == "__main__":
    main()
