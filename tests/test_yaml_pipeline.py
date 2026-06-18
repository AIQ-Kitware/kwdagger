"""
Tests for the declarative (pure-YAML) pipeline specification.

These cover three things:

* structural correctness of the loader (nodes, edges, forwarding, validation),
* equivalence with the Python API (identical hashes and commands), and
* an end-to-end schedule + aggregate run exercising the generic ``load_result``
  and the YAML ``metrics`` metadata for an evaluation node.
"""

import ubelt as ub


def test_yaml_pipeline_matches_python_pipeline():
    """
    The declarative loader must produce a pipeline that is indistinguishable
    from one built via the Python API: same node IDs and same final commands.
    """
    from kwdagger.pipeline import Pipeline, ProcessNode
    from kwdagger.yaml_pipeline import load_yaml_pipeline

    class Step1(ProcessNode):
        name = 'step1'
        executable = 'python step1.py'
        in_paths = {'src'}
        out_paths = {'dst': 'out1.json'}
        algo_params = {'alpha': 0.1}
        perf_params = {'workers': 0}

    class Step2(ProcessNode):
        name = 'step2'
        executable = 'python step2.py'
        in_paths = {'src'}
        out_paths = {'dst': 'out2.json'}

    def py_pipeline():
        nodes = {'step1': Step1(), 'step2': Step2()}
        nodes['step1'].outputs['dst'].connect(nodes['step2'].inputs['src'])
        dag = Pipeline(nodes)
        dag.build_nx_graphs()
        return dag

    yaml_dag = load_yaml_pipeline(
        {
            'nodes': {
                'step1': {
                    'executable': 'python step1.py',
                    'in_paths': ['src'],
                    'out_paths': {'dst': 'out1.json'},
                    'algo_params': {'alpha': 0.1},
                    'perf_params': {'workers': 0},
                },
                'step2': {
                    'executable': 'python step2.py',
                    'in_paths': ['src'],
                    'out_paths': {'dst': 'out2.json'},
                },
            },
            'edges': ['step1.dst -> step2.src'],
        }
    )

    root = ub.Path.appdir('kwdagger/unit_tests/yaml_pipeline/equiv').ensuredir()
    cfg = {'step1.src': '/data/in.json', 'step1.alpha': 0.7, 'step1.workers': 4}

    def fingerprint(dag):
        dag.configure(config=cfg, root_dpath=root, cache=False)
        return [
            (
                name,
                dag.node_dict[name].algo_id,
                dag.node_dict[name].process_id,
                dag.node_dict[name].final_command(),
            )
            for name in ['step1', 'step2']
        ]

    assert fingerprint(py_pipeline()) == fingerprint(yaml_dag)


def test_load_from_string_and_file(tmp_path):
    """A dict, an inline YAML string, and a YAML file are all equivalent."""
    from kwdagger.pipeline import coerce_pipeline
    from kwdagger.yaml_pipeline import load_yaml_pipeline

    spec = {
        'nodes': {
            'a': {'executable': 'echo a', 'out_paths': {'dst': 'a.txt'}},
            'b': {
                'executable': 'echo b',
                'in_paths': ['src'],
                'out_paths': {'dst': 'b.txt'},
            },
        },
        'edges': ['a.dst -> b.src'],
    }

    text = ub.codeblock(
        """
        nodes:
          a:
            executable: "echo a"
            out_paths: {dst: a.txt}
          b:
            executable: "echo b"
            in_paths: [src]
            out_paths: {dst: b.txt}
        edges:
          - a.dst -> b.src
        """
    )

    fpath = ub.Path(tmp_path) / 'pipeline.yaml'
    fpath.write_text(text)

    from_dict = load_yaml_pipeline(spec)
    from_text = load_yaml_pipeline(text)
    from_file = load_yaml_pipeline(fpath)
    # coerce_pipeline should auto-detect the .yaml file by suffix.
    from_coerce = coerce_pipeline(str(fpath))

    for dag in [from_dict, from_text, from_file, from_coerce]:
        assert sorted(dag.node_dict) == ['a', 'b']
        assert dag.node_dict['b'].inputs['src'].pred[0].key == 'a.dst'


