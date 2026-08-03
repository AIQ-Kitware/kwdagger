"""
The core pipeline data structure for a KWDagger pipeline.

This package outlines the structure for a generic DAG of bash process nodes. It
contains examples of generic test pipelines.

The basic idea is that each bash process knows about:

    * its filepath inputs
    * its filepath outputs
    * algorithm parameters
    * performance parameters
    * the command that invokes the job

Given a set of processes, a DAG is built by connecting process ouputs to
process inputs. This DAG can then be configured with customized input paths and
parameters. The resulting jobs can then be submitted to a cmd_queue.Queue for
actual execution.

This module is the stable import surface. It was a single file until it grew
past the point where the layering was legible; the implementation now lives in
private submodules, in one direction:

.. code::

    _shell        _slurm        pure leaves: shell text, Slurm option coercion
        \\           |
         \\          |     _connections   ports, edges, gather specs
          \\         |     /
           \\        |    /
            +---> _process <---+           ProcessNode: config, identity, command
                     |
                  _compile                 the matrix becomes a concrete graph
                     |
                  _logical                 Pipeline, graphs, coercion
                     |
                  _runtime                 queue submission (deferred import)

Nothing imports this package's ``__init__`` from inside it -- submodules import
each other directly -- so there is no import cycle to work around. Where a name
lives is an implementation detail; where it is imported from is not. Import
from ``kwdagger.pipeline``.
"""

from __future__ import annotations

from typing import Any

from kwdagger.pipeline._compile import CompiledPipeline
from kwdagger.pipeline._connections import (
    Collection,
    Configurable,
    GatherConnection,
    GatherSpec,
    GroupByKey,
    InputNode,
    IONode,
    Node,
    OutputNode,
    ParamNode,
    StoredGroupByKey,
)
from kwdagger.pipeline._logical import Pipeline, coerce_pipeline
from kwdagger.pipeline._process import (
    ProcessNode,
    memoize_configured_method,
    memoize_configured_property,
)
from kwdagger.pipeline._shell import bash_heredoc_write_command
from kwdagger.pipeline._slurm import coerce_slurm_options

# Private names that the rest of the repository -- tests, audits under dev/,
# and doctests -- already import from here. They are not part of the public
# API and are not in ``__all__``, but moving them between submodules must not
# break a caller that has been importing them from ``kwdagger.pipeline`` all
# along. See tests/test_import_compat.py.
from kwdagger.pipeline._compile import (  # NOQA: F401
    _compile_pipeline_configurations,
    _hashable_group_value,
    _node_param_value,
)
from kwdagger.pipeline._connections import (  # NOQA: F401
    _UNSET,
    _alias_preds,
    _config_values_equal,
    _dependency_preds,
    _produced_origins,
)
from kwdagger.pipeline._logical import _resolve_pipeline  # NOQA: F401
from kwdagger.pipeline._process import _classvar_init  # NOQA: F401

__all__ = [
    'Collection',
    'CompiledPipeline',
    'Configurable',
    'GatherConnection',
    'GatherSpec',
    'GroupByKey',
    'IONode',
    'InputNode',
    'Node',
    'OutputNode',
    'ParamNode',
    'Pipeline',
    'ProcessNode',
    'StoredGroupByKey',
    'bash_heredoc_write_command',
    'coerce_pipeline',
    'coerce_slurm_options',
    'demo_pipeline_run',
    'demodata_pipeline',
    'memoize_configured_method',
    'memoize_configured_property',
]


def __getattr__(name: str) -> Any:
    """
    Keep the demo pipeline reachable from its historical home.

    ``demodata_pipeline`` and ``demo_pipeline_run`` live in
    :mod:`kwdagger.demo.demodata`, but ``kwdagger.pipeline.demodata_pipeline()``
    is a documented pipeline expression and is resolved by name, so it must
    still work. Resolving lazily also keeps the import direction one-way: the
    demo package imports this one, not the other way around.
    """
    if name in {'demodata_pipeline', 'demo_pipeline_run'}:
        from kwdagger.demo import demodata

        return getattr(demodata, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
