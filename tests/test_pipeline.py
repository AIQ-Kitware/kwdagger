

def test_variable_inputs():
    """
    Test case where a node depends on a variable length set of inputs.
    """
    from kwdagger.pipeline import ProcessNode
    from kwdagger.pipeline import Pipeline

    # A simple pipeline where we don't need to manage reconfiguration.
    node1 = ProcessNode(name='node1', executable='node1', out_paths={'key1': 'path1'}, node_dpath='.')
    node2 = ProcessNode(name='node2', executable='node2', out_paths={'key2': 'path2'}, node_dpath='.')
    node3 = ProcessNode(name='node3', executable='node3', out_paths={'key3': 'path3'}, node_dpath='.')

    combine_node = ProcessNode(name='combine', executable='combine', in_paths={'varpaths'})

    node1.outputs['key1'].connect(combine_node.inputs['varpaths'])
    node2.outputs['key2'].connect(combine_node.inputs['varpaths'])
    node3.outputs['key3'].connect(combine_node.inputs['varpaths'])

    dag_nodes = [
        node1,
        node2,
        node3,
        combine_node
    ]
    dag = Pipeline(dag_nodes)
    dag.print_graphs()
    dag.configure()

    print(node1.final_command())
    print(node2.final_command())
    print(node3.final_command())
    print(combine_node.final_command())

    pred_nodes = combine_node.predecessor_process_nodes()
    assert list(pred_nodes) == [node1, node2, node3]


def test_slurm_options(tmp_path):
    from kwdagger.pipeline import ProcessNode
    from kwdagger.pipeline import Pipeline

    class TimedNode(ProcessNode):
        slurm_options = {
            'time': '00:20:00',
        }

    # A simple pipeline where we don't need to manage reconfiguration.
    node1 = TimedNode(name='node1', executable='node1.exe', out_paths={'key1': 'path1'}, node_dpath='.')
    node2 = ProcessNode(name='node2', executable='node2.exe', out_paths={'key2': 'path2'}, node_dpath='.')
    node3 = ProcessNode(name='node3', executable='node3.exe', out_paths={'key3': 'path3'}, node_dpath='.')

    combine_node = ProcessNode(name='combine', executable='combine', in_paths={'varpaths'})

    node1.outputs['key1'].connect(combine_node.inputs['varpaths'])
    node2.outputs['key2'].connect(combine_node.inputs['varpaths'])
    node3.outputs['key3'].connect(combine_node.inputs['varpaths'])

    dag_nodes = [
        node1,
        node2,
        node3,
        combine_node
    ]
    dag = Pipeline(dag_nodes)
    dag.print_graphs()

    dag.configure(config={
        '__slurm_options__': {
            'partition': 'debug',
        },
        'node2.__slurm_options__': {
            'gres': 'gpu:1',
        },
        'node3.__slurm_options__': {
            'time': '00:05:00',
            'partition': 'short',
        },
        'node2.some_key': 'some_value',
    }, root_dpath=tmp_path)

    import cmd_queue
    queue = cmd_queue.Queue.create(backend='slurm')
    dag.submit_jobs(
        queue=queue,
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )

    text = queue.finalize_text()
    assert '--partition="debug"' in text
    assert '--gres="gpu:1"' in text
    assert '--time="00:20:00"' in text
    assert '--time="00:05:00"' in text
    assert '--partition="short"' in text

    job1 = queue.named_jobs[node1.process_id]
    job2 = queue.named_jobs[node2.process_id]
    job3 = queue.named_jobs[node3.process_id]
    assert job1._sbatch_kvargs['partition'] == 'debug'
    assert job1._sbatch_kvargs['time'] == '00:20:00'
    assert job2._sbatch_kvargs['gres'] == 'gpu:1'
    assert job3._sbatch_kvargs['partition'] == 'short'
    assert job3._sbatch_kvargs['time'] == '00:05:00'
