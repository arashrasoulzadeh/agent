"""Pipeline configuration.

Controls which steps run and how each behaves, so the same pipeline can
be reshaped without touching its wiring.
"""

from dataclasses import dataclass


@dataclass
class PipelineConfig:
    # --- step toggles ---
    collect: bool = True       # Stage 1: gather the private project map
    analyze: bool = True       # Stage 2: reason and answer the query
    synthesize: bool = True     # Stage 3: convert answer to AI-ready context

    # --- step options ---
    analysis_temperature: float = 0.0
    synthesis_temperature: float = 0.0
    synthesis_format: str = "markdown"  # "markdown" | "json"