def test_input_forwarding():
    """An input->input edge forwards shared data to a second node."""
    from kwdagger.yaml_pipeline import load_yaml_pipeline

    dag = load_yaml_pipeline(
        {
            'nodes': {
                'predict': {
                    'executable': 'echo predict',
                    'in_paths': ['src_fpath'],
                    'out_paths': {'dst_fpath': 'pred.json'},
                },
                'evaluate': {
                    'executable': 'echo evaluate',
                    'in_paths': ['true_fpath', 'pred_fpath'],
                    'out_paths': {'out_fpath': 'metrics.json'},
                },
            },
            'edges': [
                'predict.dst_fpath -> evaluate.pred_fpath',
                'predict.src_fpath -> evaluate.true_fpath',
            ],
        }
    )
    evaluate = dag.node_dict['evaluate']
    assert evaluate.inputs['pred_fpath'].pred[0].key == 'predict.dst_fpath'
    # The true_fpath input is forwarded from predict's *input* (not output).
    assert evaluate.inputs['true_fpath'].pred[0].key == 'predict.src_fpath'


def test_validation_errors():
    """The loader should reject malformed specs with clear errors."""
    import pytest

    from kwdagger.yaml_pipeline import load_yaml_pipeline

    base = {
        'nodes': {'a': {'executable': 'echo a', 'out_paths': {'dst': 'a.txt'}}}
    }

    # Missing executable / command.
    with pytest.raises(ValueError, match='executable'):
        load_yaml_pipeline({'nodes': {'a': {'out_paths': {'dst': 'a.txt'}}}})

    # Unknown node spec key.
    with pytest.raises(ValueError, match='unknown node spec key'):
        load_yaml_pipeline(
            {'nodes': {'a': {'executable': 'echo a', 'bogus': 1}}}
        )

    # "name" inside a node spec is disallowed (the key is the name).
    with pytest.raises(ValueError, match='do not set'):
        load_yaml_pipeline(
            {'nodes': {'a': {'executable': 'echo a', 'name': 'a'}}}
        )

    # Unknown top-level key.
    with pytest.raises(ValueError, match='unknown top-level'):
        load_yaml_pipeline({**base, 'matrix': {}})

    # Edge referencing an unknown node.
    with pytest.raises(ValueError, match='unknown node'):
        load_yaml_pipeline({**base, 'edges': ['a.dst -> missing.src']})

    # Edge referencing an unknown port.
    with pytest.raises(ValueError, match='unknown port'):
        load_yaml_pipeline({**base, 'edges': ['a.nope -> a.dst']})

    # Edge string without an arrow.
    with pytest.raises(ValueError, match='->'):
        load_yaml_pipeline({**base, 'edges': ['a.dst a.dst']})

    # Empty nodes mapping.
    with pytest.raises(ValueError, match='non-empty'):
        load_yaml_pipeline({'nodes': {}})


def test_load_result_escape_hatch(tmp_path):
    """
    A node can opt into a Python ``load_result`` via a dotted path, invoked as
    ``func(node, node_dpath)``. Used for tools whose output the generic loader
    cannot describe.
    """
    import sys

    from kwdagger.yaml_pipeline import _import_callable, load_yaml_pipeline

    loader_mod = ub.Path(tmp_path) / 'custom_loader_mod.py'
    loader_mod.write_text(
        ub.codeblock(
            """
            def my_loader(node, node_dpath):
                # Prove we received the node (with its name) and the dir.
                return {f'metrics.{node.name}.sentinel': 42,
                        'seen_dpath': str(node_dpath)}
            """
        )
    )
    sys.path.insert(0, str(tmp_path))
    try:
        # The importer resolves a free function by dotted path.
        assert _import_callable('custom_loader_mod.my_loader')(
            type('N', (), {'name': 'n'})(), 'd'
        ) == {'metrics.n.sentinel': 42, 'seen_dpath': 'd'}

        dag = load_yaml_pipeline(
            {
                'nodes': {
                    'evalnode': {
                        'executable': 'echo hi',
                        'in_paths': ['src'],
                        'out_paths': {'dst': 'out.json'},
                        'load_result': 'custom_loader_mod.my_loader',
                    }
                }
            }
        )
        node = dag.node_dict['evalnode']
        result = node.load_result(ub.Path('/some/node/dpath'))
        assert result == {
            'metrics.evalnode.sentinel': 42,
            'seen_dpath': '/some/node/dpath',
        }
    finally:
        sys.path.remove(str(tmp_path))

    # A bad reference raises a clear ImportError.
    import pytest

    with pytest.raises(ImportError):
        _import_callable('definitely.not.a.real.module.fn')


