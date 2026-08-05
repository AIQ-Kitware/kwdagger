"""
There is one way to schedule a batch.

``build_schedule`` used to branch on ``dag.has_gather_connections`` and run
one of two execution architectures. A gather is a *compilation* feature, so
that made an unrelated property of the pipeline decide how every node in it
was scheduled -- which is how the two implementations drifted apart.

Now the matrix is compiled and the compiled graph is submitted, for every
pipeline, and an interactive one-row submission is that same compiler with one
row. These tests pin the consequences that are visible to a caller.
"""

from __future__ import annotations

import ubelt as ub

from kwdagger.pipeline import CompiledPipeline, Pipeline, ProcessNode


def _demo_pipeline_file(dpath):
    """A tiny gather-free pipeline defined in an importable module."""
    fpath = dpath / 'single_path_pipeline.py'
    fpath.write_text(
        ub.codeblock(
            """
            from kwdagger.pipeline import Pipeline, ProcessNode


            def build_pipeline():
                step1 = ProcessNode(
                    name='step1',
                    executable='python -c ""',
                    in_paths={'src'},
                    out_paths={'dst': 'step1_output.json'},
                    algo_params={'param1': None},
                )
                return Pipeline([step1])
            """
        )
    )
    return fpath


def _schedule(tmp_path, matrix):
    from kwdagger import schedule

    dpath = ub.Path(tmp_path)
    pipeline_fpath = _demo_pipeline_file(dpath)
    input_fpath = dpath / 'input.json'
    input_fpath.write_text('{}')
    root_dpath = (dpath / 'runs').ensuredir()
    params = ub.codeblock(
        f"""
        pipeline: {pipeline_fpath}::build_pipeline()
        matrix:
          step1.src:
            - {input_fpath}
          step1.param1: {matrix}
        """
    )
    config = schedule.ScheduleEvaluationConfig(
        run=0, root_dpath=root_dpath, backend='serial', params=params
    )
    return schedule.build_schedule(config)


def test_build_schedule_compiles_a_gather_free_matrix(tmp_path):
    """
    The branch is gone: a pipeline with no gather anywhere is compiled, and
    ``build_schedule`` returns the compiled pipeline rather than the template.
    """
    dag, queue = _schedule(tmp_path, '[a, b, c]')
    assert isinstance(dag, CompiledPipeline)
    assert dag.compile_summary['concrete_nodes'] == 3
    assert len(dag.nodes_by_name['step1']) == 3
    for node in dag.nodes.values():
        assert node.process_id in queue.named_jobs


def test_an_empty_matrix_compiles_to_nothing(tmp_path):
    """
    An empty grid used to skip the configure loop entirely and hand back an
    unconfigured template. It now compiles to an empty graph, which is the
    same statement without the special case.
    """
    dag, queue = _schedule(tmp_path, '[]')
    assert isinstance(dag, CompiledPipeline)
    assert dag.nodes == {}
    assert len(queue) == 0


def _linear_pipeline():
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


def test_interactive_submission_goes_through_the_compiler(tmp_path):
    """
    ``Pipeline.submit_jobs`` is a one-row compile, not a second scheduler.
    """
    dag = _linear_pipeline()
    dag.configure(
        {'producer.src_fpath': '/data/a'}, root_dpath=tmp_path, cache=False
    )
    compiled = dag.compile_current_configuration()
    assert isinstance(compiled, CompiledPipeline)
    assert sorted(compiled.nodes_by_name) == ['consumer', 'producer']
    # The compiled instances are the same processes the template describes.
    assert {n.process_id for n in compiled.nodes.values()} == {
        n.process_id for n in dag.node_dict.values()
    }


def test_the_compiled_row_is_the_row_as_given(tmp_path):
    """
    Reserved keys are remembered, not reconstructed from node state. A
    row-global ``__slurm_options__`` is popped off ``Pipeline.config``, so
    recovering the row from what the nodes ended up with would lose it.
    """
    dag = _linear_pipeline()
    row = {
        'producer.src_fpath': '/data/a',
        '__slurm_options__': {'partition': 'row'},
        'consumer.__slurm_options__': {'gres': 'gpu:1'},
    }
    dag.configure(row, root_dpath=tmp_path, cache=False)
    assert '__slurm_options__' not in dag.config
    assert dag._configured_row['__slurm_options__'] == {'partition': 'row'}

    summary = dag.submit_jobs(
        queue={'backend': 'slurm', 'name': 'one-row'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    queue = summary['queue']
    compiled = dag.compile_current_configuration()
    (consumer,) = compiled.nodes_by_name['consumer']
    job = queue.named_jobs[consumer.process_id]
    # Row-global under the node's own, exactly as the batch path layers them.
    assert job.unused_kwargs['partition'] == 'row'
    assert job.unused_kwargs['gres'] == 'gpu:1'


def test_node_status_is_keyed_by_process_id(tmp_path):
    """
    A name cannot key a compiled matrix -- it holds many instances of one --
    so the summary uses the key that identifies a job, the same one
    ``queue.named_jobs`` uses.
    """
    dag = _linear_pipeline()
    dag.configure(
        {'producer.src_fpath': '/data/a'}, root_dpath=tmp_path, cache=False
    )
    summary = dag.submit_jobs(
        queue={'backend': 'serial', 'name': 'keys'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    status = summary['node_status']
    assert set(status) == {n.process_id for n in dag.node_dict.values()}
    assert set(status.values()) == {'new_submission'}


def test_a_batch_reports_every_instance_rather_than_the_last_row(tmp_path):
    """
    The old loop returned one summary per row, keyed by node name, so a
    matrix-wide view had to be reassembled by the caller. One compile means
    one summary covering every concrete process.
    """
    rows = [
        {'producer.src_fpath': '/data/a'},
        {'producer.src_fpath': '/data/b'},
    ]
    compiled = _linear_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'batch'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    assert set(summary['node_status']) == set(compiled.nodes)
    assert len(summary['node_status']) == 4


def test_submitted_nodes_still_describe_their_row_afterwards(tmp_path):
    """
    The interactive wart this removes. The row-at-a-time path mutated one node
    per name, so after a second row the first row's submitted state was gone.
    A compiled row keeps its own instances.
    """
    dag = _linear_pipeline()
    compiled_rows = []
    for src in ['/data/a', '/data/b']:
        dag.configure(
            {'producer.src_fpath': src}, root_dpath=tmp_path, cache=False
        )
        compiled_rows.append(dag.compile_current_configuration())

    first, second = compiled_rows
    assert str(
        first.nodes_by_name['producer'][0].final_in_paths['src_fpath']
    ) == ('/data/a')
    assert (
        str(second.nodes_by_name['producer'][0].final_in_paths['src_fpath'])
        == '/data/b'
    )
    # The template, by contrast, only ever describes the row it was last given.
    assert str(dag.node_dict['producer'].final_in_paths['src_fpath']) == (
        '/data/b'
    )
