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

    The primitive :func:`resolve_slurm_options` is written in terms of. Prefer
    that: it names the four layers, so a caller cannot supply them in the
    wrong order or leave one out. This remains available for the cases that
    genuinely have some other number of layers to merge.

    Example:
        >>> layer_slurm_options({'partition': 'a', 'gres': 'gpu:1'},
        ...                     {'partition': 'b'})
        {'partition': 'b', 'gres': 'gpu:1'}
    """
    merged: dict[str, Any] = {}
    for layer in layers:
        merged.update(coerce_slurm_options(layer))
    return merged


def resolve_slurm_options(
    *,
    pipeline_base: SlurmOptions = None,
    row_global: SlurmOptions = None,
    node_default: SlurmOptions = None,
    node_override: SlurmOptions = None,
) -> dict[str, Any]:
    """
    The effective Slurm request for one process, from its four sources.

    This is the single authority for the documented precedence. It used to be
    computed in three places -- the compiler folded a row-global mapping into
    node-local configuration, the runtime layered pipeline-wide options over
    whatever the node had resolved, and arbitration re-layered the two halves
    a third time to compare them. Each site knew a different subset of the
    layers, which is why ``node.slurm_options`` meant "node-level" on one
    scheduling path and "node-level plus row-global" on the other.

    Keyword-only and pure: the layers are named rather than positional
    because their order *is* the semantics, and nothing here reads or writes
    state, so the result can be computed wherever all four are known and then
    stored on the node as
    :attr:`~kwdagger.pipeline.ProcessNode.effective_slurm_options`.

    Args:
        pipeline_base (SlurmOptions): the CLI's ``--slurm_options`` or a
            parameter file's top-level ``slurm_options``. The persistent
            default every row starts from.

        row_global (SlurmOptions): a matrix row's top-level
            ``__slurm_options__``.

        node_default (SlurmOptions): the node's own declared default, from
            its class or its YAML.

        node_override (SlurmOptions): that row's per-node
            ``<node>.__slurm_options__``.

    Returns:
        dict: the merged request, most specific layer winning key-wise.

    Example:
        >>> resolve_slurm_options(
        ...     pipeline_base={'partition': 'general', 'account': 'x'},
        ...     row_global={'partition': 'debug'},
        ...     node_default={'gres': 'gpu:1'},
        ...     node_override={'gres': 'gpu:2'})
        {'partition': 'debug', 'account': 'x', 'gres': 'gpu:2'}
    """
    return layer_slurm_options(
        pipeline_base, row_global, node_default, node_override
    )
