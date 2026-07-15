"""Shared infrastructure used across agent-capability modules.

Not a capability itself — `guard` enforces what the agent may touch, and
`registry` discovers the modules that implement what it can do. Both live
here rather than in `modules/` so the auto-discovery in `modules/__init__`
never mistakes them for a capability to register.
"""
