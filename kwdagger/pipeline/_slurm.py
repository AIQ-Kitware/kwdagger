"""
Coercion for the Slurm options a pipeline or a node may declare.

A leaf: both the process layer (which stores per-node options) and the runtime
submitter (which merges pipeline-wide options into each job) need this, and
neither should have to import the other to get it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias

import kwutil

#: Slurm options as a caller may write them: a mapping, a YAML string, or
#: nothing. :func:`coerce_slurm_options` is what turns any of those into the
#: dict the rest of the package passes around.
SlurmOptions: TypeAlias = 'Mapping[str, Any] | str | None'


def coerce_slurm_options(slurm_options: SlurmOptions) -> dict[str, Any]:
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
