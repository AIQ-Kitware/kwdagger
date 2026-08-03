"""
A template ProcessNode answers ``predecessor_process_nodes`` from a cache
populated during its own construction, before any connection exists.

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

    # ... but the memoized query does not know about it.
    stale = consumer.predecessor_process_nodes()
    assert stale == [], f'expected a stale empty list, got {stale}'

    # Clearing the cache (which is what configure does) gives the right answer.
    consumer._configured_cache.clear()
    fresh = consumer.predecessor_process_nodes()
    assert fresh == [producer], fresh

    # build_nx_graphs still finds the edge, because it also walks the
    # successor direction, whose cache is not populated during construction.
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    assert dag.proc_graph.has_edge('producer', 'consumer')

    print(
        'confirmed: template predecessor query is stale; successor pass '
        'is what makes build_nx_graphs correct'
    )


if __name__ == '__main__':
    main()