def test_node_class_reference(tmp_path):
    """
    A node spec can reference a Python ProcessNode subclass via ``class:`` to
    bring *behavioral* overrides (e.g. a custom ``command``) that cannot be
    serialized as data. The YAML supplies the data; the class supplies behavior.
    """
    import sys

    import pytest

    from kwdagger.yaml_pipeline import load_yaml_pipeline

    mod = ub.Path(tmp_path) / 'custom_nodes_mod.py'
    mod.write_text(
        ub.codeblock(
            '''
            from kwdagger.pipeline import ProcessNode
            from kwdagger.yaml_pipeline import YamlProcessNode

            class PositionalCommandNode(ProcessNode):
                """A CLI that takes positional args, not --key=value."""
                @property
                def command(self):
                    src = self.final_in_paths.get("src", "")
                    dst = self.final_out_paths.get("dst", "")
                    return f"{self.executable} run {src} {dst}"

            class MetricAwareNode(YamlProcessNode):
                """A YamlProcessNode subclass still accepts YAML metrics."""
                @property
                def command(self):
                    return f"{self.executable} --special"

            class NotANode:
                pass
            '''
        )
    )
    sys.path.insert(0, str(tmp_path))
    root = ub.Path.appdir('kwdagger/unit_tests/yaml_pipeline/classref').ensuredir()
    try:
        # 1. Plain ProcessNode subclass: the command override is honored.
        dag = load_yaml_pipeline(
            {
                'nodes': {
                    'step': {
                        'class': 'custom_nodes_mod.PositionalCommandNode',
                        'executable': 'mytool',
                        'in_paths': ['src'],
                        'out_paths': {'dst': 'out.txt'},
                        'algo_params': {'alpha': 0.1},
                    }
                }
            }
        )
        node = dag.node_dict['step']
        assert type(node).__name__ == 'PositionalCommandNode'
        dag.configure(
            config={'step.src': '/data/in.txt', 'step.alpha': 0.5},
            root_dpath=root,
            cache=False,
        )
        cmd = node.final_command()
        assert 'mytool run /data/in.txt' in cmd  # positional override used
        assert '--src=' not in cmd  # NOT the default --key=value form

        # 2. YamlProcessNode subclass: class override AND YAML metrics together.
        dag2 = load_yaml_pipeline(
            {
                'nodes': {
                    'ev': {
                        'class': 'custom_nodes_mod.MetricAwareNode',
                        'executable': 'evtool',
                        'out_paths': {'dst': 'm.json'},
                        'metrics': [{'metric': 'acc', 'primary': True}],
                    }
                }
            }
        )
        ev = dag2.node_dict['ev']
        assert ev.default_metrics() == [{'metric': 'acc', 'primary': True}]
        dag2.configure(config={}, root_dpath=root, cache=False)
        assert 'evtool --special' in ev.final_command()

        # 3. Error: the referenced class is not a ProcessNode subclass.
        with pytest.raises(TypeError, match='ProcessNode subclass'):
            load_yaml_pipeline(
                {'nodes': {'x': {'class': 'custom_nodes_mod.NotANode'}}}
            )

        # 4. Error: data-driven keys on a non-YamlProcessNode subclass.
        with pytest.raises(ValueError, match='YamlProcessNode subclass'):
            load_yaml_pipeline(
                {
                    'nodes': {
                        'x': {
                            'class': 'custom_nodes_mod.PositionalCommandNode',
                            'executable': 't',
                            'metrics': [{'metric': 'a'}],
                        }
                    }
                }
            )
    finally:
        sys.path.remove(str(tmp_path))


def test_schedule_with_inline_yaml_pipeline():
    """
    A full ``kwdagger schedule`` dry-run where the pipeline is declared inline
    in the same ``--params`` document as the matrix.
    """
    from kwdagger import schedule

    dpath = ub.Path.appdir(
        'kwdagger/unit_tests/yaml_pipeline/schedule'
    ).ensuredir()
    input_fpath = dpath / 'input.json'
    input_fpath.write_text('{"type": "orig_input"}')
    root_dpath = (dpath / 'runs').delete().ensuredir()

    params = ub.codeblock(
        f"""
        pipeline:
          nodes:
            step1:
              executable: "echo step1"
              in_paths: [src]
              out_paths: {{dst: step1_output.json}}
              algo_params: {{param1: null, param2: null}}
              perf_params: {{workers: 0}}
        matrix:
          step1.src:
            - {input_fpath}
          step1.param1: [option1, option2]
          step1.param2: [0.1, 0.2]
        """
    )
    config = schedule.ScheduleEvaluationConfig(
        run=0, root_dpath=root_dpath, backend='serial', params=params
    )
    dag, queue = schedule.build_schedule(config)
    # 2 param1 values x 2 param2 values = 4 configs.
    assert len(queue) >= 4
    assert sorted(dag.node_dict) == ['step1']


