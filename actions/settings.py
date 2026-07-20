"""`/settings` — open the settings screen."""

from core.action import Action, ActionContext


async def _run(ctx: ActionContext, args: list[str]) -> None:
    await ctx.show_settings()


action = Action(
    name="/settings",
    usage="/settings",
    description="Open the settings screen",
    kind="ui",
    run=_run,
)
