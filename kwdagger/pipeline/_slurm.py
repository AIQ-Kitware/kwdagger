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

from kwdagger.pipeline._config_values import normalize_config

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
    # Options reach this function by four routes -- the CLI, a parameter
    # file, a matrix row, a node declaration -- and only some of them have
    # crossed the configuration boundary already. Normalizing here means a
    # ``Path`` value renders the same on the sbatch line however it arrived,
    # rather than depending on which entrance it used.
    return normalize_config(slurm_options)


def layer_slurm_options(*layers: SlurmOptions) -> dict[str, Any]:
    """
    Combine Slurm option layers key-wise, least specific first.

    The layers, in order, are:

    1. the pipeline base -- the CLI's ``--slurm_options`` or a parameter
       file's top-level ``slurm_options``;
    2. a matrix row's global ``__slurm_options__``;
    3. a node's declared default, from its class or its YAML;
    4. that row's per-node ``<node>.__slurm_options__`` override.

    Defined once and called from every site that combines them, because the
    two scheduling paths used to layer differently: the compiler substituted a
    row-global mapping for a node's own instead of merging them, so adding an
    unrelated gather to a pipeline could change what resources a node asked
    for. Each caller names the layers it has; the ordering lives here.

    Example:
        >>> layer_slurm_options({'partition': 'a', 'gres': 'gpu:1'},
        ...                     {'partition': 'b'})
        {'partition': 'b', 'gres': 'gpu:1'}
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        merged.update(coerce_slurm_options(layer))
    return merged
