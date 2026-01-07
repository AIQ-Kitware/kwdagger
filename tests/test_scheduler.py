"""
Checks that the scheduler builds appropriate commands.
"""


def demodata_pipeline(dpath):
    import ubelt as ub
    script_fpath = dpath / 'script.py'
    pipeline_fpath = dpath / '_simple_demo_pipeline_v003.py'

    script_text = ub.codeblock(
        '''
        #!/usr/bin/env python3
        import scriptconfig as scfg
        import ubelt as ub
        import json


        class ScriptCLI(scfg.DataConfig):
            src = 'input.json'
            dst = 'output.json'
            param1 = None
            param2 = None
            param3 = None

            @classmethod
            def main(cls, argv=1, **kwargs):
                config = cls.cli(argv=argv, data=kwargs, strict=True, verbose='auto')
                src_fpath = ub.Path(config.src)
                dst_fpath = ub.Path(config.dst)
                src_text = src_fpath.read_text()
                src_data = json.loads(src_text)

                hidden = int(ub.hash_data([config.param1, config.param2, config.param3], base=10, hasher='sha1'))
                flags = [c == '1' for c in bin(hidden)[2:]]
                goodness = sum(flags) / len(flags)

                dst_data = {'size': len(src_text), 'goodness': goodness, 'nest': src_data}
                dst_fpath.parent.ensuredir()
                dst_fpath.write_text(json.dumps(dst_data))

        __cli__ = ScriptCLI

        if __name__ == '__main__':
            __cli__.main()
        ''')
    # Test the code compiles and write it to disk
    compile(script_text, mode='exec', filename='<test-compile>')
    script_fpath.write_text(script_text)

    pipeline_text = ub.codeblock(
        '''
        from kwdagger.pipeline import ProcessNode
        from kwdagger.pipeline import Pipeline

        class Step1(ProcessNode):
            name = 'step1'
            executable = 'python ''' + str(script_fpath) + ''''
            in_paths = {
                'src',
            }
            out_paths = {
                'dst': 'step1_output.json',
            }

            def load_result(self, node_dpath):
                import json
                from kwdagger.aggregate_loader import new_process_context_parser
                from kwdagger.utils import util_dotdict
                fpath = node_dpath / self.out_paths[self.primary_out_key]
                data = json.loads(fpath.read_text())
                nest_resolved = {}
                nest_resolved['metrics.size'] = data['size']
                nest_resolved['metrics.goodness'] = data['goodness']
                flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
                flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
                return flat_resolved

        def build_pipeline():
            nodes = {}
            nodes['step1'] = Step1()
            dag = Pipeline(nodes)
            dag.build_nx_graphs()
            return dag
        ''')

    # Test that the code compiles
    compile(pipeline_text, mode='exec', filename='<test-compile>')
    pipeline_fpath.write_text(pipeline_text)
    return pipeline_fpath


def test_simple_slurm_dry_run():
    """
    Ignore:
        python ~/code/kwdagger/tests/test_scheduler.py test_simple_but_real_custom_pipeline

    Ignore:
        import sys, ubelt
        sys.path.append(ubelt.expandpath('~/code/kwdagger/tests'))
        from test_scheduler import *  # NOQA
    """
    from kwdagger import schedule
    import ubelt as ub
    dpath = ub.Path.appdir('kwdagger/unit_tests/scheduler/test_slurm_dryrun').ensuredir()

    pipeline_fpath = demodata_pipeline(dpath)

    input_fpath = dpath / 'input.json'
    input_fpath.write_text('{"type": "orig_input"}')

    root_dpath = (dpath / 'runs').delete().ensuredir()
    config = schedule.ScheduleEvaluationConfig(**{
        'run': 0,
        'root_dpath': root_dpath,
        'backend': 'slurm',
        'params': ub.codeblock(
            f'''
            pipeline: {pipeline_fpath}::build_pipeline()
            matrix:
                step1.src:
                    - {input_fpath}
                step1.param1: |
                    - this: "is text 100% representing"
                      some: "yaml config"
                      omg: "single ' quote"
                      eek: 'double " quote'
                step1.param2:
                    - option1
                    - option2
                step1.param3:
                    - 4.5
                    - 9.2
                    - 3.14159
                    - 2.71828
            '''
        )
    })

    print('Dry run first')
    config['run'] = 0
    dag, queue = schedule.build_schedule(config)


