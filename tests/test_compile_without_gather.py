"""
Compilation is the ordinary case, not the gather case.

``compile_configurations`` used to refuse a pipeline without a gather, which
is what forced the second, row-at-a-time scheduling path into existence. These
tests pin that a gather-free matrix compiles by the *same* algorithm: nodes are
cloned per row rather than mutated in place, identical requests canonicalize,
dependency-only edges and aliased inputs survive cloning, the effective
execution graph is built from what each command actually reads, and the
cardinality diagnostics work with no collections to report.

Parity with the historical row-at-a-time path is asserted separately, in
``test_scheduler_parity.py``.
"""

from __future__ import annotations

import pytest

from kwdagger.pipeline import Pipeline, ProcessNode


def _linear_pipeline():
    """A producer feeding a consumer. No gather anywhere."""
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        in_paths={'src_fpath'},
        out_paths={'data_fpath': 'data.json'},
        algo_params={'algo': 'x'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'thresh': 0.5},
    )
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([producer, consumer])


def _nodes_named(compiled, name):
    return [node for node in compiled.nodes.values() if node.name == name]


def test_a_gather_free_pipeline_compiles(tmp_path):
    """The precondition that created the second path is gone."""
    compiled = _linear_pipeline().compile_configurations(
        [{'producer.src_fpath': '/data/a'}], root_dpath=tmp_path, cache=False
    )
    assert compiled.compile_summary['concrete_nodes'] == 2
    assert compiled.compile_summary['collection_groups'] == 0


def test_every_row_becomes_its_own_instance(tmp_path):
    """
    Cloning, not mutation.

    The row-at-a-time path reused one node object per name, so inspecting a
    pipeline after scheduling showed only the final row. A compiled pipeline
    holds every row at once, which is the interactive wart this incidentally
    fixes.
    """
    rows = [
        {'producer.src_fpath': '/data/a'},
        {'producer.src_fpath': '/data/b'},
        {'producer.src_fpath': '/data/c'},
    ]
    compiled = _linear_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    producers = _nodes_named(compiled, 'producer')
    assert len(producers) == 3
    assert {str(n.final_in_paths['src_fpath']) for n in producers} == {
        '/data/a',
        '/data/b',
        '/data/c',
    }
    # Distinct objects, not one object observed three times.
    assert len({id(n) for n in producers}) == 3


def test_identical_rows_canonicalize_to_one_process(tmp_path):
    row = {'producer.src_fpath': '/data/a'}
    compiled = _linear_pipeline().compile_configurations(
        [dict(row), dict(row), dict(row)],
        root_dpath=tmp_path,
        cache=False,
    )
    assert len(_nodes_named(compiled, 'producer')) == 1
    assert len(_nodes_named(compiled, 'consumer')) == 1


def test_rows_that_share_an_identity_but_disagree_on_execution_are_refused(
    tmp_path,
):
    """
    Arbitration is not a gather feature either.

    ``__enabled__`` is deliberately outside ``process_id``, so two rows can
    compile to one process and still disagree about whether it runs. Without a
    check, whichever row came first would silently win.
    """
    rows = [
        {'producer.src_fpath': '/data/a', 'producer.__enabled__': True},
        {'producer.src_fpath': '/data/a', 'producer.__enabled__': False},
    ]
    with pytest.raises(ValueError, match='__enabled__'):
        _linear_pipeline().compile_configurations(
            rows, root_dpath=tmp_path, cache=False
        )


def test_dependency_only_edges_survive_cloning(tmp_path):
    """
    An ordering edge carries no value, so nothing about it is gather-shaped.
    It still has to reach the compiled graph, or the consumer would be
    submitted without waiting.
    """
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        in_paths={'data_fpath'},
        out_paths={'prepared_fpath': 'prepared.json'},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        out_paths={'summary_fpath': 'summary.json'},
    )
    summarize._pred_nodes_without_io_connection.append(prepare)
    dag = Pipeline([summarize, prepare])

    rows = [
        {'prepare.data_fpath': '/data/a'},
        {'prepare.data_fpath': '/data/b'},
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    summaries = _nodes_named(compiled, 'summarize')
    # The ordering edge reaches identity through ``__dependency__``, so the
    # two rows are two distinct processes rather than one.
    assert len(summaries) == 2
    for node in summaries:
        preds = node.effective_predecessor_process_nodes()
        assert [p.name for p in preds] == ['prepare']
        assert node.depends['__dependency__.prepare'] == preds[0].process_id


def test_aliased_inputs_survive_cloning(tmp_path):
    """
    Input-to-input forwarding shares a known value. It must be replicated onto
    each clone -- otherwise the consumer loses its value -- and it must *not*
    become an execution dependency.
    """
    source = ProcessNode(
        name='source',
        executable='python source.py',
        in_paths={'data_fpath'},
        out_paths={'out_fpath': 'out.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    source.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline([source, consumer])

    rows = [{'source.data_fpath': '/data/a'}, {'source.data_fpath': '/data/b'}]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    consumers = _nodes_named(compiled, 'consumer')
    assert {str(n.final_in_paths['data_fpath']) for n in consumers} == {
        '/data/a',
        '/data/b',
    }
    for node in consumers:
        assert node.effective_predecessor_process_nodes() == []


def test_the_compiled_graph_is_the_effective_one(tmp_path):
    """
    An overridden input outranks its wired producer, so the producer supplies
    nothing and must not gate the consumer. That is the *effective* graph, and
    compilation has to build it whether or not a gather is present.
    """
    dag = _linear_pipeline()
    dag.configure(
        {'producer.src_fpath': '/data/a'}, root_dpath=tmp_path, cache=False
    )
    compiled = _linear_pipeline().compile_configurations(
        [
            {
                'producer.src_fpath': '/data/a',
                'consumer.data_fpath': '/precomputed/data.json',
            }
        ],
        root_dpath=tmp_path,
        cache=False,
    )
    consumer = _nodes_named(compiled, 'consumer')[0]
    assert consumer.effective_predecessor_process_nodes() == []
    # The structural template still says the edge is possible.
    assert list(dag.proc_graph.edges()) == [('producer', 'consumer')]
    assert list(compiled.proc_graph.edges()) == []


def test_cardinality_diagnostics_without_a_collection(tmp_path, capsys):
    rows = [
        {'producer.src_fpath': '/data/a'},
        {'producer.src_fpath': '/data/b'},
    ]
    compiled = _linear_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    records = compiled._edge_cardinality_records()
    assert [r['relation'] for r in records] == ['direct 1:1']
    assert records[0]['kind'] == 'ordinary'
    compiled.print_cardinality_graph()
    out = capsys.readouterr().out
    assert 'producer [2 instances]' in out
    assert 'consumer [2 instances]' in out


def test_a_compiled_gather_free_pipeline_submits(tmp_path):
    rows = [
        {'producer.src_fpath': '/data/a'},
        {'producer.src_fpath': '/data/b'},
    ]
    compiled = _linear_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'gather-free'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    assert sorted(summary['node_status'].values()) == ['new_submission'] * 4
    queue = summary['queue']
    for node in compiled.nodes.values():
        assert node.process_id in queue.named_jobs