def _write_eval_script(dpath):
    """Write a self-contained evaluation CLI that emits ProcessContext info."""
    script_fpath = dpath / 'eval_node.py'
    text = ub.codeblock(
        """
        #!/usr/bin/env python3
        import json
        import kwutil
        import scriptconfig as scfg
        import ubelt as ub


        class EvalCLI(scfg.DataConfig):
            src = None
            dst = 'metrics.json'
            thresh = 0.5
            workers = 0

            @classmethod
            def main(cls, argv=1, **kwargs):
                config = cls.cli(argv=argv, data=kwargs, strict=True)
                proc = kwutil.ProcessContext(
                    name='eval_node', type='process',
                    config=kwutil.Json.ensure_serializable(dict(config)),
                    track_emissions=False)
                proc.start()
                src_text = ub.Path(config.src).read_text()
                score = round((len(src_text) % 10) / 10 + float(config.thresh), 4)
                data = {'info': [], 'result': {'metrics': {
                    'score': score, 'count': len(src_text)}}}
                data['info'].append(proc.stop())
                dst = ub.Path(config.dst)
                dst.parent.ensuredir()
                dst.write_text(json.dumps(data))

        __cli__ = EvalCLI

        if __name__ == '__main__':
            __cli__.main()
        """
    )
    compile(text, mode='exec', filename='<test-compile>')
    script_fpath.write_text(text)
    return script_fpath


def test_yaml_eval_node_end_to_end():
    """
    Run a real YAML eval node serially, then aggregate it. This exercises the
    generic ``load_result`` and the declarative ``metrics`` metadata.
    """
    from kwdagger import aggregate, schedule

    dpath = ub.Path.appdir(
        'kwdagger/unit_tests/yaml_pipeline/eval'
    ).ensuredir()
    script_fpath = _write_eval_script(dpath)
    input_fpath = dpath / 'input.txt'
    input_fpath.write_text('hello world')
    root_dpath = (dpath / 'runs').delete().ensuredir()

    pipeline_fpath = dpath / 'pipeline.yaml'
    pipeline_fpath.write_text(
        ub.codeblock(
            f"""
            nodes:
              eval_node:
                executable: "python {script_fpath}"
                in_paths: [src]
                out_paths: {{dst: metrics.json}}
                perf_params: {{workers: 0}}
                result:
                  metrics: result.metrics
                metrics:
                  - {{metric: score, objective: maximize, primary: true, display: true}}
                  - {{metric: count, objective: minimize, display: true}}
            """
        )
    )

    params = ub.codeblock(
        f"""
        matrix:
          eval_node.src:
            - {input_fpath}
          eval_node.thresh: [0.1, 0.9]
        """
    )
    config = schedule.ScheduleEvaluationConfig(
        run=1,
        root_dpath=root_dpath,
        backend='serial',
        pipeline=str(pipeline_fpath),
        params=params,
    )
    dag, queue = schedule.build_schedule(config)

    # The output files should have been produced by the serial run.
    produced = list(dag.root_dpath.glob('**/eval_node/*/metrics.json'))
    assert len(produced) == 2, f'expected 2 outputs, got {produced}'

    # Now aggregate and confirm the generic loader surfaced the metrics.
    agg_config = aggregate.AggregateEvluationConfig(
        target=root_dpath,
        pipeline=str(pipeline_fpath),
        output_dpath=(root_dpath / 'aggregate'),
        io_workers=0,
        eval_nodes=['eval_node'],
    )
    eval_type_to_aggregator = aggregate.run_aggregate(agg_config)
    agg = eval_type_to_aggregator['eval_node']
    assert len(agg) == 2
    metric_cols = list(agg.table.search_columns('metrics.eval_node'))
    assert any(c.endswith('.score') for c in metric_cols)
    assert any(c.endswith('.count') for c in metric_cols)


