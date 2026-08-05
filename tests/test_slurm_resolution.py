"""
One resolver for the Slurm request, and one place it is stored.

The effective options a job is submitted with used to be computed three times
from three different subsets of the four layers: the compiler folded a
row-global mapping into node-local configuration, the runtime layered
pipeline-wide options over whatever the node had resolved, and arbitration
re-layered those two halves a third time in order to compare them.

Now ``resolve_slurm_options`` is the only thing that knows the precedence.
Whoever holds all four layers calls it -- compilation for each clone, and
``Pipeline.configure`` so a configured template node reports the same request
that will be submitted -- and ``node.effective_slurm_options`` is the only
thing anything downstream reads.
"""

from __future__ import annotations

import pytest

from kwdagger.pipeline import Pipeline, ProcessNode, resolve_slurm_options


def test_the_resolver_states_the_documented_precedence():
    resolved = resolve_slurm_options(
        pipeline_base={'account': 'acct', 'partition': 'base', 'time': '1:00'},
        row_global={'partition': 'row', 'time': '2:00'},
        node_default={'time': '3:00', 'gres': 'gpu:1'},
        node_override={'gres': 'gpu:4'},
    )
    assert resolved == {
        'account': 'acct',  # only the base declares it
        'partition': 'row',  # row-global outranks the base
        'time': '3:00',  # a node default outranks a row-global one
        'gres': 'gpu:4',  # and the row's node override outranks all
    }


def test_the_resolver_is_pure_and_tolerates_missing_layers():
    assert resolve_slurm_options() == {}
    assert resolve_slurm_options(node_default={'gres': 'gpu:1'}) == {
        'gres': 'gpu:1'
    }
    # A YAML string is a documented spelling of a layer.
    assert resolve_slurm_options(row_global='partition: row') == {
        'partition': 'row'
    }


def _pipeline(node_default=None):
    return Pipeline(
        [
            ProcessNode(
                name='predict',
                executable='python predict.py',
                out_paths={'out_fpath': 'out.json'},
                algo_params={'model': 'm'},
                slurm_options=node_default,
            )
        ]
    )


