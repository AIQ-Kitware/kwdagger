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


# ---------------------------------------------------------------------------
# The package layering
# ---------------------------------------------------------------------------

#: The submodules of ``kwdagger.pipeline``, bottom first. A module may import
#: anything earlier in this list and nothing later. That one-way direction is
#: the whole point of the package split -- without it the boundaries are
#: decoration.
LAYERS = [
    '_shell',
    '_slurm',
    '_runtime',
    '_connections',
    '_process',
    '_compile',
    '_logical',
]


def _internal_imports(module_name, include_type_checking=False):
    """
    Sibling modules imported at runtime by ``kwdagger.pipeline.<module_name>``.

    Imports guarded by ``if TYPE_CHECKING:`` are excluded: they cost nothing at
    runtime and cannot create a cycle, which is exactly why an annotation-only
    reference upward is allowed.
    """
    import ast
    import pathlib

    import kwdagger.pipeline

    root = pathlib.Path(kwdagger.pipeline.__file__).parent
    tree = ast.parse((root / f'{module_name}.py').read_text())

    if not include_type_checking:
        for node in ast.walk(tree):
            body = getattr(node, 'body', None)
            if not isinstance(body, list):
                continue
            body[:] = [
                stmt
                for stmt in body
                if not (
                    isinstance(stmt, ast.If)
                    and isinstance(stmt.test, ast.Name)
                    and stmt.test.id == 'TYPE_CHECKING'
                )
            ]

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if not node.module.startswith('kwdagger.pipeline'):
                continue
            tail = node.module[len('kwdagger.pipeline'):].lstrip('.')
            if tail:
                found.add(tail)
            else:
                # ``from kwdagger.pipeline import _runtime``
                found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize('module_name', LAYERS)
def test_package_layering_is_one_directional(module_name):
    allowed = set(LAYERS[: LAYERS.index(module_name)])
    found = _internal_imports(module_name)
    violations = sorted(found - allowed)
    assert not violations, (
        f'kwdagger.pipeline.{module_name} imports {violations} at runtime, '
        f'which is at or above it in the layering'
    )


def test_upward_references_are_annotation_only():
    """
    The compiler names ``Pipeline`` in a signature. That is fine as long as it
    stays behind ``TYPE_CHECKING`` -- if it ever becomes a runtime import the
    package gains a cycle.
    """
    runtime = _internal_imports('_compile')
    annotated = _internal_imports('_compile', include_type_checking=True)
    assert '_logical' not in runtime
    assert '_logical' in annotated


def test_no_submodule_imports_the_package_facade():
    """
    A submodule doing ``from kwdagger.pipeline import <name>`` would route
    through ``__init__`` and cycle back into the package. Submodules import
    each other directly; only whole-module imports of a sibling are allowed.
    """
    for module_name in LAYERS:
        for name in _internal_imports(module_name, include_type_checking=True):
            assert name in LAYERS, (
                f'kwdagger.pipeline.{module_name} imports {name!r} through '
                f'the package facade'
            )


def test_demo_still_builds_through_the_public_entry_point():
    from kwdagger import Pipeline

    dag = Pipeline.demo()
    assert dag.node_dict
