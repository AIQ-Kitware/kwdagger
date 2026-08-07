"""
The compiled graph decides what must run first.

Submission answers every dependency question by walking the graph it was
handed -- queue ordering, whether an ancestor will exist, which ``.pred`` and
``.succ`` links to write, and what a duplicate request's prerequisites were.
It does not ask a node about its own ancestry, because that would be a second
derivation of the edges it is already walking, and two derivations of one
answer is the shape this refactor exists to remove.

The container follows the same rule: everything a ``CompiledPipeline``
reports about its nodes is derived from ``proc_graph``, so there is no second
collection to keep in step with it.
"""

from __future__ import annotations

import networkx as nx

from kwdagger.pipeline import Pipeline, ProcessNode
from kwdagger.pipeline._runtime import submit_jobs


def _chain_pipeline():
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        in_paths={'src_fpath'},
        out_paths={'data_fpath': 'data.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([producer, consumer])


def _compile(tmp_path, rows=None):
    rows = rows if rows is not None else [{'producer.src_fpath': '/data/a'}]
    return _chain_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )


def _submit(compiled, name, **kwargs):
    options = {
        'enable_links': False,
        'write_invocations': False,
        'write_configs': False,
    }
    options.update(kwargs)
    return compiled.submit_jobs(
        queue={'backend': 'serial', 'name': name}, **options
    )


def test_queue_dependencies_come_from_the_graph_edges(tmp_path):
    compiled = _compile(tmp_path)
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    queue = _submit(compiled, 'edges')['queue']
    job = queue.named_jobs[consumer.process_id]
    assert producer.process_id in [
        getattr(d, 'name', d) for d in (job.depends or [])
    ]


def test_an_edge_removed_from_the_graph_is_not_waited_on(tmp_path):
    """
    The load-bearing statement. Node state still says the consumer is wired to
    the producer; the graph says otherwise, and the graph is what decides.

    Nothing produces this situation in normal use -- the compiler builds the
    graph from that very node state -- which is exactly why it has to be
    provoked to be observed.
    """
    compiled = _compile(tmp_path)
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    # The node still believes it reads the producer's output.
    assert [n.name for n in consumer.effective_predecessor_process_nodes()] == [
        'producer'
    ]

    graph = compiled.proc_graph.copy()
    graph.remove_edge(producer.process_id, consumer.process_id)
    summary = submit_jobs(
        graph,
        queue={'backend': 'serial', 'name': 'severed'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    job = summary['queue'].named_jobs[consumer.process_id]
    assert [getattr(d, 'name', d) for d in (job.depends or [])] == []


def test_a_disabled_producer_suppresses_its_successor_through_the_graph(
    tmp_path,
):
    """
    Gating reads the same edges. A predecessor that will not exist stops its
    successor, and the graph is where "predecessor" is defined.
    """
    compiled = _compile(
        tmp_path,
        rows=[
            {
                'producer.src_fpath': '/data/a',
                'producer.__enabled__': False,
            }
        ],
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    status = _submit(compiled, 'gated')['node_status']
    assert status[producer.process_id] == 'disabled'
    assert status[consumer.process_id] == 'skipped'


def test_the_links_written_follow_the_graph(tmp_path):
    """
    ``.pred`` and ``.succ`` make the result directory navigable, so they have
    to describe the same lineage the queue enforced.
    """
    compiled = _compile(tmp_path)
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    queue = _submit(compiled, 'links', enable_links=True)['queue']
    text = queue.finalize_text()
    assert f'.pred/producer/{producer.process_id}' in text
    assert f'.succ/consumer/{consumer.process_id}' in text


def test_the_compiled_containers_are_derived_from_the_graph(tmp_path):
    """
    ``nodes`` and ``nodes_by_name`` are views, not copies taken at
    construction -- the shape that goes stale the first time anything mutates.
    """
    compiled = _compile(
        tmp_path,
        rows=[
            {'producer.src_fpath': '/data/a'},
            {'producer.src_fpath': '/data/b'},
        ],
    )
    assert set(compiled.nodes) == set(compiled.proc_graph.nodes)
    assert sorted(compiled.nodes_by_name) == ['consumer', 'producer']
    assert len(compiled.nodes_by_name['producer']) == 2
    # Every instance reported is the one carried by the graph, not a copy.
    for process_id, node in compiled.nodes.items():
        assert compiled.proc_graph.nodes[process_id]['node'] is node

    # Derived rather than stored: a container built over a graph reports that
    # graph, with nothing to populate separately and nothing to fall out of
    # step. A stored snapshot would have been taken before this edit.
    graph = compiled.proc_graph.copy()
    dropped = next(iter(compiled.nodes_by_name['producer'])).process_id
    graph.remove_node(dropped)
    reduced = type(compiled)(proc_graph=graph, root_dpath=compiled.root_dpath)
    assert dropped not in reduced.nodes
    assert len(reduced.nodes_by_name['producer']) == 1


def test_the_compiled_graph_is_a_dag_over_process_ids(tmp_path):
    compiled = _compile(
        tmp_path,
        rows=[
            {'producer.src_fpath': '/data/a'},
            {'producer.src_fpath': '/data/b'},
        ],
    )
    assert nx.is_directed_acyclic_graph(compiled.proc_graph)
    assert set(compiled.proc_graph.nodes) == {
        node.process_id for node in compiled.nodes.values()
    }
    for source, target in compiled.proc_graph.edges:
        assert compiled.nodes[source].name == 'producer'
        assert compiled.nodes[target].name == 'consumer'
