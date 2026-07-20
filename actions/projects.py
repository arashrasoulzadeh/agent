"""`/projects` — list every project currently attached to this room."""

from core.action import Action, ActionContext


async def _run(ctx: ActionContext, args: list[str]) -> None:
    projects = ctx.project_list()
    if not projects:
        await ctx.info("No projects attached.")
        return
    lines = ["Attached projects:"]
    for p in sorted(projects, key=lambda p: p["name"]):
        marker = "primary" if p.get("primary") else "secondary"
        lines.append(f"  {p['name']} ({marker})  {p['path']}")
    await ctx.info("\n".join(lines))


action = Action(
    name="/projects",
    usage="/projects",
    description="List attached projects",
    kind="action",
    run=_run,
)
