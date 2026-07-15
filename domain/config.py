"""Pipeline configuration.

`stages` is what makes the per-query pipeline composable and reorderable
from configuration rather than hardcoded: it's an ordered list of names
looked up in `domain.stages.STAGE_FACTORIES` (extendable via
`register_stage()`), not a fixed sequence of method calls. Dropping
`"synthesize"` from the list disables that step; adding a custom stage's
registered name inserts it, in whatever position — nothing in
`domain/__init__.py` needs to change either way.

`collect` stays a separate toggle rather than a `stages` entry: it's the
one-time session bootstrap (ProjectPipeline.start()), not part of the
repeated per-query flow `stages` describes.
"""

from dataclasses import dataclass, field


@dataclass
class PipelineConfig:
    collect: bool = True  # bootstrap step: gather the private project map

    # The per-query pipeline, in order. Looked up in
    # domain.stages.STAGE_FACTORIES at ProjectPipeline construction time.
    stages: list[str] = field(default_factory=lambda: ["analyze", "synthesize"])

    # --- stage options ---
    analysis_temperature: float = 0.0
    synthesis_temperature: float = 0.0
    synthesis_format: str = "markdown"  # "markdown" | "json"
