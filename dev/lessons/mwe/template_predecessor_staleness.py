"""
A ProcessNode memoizes lineage queries during its own construction, before it
has been connected to anything. Building a Pipeline clears those caches, which
is what makes template lineage answer correctly.

Run::

    python dev/lessons/mwe/template_predecessor_staleness.py
"""

from kwdagger.pipeline import Pipeline, ProcessNode


def main():
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(consumer.inputs['data_fpath'])

    # The port edge exists ...
    assert consumer.inputs['data_fpath'].pred == [
        producer.outputs['produced_fpath']
    ]

    # ... but the query was memoized during __init__, before the connection,
    # and nothing has invalidated it yet.
    stale = consumer.predecessor_process_nodes()
    assert stale == [], f'expected a stale empty list, got {stale}'

    # Building the pipeline is the first moment the complete connection state
    # is known, so that is where the caches are dropped.
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    assert dag.proc_graph.has_edge('producer', 'consumer')
    assert consumer.predecessor_process_nodes() == [producer]
    assert consumer.ancestor_process_nodes() == [producer]

    print(
        'confirmed: lineage memoized at construction is stale; '
        'Pipeline.build_nx_graphs clears it'
    )


if __name__ == '__main__':
    main()