def _write_python_pipeline_module(dpath, script_fpath):
    """Write a Python pipeline module with a custom ProcessNode subclass."""
    mod_fpath = dpath / 'rt_pipeline_mod.py'
    text = ub.codeblock(
        f"""
        from kwdagger.pipeline import ProcessNode, Pipeline


        class RtEval(ProcessNode):
            name = 'rt_eval'
            executable = 'python {script_fpath}'
            in_paths = {{'src'}}
            out_paths = {{'dst': 'metrics.json'}}
            algo_params = {{'thresh': 0.5}}

            def load_result(self, node_dpath):
                import json
                from kwdagger.aggregate_loader import new_process_context_parser
                from kwdagger.utils import util_dotdict
                fpath = node_dpath / self.out_paths[self.primary_out_key]
                data = json.loads(fpath.read_text())
                nested = new_process_context_parser(data['info'][-1])
                nested['metrics'] = data['result']['metrics']
                flat = util_dotdict.DotDict.from_nested(nested)
                return flat.insert_prefix(self.name, index=1)

            def default_metrics(self):
                return [{{'metric': 'score', 'objective': 'maximize',
                         'primary': True, 'display': True}}]


        def build():
            return Pipeline({{'rt_eval': RtEval()}})
        """
    )
    compile(text, mode='exec', filename='<test-compile>')
    mod_fpath.write_text(text)
    return mod_fpath


def test_schedule_aggregate_python_pipeline_round_trip():
    """
    A *Python*-defined pipeline round-trips through serialization: schedule
    serializes it to a declarative spec (custom node -> ``class:`` reference),
    and aggregate reconstructs it with no ``--pipeline``.
    """
    import json
    import sys

    from kwdagger import aggregate, schedule

    dpath = ub.Path.appdir(
        'kwdagger/unit_tests/yaml_pipeline/py_round_trip'
    ).ensuredir()
    script_fpath = _write_eval_script(dpath)
    _write_python_pipeline_module(dpath, script_fpath)
    input_fpath = dpath / 'input.txt'
    input_fpath.write_text('hello world')
    root_dpath = (dpath / 'runs').delete().ensuredir()

    sys.path.insert(0, str(dpath))
    try:
        config = schedule.ScheduleEvaluationConfig(
            run=1,
            root_dpath=root_dpath,
            backend='serial',
            pipeline='rt_pipeline_mod.build()',
            params=ub.codeblock(
                f"""
                matrix:
                  rt_eval.src:
                    - {input_fpath}
                  rt_eval.thresh: [0.1, 0.9]
                """
            ),
        )
        schedule.build_schedule(config)

        # The Python pipeline was serialized to a declarative spec, with the
        # custom node emitted as an importable class reference.
        meta = json.loads(
            (root_dpath / '_kwdagger_schedule' / 'most_recent_run.json').read_text()
        )
        assert isinstance(meta['pipeline'], dict)
        assert (
            meta['pipeline']['nodes']['rt_eval']['class']
            == 'rt_pipeline_mod.RtEval'
        )

        # Aggregate with NO --pipeline: reconstructed from the serialized spec.
        agg_config = aggregate.AggregateEvluationConfig(
            target=root_dpath,
            output_dpath=(root_dpath / 'aggregate'),
            io_workers=0,
            eval_nodes=['rt_eval'],
        )
        eval_type_to_aggregator = aggregate.run_aggregate(agg_config)
        agg = eval_type_to_aggregator['rt_eval']
        assert len(agg) == 2
        assert any(
            c.endswith('.score')
            for c in agg.table.search_columns('metrics.rt_eval')
        )
    finally:
        sys.path.remove(str(dpath))
        sys.modules.pop('rt_pipeline_mod', None)


