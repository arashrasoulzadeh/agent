"""`/add <path> [name]` — attach another project to this room."""

from core.action import Action, ActionContext


async def _run(ctx: ActionContext, args: list[str]) -> None:
    if not args:
        await ctx.info("Usage: /add <path> [name]")
        return
    path = args[0]
    name = args[1] if len(args) > 1 else None
    await ctx.add_project(path, name)


action = Action(
    name="/add",
    usage="/add <path> [name]",
    description="Attach another project to this room",
    kind="action",
    run=_run,
)