def _compiled_node(dag, row, tmp_path):
    compiled = dag.compile_configurations(
        [row], root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    return node


def test_every_layer_reaches_the_effective_request(tmp_path):
    dag = _pipeline(node_default={'time': '3:00', 'gres': 'gpu:1'})
    dag._base_slurm_options = {'account': 'acct', 'partition': 'base'}
    node = _compiled_node(
        dag,
        {
            'predict.model': 'm',
            '__slurm_options__': {'partition': 'row'},
            'predict.__slurm_options__': {'gres': 'gpu:4'},
        },
        tmp_path,
    )
    assert node.effective_slurm_options == {
        'account': 'acct',
        'partition': 'row',
        'time': '3:00',
        'gres': 'gpu:4',
    }


def test_the_node_attribute_stays_node_level(tmp_path):
    """
    ``slurm_options`` is the node's own two layers and nothing else. It used
    to absorb the row-global mapping during compilation, which is what made it
    mean different things on the two scheduling paths.
    """
    dag = _pipeline(node_default={'gres': 'gpu:1'})
    dag._base_slurm_options = {'account': 'acct'}
    node = _compiled_node(
        dag,
        {'predict.model': 'm', '__slurm_options__': {'partition': 'row'}},
        tmp_path,
    )
    assert node.slurm_options == {'gres': 'gpu:1'}
    assert node.effective_slurm_options == {
        'account': 'acct',
        'partition': 'row',
        'gres': 'gpu:1',
    }


def test_the_submitted_job_carries_exactly_the_resolved_request(tmp_path):
    """The runtime reads the resolution rather than recomputing part of it."""
    dag = _pipeline(node_default={'time': '3:00'})
    dag._base_slurm_options = {'account': 'acct'}
    row = {
        'predict.model': 'm',
        '__slurm_options__': {'partition': 'row'},
        'predict.__slurm_options__': {'gres': 'gpu:4'},
    }
    compiled = dag.compile_configurations(
        [row], root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    summary = compiled.submit_jobs(
        queue={'backend': 'slurm', 'name': 'resolved'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    job = summary['queue'].named_jobs[node.process_id]
    for key, value in node.effective_slurm_options.items():
        assert job.unused_kwargs[key] == value


def test_a_row_that_omits_options_resets_to_the_base(tmp_path):
    """
    The stale-row-state defect, in the layer that carries it. A row without
    ``__slurm_options__`` asks for the pipeline default, not for whatever the
    previous row happened to ask for.
    """
    dag = _pipeline()
    dag._base_slurm_options = {'partition': 'base'}
    compiled = dag.compile_configurations(
        [
            {'predict.model': 'a', '__slurm_options__': {'partition': 'row'}},
            {'predict.model': 'b'},
        ],
        root_dpath=tmp_path,
        cache=False,
    )
    by_model = {
        n.final_algo_config['model']: n
        for n in compiled.nodes_by_name['predict']
    }
    assert by_model['a'].effective_slurm_options == {'partition': 'row'}
    assert by_model['b'].effective_slurm_options == {'partition': 'base'}


def test_rows_that_differ_about_resources_take_the_first(tmp_path):
    """
    Slurm options are outside ``process_id``, so two rows asking for
    different resources are one job and the first supplies the request.
    Under ``duplicate_policy='error'`` the resolved values are what get
    compared, which is the only comparison that sees every layer.
    """
    dag = _pipeline()
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:1'}},
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert node.effective_slurm_options['gres'] == 'gpu:1'

    with pytest.raises(ValueError, match='__slurm_options__'):
        _pipeline().compile_configurations(
            rows, root_dpath=tmp_path, cache=False, duplicate_policy='error'
        )


def test_the_same_request_at_different_levels_is_agreement(tmp_path):
    """
    The complement: asking for the same thing row-globally and node-locally
    resolves to one request, so the rows deduplicate rather than conflict.
    """
    dag = _pipeline()
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
        {'predict.model': 'm', 'predict.__slurm_options__': {'gres': 'gpu:2'}},
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    assert len(compiled.nodes_by_name['predict']) == 1


def test_a_node_configured_on_its_own_still_reports_a_request():
    """
    Nothing compiled this node, so only its own layers exist -- but the
    attribute is populated rather than absent, so a reader never sees a
    request with layers silently missing.
    """
    node = ProcessNode(
        name='predict',
        executable='python predict.py',
        out_paths={'out_fpath': 'out.json'},
        slurm_options={'gres': 'gpu:1'},
    )
    node.configure({'__slurm_options__': {'time': '1:00'}})
    assert node.effective_slurm_options == {'gres': 'gpu:1', 'time': '1:00'}
    assert node.effective_slurm_options == node.slurm_options


# ---------------------------------------------------------------------------
# The other half of the phase: one normalization boundary
# ---------------------------------------------------------------------------


def test_the_matrix_is_normalized_before_it_is_expanded(tmp_path):
    """
    The grid used to be built from raw values, so a ``Path`` and the equal
    string counted as two points on an axis -- and then compiled to one
    process, leaving the reported cardinality contradicted by the graph.
    Crossing the boundary first makes the two agree.
    """
    import ubelt as ub

    from kwdagger import schedule

    dpath = ub.Path(tmp_path)
    pipeline_fpath = dpath / 'norm_pipeline.py'
    pipeline_fpath.write_text(
        ub.codeblock(
            """
            from kwdagger.pipeline import Pipeline, ProcessNode


            def build_pipeline():
                return Pipeline([
                    ProcessNode(
                        name='step1',
                        executable='python -c ""',
                        in_paths={'src'},
                        out_paths={'dst': 'out.json'},
                    )
                ])
            """
        )
    )
    src = dpath / 'input.json'
    src.write_text('{}')
    root_dpath = (dpath / 'runs').ensuredir()
    config = schedule.ScheduleEvaluationConfig(
        run=0,
        root_dpath=root_dpath,
        backend='serial',
        pipeline=f'{pipeline_fpath}::build_pipeline()',
        # The same path twice, spelled two ways. A ``Path`` cannot come from
        # YAML, so this is the programmatic entrance.
        params={'matrix': {'step1.src': [src, str(src)]}},
    )
    dag, _queue = schedule.build_schedule(config)
    assert len(dag.nodes_by_name['step1']) == 1


def test_a_configured_template_node_reports_every_layer(tmp_path):
    """
    Inspecting a configured pipeline must show the request that will be
    submitted. ``ProcessNode.configure`` resolves only the two layers a node
    knows, so a template node used to report an incomplete request while the
    submitted job used the complete one.
    """
    dag = _pipeline(node_default={'time': '3:00'})
    dag._base_slurm_options = {'account': 'acct'}
    row = {
        'predict.model': 'm',
        '__slurm_options__': {'partition': 'row'},
        'predict.__slurm_options__': {'gres': 'gpu:4'},
    }
    dag.configure(row, root_dpath=tmp_path, cache=False)
    template = dag.node_dict['predict']
    (compiled,) = dag.compile_current_configuration().nodes_by_name['predict']
    assert template.effective_slurm_options == {
        'account': 'acct',
        'partition': 'row',
        'time': '3:00',
        'gres': 'gpu:4',
    }
    assert template.effective_slurm_options == compiled.effective_slurm_options


def test_a_row_that_drops_the_pipeline_layers_updates_the_template(tmp_path):
    """The template follows the current row, not an accumulated one."""
    dag = _pipeline()
    dag._base_slurm_options = {'partition': 'base'}
    dag.configure(
        {'predict.model': 'a', '__slurm_options__': {'gres': 'gpu:1'}},
        root_dpath=tmp_path,
        cache=False,
    )
    assert dag.node_dict['predict'].effective_slurm_options == {
        'partition': 'base',
        'gres': 'gpu:1',
    }
    dag.configure({'predict.model': 'b'}, root_dpath=tmp_path, cache=False)
    assert dag.node_dict['predict'].effective_slurm_options == {
        'partition': 'base'
    }
