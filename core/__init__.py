"""Shared, dependency-free kernel used across every other package.

Not a capability itself — `guard` enforces what the agent may touch,
`discovery` is the generic "import every .py file in a directory" scan
that both `tool/registry.py` and `hooks/loader.py` reuse, `module` is the
Lifecycle (init/start/stop) contract, `ask_context` is the contextvar
plumbing behind the `ask` tool, and `text` is a small formatting helper.
Nothing here depends on tool/agent/models/wire/service/ui/hooks — every
one of those may depend on core/, never the other way around.
"""
