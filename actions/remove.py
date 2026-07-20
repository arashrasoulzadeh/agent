"""`/remove <name>` — detach a project."""

from core.action import Action, ActionContext


async def _run(ctx: ActionContext, args: list[str]) -> None:
    if not args:
        await ctx.info("Usage: /remove <name>")
        return
    await ctx.remove_project(args[0])


action = Action(
    name="/remove",
    usage="/remove <name>",
    description="Detach a project",
    kind="action",
    run=_run,
)
