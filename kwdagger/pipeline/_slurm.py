"""
Coercion for the Slurm options a pipeline or a node may declare.

A leaf: both the process layer (which stores per-node options) and the runtime
submitter (which merges pipeline-wide options into each job) need this, and
neither should have to import the other to get it.
"""

from __future__ import annotations

from typing import Any

import kwutil


def coerce_slurm_options(slurm_options: Any) -> dict[str, Any]:
    """
    Normalize slurm option dictionaries.
    """
    if slurm_options is None:
        return {}
    if isinstance(slurm_options, str):
        slurm_options = kwutil.Yaml.coerce(slurm_options)
    if slurm_options is None:
        return {}
    if not isinstance(slurm_options, dict):
        raise TypeError(
            f'Expected slurm options to be a dict, got {type(slurm_options)}. {slurm_options=!r}'
        )
    return dict(slurm_options)