def test_aggregate_autodiscovers_inline_pipeline():
    """
    The end-to-end "fully inline" flow: schedule a run whose pipeline is defined
    inline in ``--params``, then aggregate it WITHOUT re-specifying the pipeline.
    Aggregate must recover the serialized pipeline from the schedule metadata.
    """
    from kwdagger import aggregate, schedule

    dpath = ub.Path.appdir(
        'kwdagger/unit_tests/yaml_pipeline/autodiscover'
    ).ensuredir()
    script_fpath = _write_eval_script(dpath)
    input_fpath = dpath / 'input.txt'
    input_fpath.write_text('hello world')
    root_dpath = (dpath / 'runs').delete().ensuredir()

    # Pipeline AND matrix both inline in the same params document.
    params = ub.codeblock(
        f"""
        pipeline:
          nodes:
            eval_node:
              executable: "python {script_fpath}"
              in_paths: [src]
              out_paths: {{dst: metrics.json}}
              perf_params: {{workers: 0}}
              result:
                metrics: result.metrics
              metrics:
                - {{metric: score, objective: maximize, primary: true, display: true}}
        matrix:
          eval_node.src:
            - {input_fpath}
          eval_node.thresh: [0.1, 0.9]
        """
    )
    config = schedule.ScheduleEvaluationConfig(
        run=1, root_dpath=root_dpath, backend='serial', params=params
    )
    schedule.build_schedule(config)

    # The pipeline was serialized for later use.
    meta = root_dpath / '_kwdagger_schedule' / 'most_recent_run.json'
    assert meta.exists()

    # Aggregate WITHOUT a --pipeline; it must be auto-discovered.
    agg_config = aggregate.AggregateEvluationConfig(
        target=root_dpath,
        output_dpath=(root_dpath / 'aggregate'),
        io_workers=0,
        eval_nodes=['eval_node'],
    )
    assert agg_config.pipeline == 'auto'
    eval_type_to_aggregator = aggregate.run_aggregate(agg_config)
    agg = eval_type_to_aggregator['eval_node']
    assert len(agg) == 2
    assert any(
        c.endswith('.score')
        for c in agg.table.search_columns('metrics.eval_node')
    )


def test_dump_yaml_pipeline_round_trips():
    """A YAML pipeline survives dump -> reload, including the data-driven extras."""
    from kwdagger.yaml_pipeline import dump_yaml_pipeline, load_yaml_pipeline

    spec = {
        'nodes': {
            'predict': {
                'executable': 'python predict.py',
                'in_paths': ['src_fpath'],
                'out_paths': {'dst_fpath': 'pred.json'},
                'algo_params': {'keyword': 'great'},
                'perf_params': {'workers': 0},
            },
            'evaluate': {
                'executable': 'python evaluate.py',
                'in_paths': ['true_fpath', 'pred_fpath'],
                'out_paths': {'out_fpath': 'metrics.json'},
                'result': {'metrics': 'result.metrics'},
                # Any importable callable; only the reference string is tested.
                'load_result': 'kwdagger.yaml_pipeline._dotted_get',
                'metrics': [{'metric': 'acc', 'primary': True}],
            },
        },
        'edges': [
            'predict.dst_fpath -> evaluate.pred_fpath',
            'predict.src_fpath -> evaluate.true_fpath',
        ],
    }
    dag = load_yaml_pipeline(spec)
    dumped = dump_yaml_pipeline(dag)

    ev = dumped['nodes']['evaluate']
    assert ev['load_result'] == 'kwdagger.yaml_pipeline._dotted_get'
    assert ev['result'] == {'metrics': 'result.metrics'}
    assert ev['metrics'] == [{'metric': 'acc', 'primary': True}]
    assert sorted(dumped['edges']) == sorted(spec['edges'])
    # Plain data nodes do NOT get a spurious class reference.
    assert 'class' not in dumped['nodes']['predict']

    # Reloading the dumped spec reproduces the graph.
    dag2 = load_yaml_pipeline(dumped)
    assert sorted(dag2.node_dict) == sorted(dag.node_dict)
    assert (
        dag2.node_dict['evaluate'].inputs['pred_fpath'].pred[0].key
        == 'predict.dst_fpath'
    )


def test_dump_rejects_unimportable_node_class():
    """A node class defined in a local scope cannot be serialized."""
    import pytest

    from kwdagger.pipeline import Pipeline, ProcessNode

    class LocalNode(ProcessNode):  # __qualname__ contains "<locals>"
        name = 'local'
        executable = 'echo hi'
        out_paths = {'dst': 'o.txt'}

    dag = Pipeline({'local': LocalNode()})
    with pytest.raises(ValueError, match='not importable'):
        dag.to_yaml_spec()


def test_aggregate_autodiscovery_failure_is_clear(tmp_path):
    """When there is no schedule metadata, 'auto' fails with a helpful error."""
    import pytest

    from kwdagger import aggregate

    target = ub.Path(tmp_path) / 'not_a_schedule_dir'
    target.ensuredir()
    agg_config = aggregate.AggregateEvluationConfig(
        target=target,
        output_dpath=(target / 'aggregate'),
        io_workers=0,
    )
    with pytest.raises(ValueError, match='auto-discovered'):
        aggregate.run_aggregate(agg_config)


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/kwdagger/tests/test_yaml_pipeline.py
    """
    import xdoctest

    xdoctest.doctest_module(__file__)