def test_slurm_options_from_param_grid(tmp_path):
    from kwdagger import schedule
    import ubelt as ub
    dpath = ub.Path(tmp_path) / 'slurm_grid'
    dpath.delete().ensuredir()

    pipeline_fpath = demodata_pipeline(dpath)
    input_fpath = dpath / 'input.json'
    input_fpath.write_text('{"type": "orig_input"}')

    root_dpath = (dpath / 'runs').delete().ensuredir()
    param_yaml = ub.codeblock(
        f'''
        slurm_options:
            partition: general
            qos: debug
        pipeline: {pipeline_fpath}::build_pipeline()
        matrix:
            step1.src:
                - {input_fpath}
            step1.param1:
                - option1
            step1.param2:
                - option2
            step1.param3:
                - 0.1
            step1.__slurm_options__:
                - time: 00:01:00
        ''')
    config = schedule.ScheduleEvaluationConfig(**{
        'run': 0,
        'root_dpath': root_dpath,
        'backend': 'slurm',
        'params': param_yaml,
    })

    dag, queue = schedule.build_schedule(config)

    assert queue._sbatch_kvargs['partition'] == 'general'
    assert queue._sbatch_kvargs['qos'] == 'debug'

    step1 = dag.node_dict['step1']
    step1_job = queue.named_jobs[step1.process_id]
    assert step1_job._sbatch_kvargs['time'] == '00:01:00'

    text = queue.finalize_text()
    assert '--partition="general"' in text
    assert '--qos="debug"' in text
    assert '--time="00:01:00"' in text


def test_simple_but_real_custom_pipeline():
    """
    Ignore:
        python ~/code/kwdagger/tests/test_scheduler.py test_simple_but_real_custom_pipeline

    Ignore:
        import sys, ubelt
        sys.path.append(ubelt.expandpath('~/code/kwdagger/tests'))
        from test_scheduler import *  # NOQA
    """
    from kwdagger import schedule
    from kwdagger import aggregate
    import ubelt as ub
    dpath = ub.Path.appdir('kwdagger/unit_tests/scheduler/test_real_pipeline').ensuredir()

    pipeline_fpath = demodata_pipeline(dpath)

    input_fpath = dpath / 'input.json'
    input_fpath.write_text('{"type": "orig_input"}')

    root_dpath = (dpath / 'runs').delete().ensuredir()
    config = schedule.ScheduleEvaluationConfig(**{
        'run': 0,
        'root_dpath': root_dpath,
        'backend': 'serial',
        'params': ub.codeblock(
            f'''
            pipeline: {pipeline_fpath}::build_pipeline()
            matrix:
                step1.src:
                    - {input_fpath}
                step1.param1: |
                    - this: "is text 100% representing"
                      some: "yaml config"
                      omg: "single ' quote"
                      eek: 'double " quote'
                step1.param2:
                    - option1
                    - option2
                step1.param3:
                    - 4.5
                    - 9.2
                    - 3.14159
                    - 2.71828
            '''
        )
    })

    print('Dry run first')
    config['run'] = 0
    dag, queue = schedule.build_schedule(config)

    print('Real run second')
    config['run'] = 1
    dag, queue = schedule.build_schedule(config)

    # Test that all job config files are readable
    import json
    for job_config_fpath in dag.root_dpath.glob('flat/step1/*/job_config.json'):
        config = json.loads(job_config_fpath.read_text())

    # Can we test that this is well formatted?
    for invoke_fpath in dag.root_dpath.glob('flat/step1/*/invoke.sh'):
        command = invoke_fpath.read_text()
        command

    agg_config = aggregate.AggregateEvluationConfig(
        target=root_dpath,
        pipeline=f'{pipeline_fpath}::build_pipeline()',
        output_dpath=(root_dpath / 'aggregate'),
        io_workers=0,
        eval_nodes=['step1'],
    )
    eval_type_to_aggregator = aggregate.run_aggregate(agg_config)
    agg = eval_type_to_aggregator['step1']
    print(f'agg = {ub.urepr(agg, nl=1)}')


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/kwdagger/tests/test_scheduler.py
    """
    import xdoctest
    xdoctest.doctest_module(__file__)
