"""
The established import surface of :mod:`kwdagger.pipeline`.

Implementation is allowed to move between internal modules; where callers
import it from is not. This file exists so a refactor that quietly relocates a
name fails here rather than in a downstream repository.
"""

from __future__ import annotations

import importlib

import pytest


def test_top_level_exports():
    from kwdagger import GatherSpec, Pipeline, ProcessNode  # NOQA

    assert Pipeline is not None
    assert ProcessNode is not None
    assert GatherSpec is not None


def test_public_pipeline_exports():
    from kwdagger.pipeline import (  # NOQA
        GatherSpec,
        Pipeline,
        ProcessNode,
        coerce_pipeline,
        coerce_slurm_options,
    )

    assert callable(coerce_pipeline)
    assert callable(coerce_slurm_options)


@pytest.mark.parametrize(
    'name',
    [
        # Used by tests and by generated shell for gather manifests.
        'bash_heredoc_write_command',
        # Used by tests to resolve a gather grouping key.
        '_node_param_value',
        # Referenced by name as a pipeline expression, e.g.
        # --pipeline=kwdagger.pipeline.demodata_pipeline()
        'demodata_pipeline',
        'demo_pipeline_run',
        # Asked for by the regression suite and by the compiler.
        '_hashable_group_value',
        '_produced_origins',
    ],
)
def test_repository_used_pipeline_attributes(name):
    module = importlib.import_module('kwdagger.pipeline')
    assert getattr(module, name) is not None


def test_pipeline_module_rejects_unknown_attributes():
    module = importlib.import_module('kwdagger.pipeline')
    with pytest.raises(AttributeError):
        module.no_such_attribute


def test_demo_still_builds_through_the_public_entry_point():
    from kwdagger import Pipeline

    dag = Pipeline.demo()
    assert dag.node_dict
