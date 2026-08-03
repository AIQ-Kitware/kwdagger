#!/usr/bin/env python3
r"""
This is a self contained file that contains all the necessary bits to define
and execute a simple mlops pipeline. It is very similar to the tutorial in

../../docs/source/manual/tutorial/examples/README.rst


This pipeline can be run through mlops with the following invocations:

.. code:: bash

    # This script is assumed to be run inside the example directory
    TMP_DPATH=$(mktemp -d --suffix "-mlops-demo")
    cd "$TMP_DPATH"

    echo "data1" > input_file1.txt
    echo "data2" > input_file2.txt

    EVAL_DPATH=$PWD/pipeline_output
    python -m kwdagger.schedule \
        --params="
            pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
            matrix:
                stage1_predict.src_fpath:
                    - input_file1.txt
                    - input_file2.txt
                stage1_predict.param1:
                    - 123
                    - 456
                    - 32
                    - 33
                stage1_evaluate.workers: 4
        " \
        --root_dpath="${EVAL_DPATH}" \
        --tmux_workers=2 \
        --backend=tmux --skip_existing=1 \
        --run=1


    EVAL_DPATH=$PWD/pipeline_output
    python -m kwdagger.aggregate \
        --pipeline='kwdagger.demo.demodata.my_demo_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --resource_report=1 \
        --io_workers=0 \
        --eval_nodes="
            - stage1_evaluate
        " \
        --stdout_report="
            top_k: 100
            per_group: null
            macro_analysis: 0
            analyze: 0
            print_models: True
            reference_region: null
            concise: 1
            show_csv: 0
        " \
        --plot_params="
            enabled: 1
        " \
        --cache_resolved_results=False

It can also be run within Python because every scriptconfig CLI always has a
corresponding way to invoke it with a simple python dictionary.

Example:
    >>> from kwdagger.demo.demodata import *  # NOQA
    >>> from kwdagger import schedule
    >>> # For this demo we always delete / regenerate for CI coverage
    >>> # For other demos we allow resusing cache
    >>> eval_dpath = ub.Path.appdir('kwdagger/demo2/pipeline_output').ensuredir()
    >>> eval_dpath.delete().ensuredir()
    >>> schedule_config = kwutil.Yaml.coerce(
    ...     r'''
    ...     backend: serial
    ...     skip_existing: 1
    ...     run: 1
    ...     params:
    ...         pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
    ...         matrix:
    ...             stage1_predict.param1:
    ...                 - 123
    ...                 # Remove extra params to speedup tests
    ...                 # - 456
    ...                 # - 32
    ...                 # - 33
    ...             stage1_evaluate.workers: 4
    ...     ''')
    >>> schedule_config['root_dpath'] = eval_dpath
    >>> # Specify files with absolute paths, so we dont need to cd
    >>> fpath1 = (eval_dpath / 'file1.txt')
    >>> fpath2 = (eval_dpath / 'file2.txt')
    >>> fpath1.write_text('data1')
    >>> fpath2.write_text('data2')
    >>> schedule_config['params']['matrix']['stage1_predict.src_fpath'] = [
    ...     fpath1, fpath2]
    >>> schedule.__cli__.main(argv=False, **schedule_config)
    >>> #
    >>> # Also load the results
    >>> from kwdagger import aggregate
    >>> aggregate_config = kwutil.Yaml.coerce(
    >>>     '''
    >>>     pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
    >>>     resource_report: 0
    >>>     io_workers: 0
    >>>     eval_nodes:
    >>>         - stage1_evaluate
    >>>     stdout_report:
    >>>         top_k: 100
    >>>         per_group: null
    >>>         macro_analysis: 0
    >>>         analyze: 0
    >>>         print_models: True
    >>>         reference_region: null
    >>>         concise: 1
    >>>         show_csv: 0
    >>>     plot_params:
    >>>         enabled: 0
    >>>     cache_resolved_results: False
    >>>     ''')
    >>> aggregate_config['target'] = [eval_dpath]
    >>> aggregate_config['output_dpath'] = eval_dpath / 'full_aggregate'
    >>> aggregate.__cli__.main(argv=False, **aggregate_config)
"""

from __future__ import annotations

import json
from typing import Any

import kwutil
import scriptconfig as scfg
import ubelt as ub

from kwdagger.pipeline import Pipeline, ProcessNode
from kwdagger.utils import util_dotdict

### EXECUTABLE PROCESS CODE


class Stage1PredictCLI(scfg.DataConfig):
    """
    The logic for the demo "prediction" process.
    """

    __command__ = 'stage1_predict'

    src_fpath = scfg.Value(None, help='path to input file')
    dst_fpath = scfg.Value(None, help='path to output file')
    dst_dpath = scfg.Value(None, help='path to output directory')

    param1 = scfg.Value(None, help='some important parameter')
    workers = scfg.Value(0, help='number of parallel workers')

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(
            argv=argv,  # type: ignore
            data=kwargs,
            strict=True,
            verbose='auto',
        )

        data: dict[str, Any] = {'info': [], 'result': None}

        proc_context = kwutil.ProcessContext(
            name='stage1_predict',
            type='process',
            config=kwutil.Json.ensure_serializable(dict(config)),
            track_emissions=True,
        )
        proc_context.start()

        print('Load file')
        text = ub.Path(config.src_fpath).read_text()

        # A dummy prediction computation
        data['result'] = ub.hash_data(str(config.param1) + str(text))

        obj = proc_context.stop()
        data['info'].append(obj)

        dst_fpath = ub.Path(config.dst_fpath)
        dst_fpath.parent.ensuredir()

        dst_fpath.write_text(json.dumps(data))
        print(f'Wrote to: dst_fpath={dst_fpath}')


class Stage1EvaluateCLI(scfg.DataConfig):
    """
    The logic for the demo "evaluation" process.
    """

    __command__ = 'stage1_evaluate'

    pred_fpath = scfg.Value(None, help='path to predicted file')
    true_fpath = scfg.Value(None, help='path to truth file')
    out_fpath = scfg.Value(None, help='path to evaluation file')
    workers = scfg.Value(0, help='number of parallel workers')

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(
            argv=argv,  # type: ignore
            data=kwargs,
            strict=True,
            verbose='auto',
        )

        data: dict[str, Any] = {'info': [], 'result': None}

        proc_context = kwutil.ProcessContext(
            name='stage1_evaluate',
            type='process',
            config=kwutil.Json.ensure_serializable(dict(config)),
            track_emissions=True,
        )
        proc_context.start()

        print('Load file')
        true_text = ub.Path(config.true_fpath).read_text()
        pred_text = ub.Path(config.pred_fpath).read_text()

        true_hashid = ub.hash_data(true_text)
        pred_hashid = ub.hash_data(pred_text)
        true_int = int(true_hashid, 16)
        pred_int = int(pred_hashid, 16)
        hamming_distance = bin(true_int ^ pred_int).count('1')
        size = len(true_hashid) * 4
        acc = (size - hamming_distance) / size

        metrics: dict[str, Any] = {
            'accuracy': acc,
            'hamming_distance': hamming_distance,
        }

        # A dummy evaluate computation
        data['result'] = metrics

        obj = proc_context.stop()
        data['info'].append(obj)

        out_fpath = ub.Path(config.out_fpath)
        out_fpath.parent.ensuredir()
        out_fpath.write_text(json.dumps(data))
        print(f'wrote to: out_fpath={out_fpath}')


class DemodataScript(scfg.ModalCLI):
    """
    To self contain multiple "processes" in the same file we make a simple
    modal CLI.
    """

    stage1_predict = Stage1PredictCLI
    stage1_evaluate = Stage1EvaluateCLI


__cli__ = DemodataScript


#### PIPELINE DEFINITION CODE


class Stage1_Predict(ProcessNode):
    """
    Example:
        >>> from kwdagger.demo.demodata import *  # NOQA
        >>> self = Stage1_Predict()
        >>> print(self.command)
    """

    name = 'stage1_predict'
    executable = 'python -m kwdagger.demo.demodata stage1_predict'

    in_paths = {
        'src_fpath',
    }
    out_paths = {
        'dst_fpath': 'stage1_prediction.json',
        'dst_dpath': '.',
    }
    primary_out_key = 'dst_fpath'

    algo_params = {
        'param1': 1,
    }
    perf_params = {
        'workers': 0,
    }

    def load_result(self, node_dpath: Any) -> util_dotdict.DotDict:
        import json

        from kwdagger.aggregate_loader import new_process_context_parser

        # primary_out_key is derived/required by the time a node loads its
        # result; assert it so the out_paths lookup is well-typed.
        assert self.primary_out_key is not None
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        assert self.name is not None
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved


class Stage1_Evaluate(ProcessNode):
    """
    Example:
        >>> from kwdagger.demo.demodata import *  # NOQA
        >>> self = Stage1_Evaluate()
        >>> print(self.command)
    """

    name = 'stage1_evaluate'
    executable = 'python -m kwdagger.demo.demodata stage1_evaluate'

    in_paths = {
        'true_fpath',
        'pred_fpath',
    }
    out_paths = {
        'out_fpath': 'stage1_evaluation.json',
    }
    algo_params = {}
    perf_params = {
        'workers': 0,
    }

    def load_result(self, node_dpath: Any) -> util_dotdict.DotDict:
        """
        The specific implementation uses convinience functions that rely on how
        the script implemention stores results, but any manual implementation
        will work if it returns a flat dict items of the form:
        ``"metrics.<node_name>.<metric>": <value>``.

        Returns:
            Dict[str, Any]
        """
        import json

        from kwdagger.aggregate_loader import new_process_context_parser

        # primary_out_key is derived/required by the time a node loads its
        # result; assert it so the out_paths lookup is well-typed.
        assert self.primary_out_key is not None
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        nest_resolved['metrics'] = result['result']
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        assert self.name is not None
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved

    def default_metrics(self) -> list[dict[str, Any]]:
        """
        Returns:
            List[Dict]: containing information on how to interpret and
            prioritize the metrics returned here.
        """
        metric_infos = [
            {
                'metric': 'accuracy',
                'objective': 'maximize',
                'primary': True,
                'display': True,
            },
            {
                'metric': 'hamming_distance',
                'objective': 'minimize',
                'primary': True,
                'display': True,
            },
        ]
        return metric_infos

    @property
    def default_vantage_points(self) -> list[dict[str, Any]]:
        vantage_points = [
            {
                'metric1': 'metrics.stage1_evaluate.accuracy',
                'metric2': 'metrics.stage1_evaluate.hamming_distance',
            },
        ]
        return vantage_points


def my_demo_pipeline() -> Pipeline:
    """
    Example:
        >>> from kwdagger.demo.demodata import *  # NOQA
        >>> dag = my_demo_pipeline()
        >>> dag.configure({
        ...     'stage1_predict.src_fpath': 'my-input-path',
        ... })
        >>> dag.print_graphs(shrink_labels=1, show_types=1)
        >>> queue = dag.make_queue()['queue']
        >>> queue.print_commands(with_locks=0)

    Ignore:
        from graphid import util
        proc_graph = dag.proc_graph.copy()
        util.util_graphviz.dump_nx_ondisk(proc_graph, 'proc_graph.png')
        import xdev
        xdev.startfile('proc_graph.png')
    """
    # Define the nodes as stages in the pipeline
    nodes: dict[str, Any] = {}
    nodes['stage1_predict'] = Stage1_Predict()
    nodes['stage1_evaluate'] = Stage1_Evaluate()

    # Next we build the edges

    # Outputs can be connected to inputs
    nodes['stage1_predict'].outputs['dst_fpath'].connect(
        nodes['stage1_evaluate'].inputs['pred_fpath']
    )

    # Inputs can be connected to other inputs if they are reused.
    nodes['stage1_predict'].inputs['src_fpath'].connect(
        nodes['stage1_evaluate'].inputs['true_fpath']
    )

    dag = Pipeline(nodes)
    dag.build_nx_graphs()
    return dag


### Programatic code to execute the pipeline that can be used in tests


def run_demo_schedule() -> dict[str, Any]:
    """
    Example:
        from kwdagger.demo.demodata import run_demo_schedule
        run_demo_schedule()
    """
    # TODO: use these in doctests in a useful way where
    # the doctest has some control
    from kwdagger import schedule

    eval_dpath = ub.Path.appdir('kwdagger/demo1/pipeline_output').ensuredir()
    schedule_config = kwutil.Yaml.coerce(
        r"""
        backend: serial
        skip_existing: 1
        run: 1
        params:
            pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
            matrix:
                stage1_predict.param1:
                    - 123
                    - 456
                    - 33
                stage1_evaluate.workers: 4
        """
    )
    schedule_config['root_dpath'] = eval_dpath
    # Specify files with absolute paths, so we dont need to cd
    fpath1 = eval_dpath / 'file1.txt'
    fpath2 = eval_dpath / 'file2.txt'
    fpath1.write_text('data1')
    fpath2.write_text('data2')
    schedule_config['params']['matrix']['stage1_predict.src_fpath'] = [
        fpath1,
        fpath2,
    ]
    schedule.__cli__.main(argv=False, **schedule_config)

    info = {
        'pipeline': 'kwdagger.demo.demodata.my_demo_pipeline()',
        'eval_dpath': eval_dpath,
    }
    return info


def run_demo_aggregate() -> None:
    # TODO: use these in doctests in a useful way where
    # the doctest has some control
    # Also load the results
    from kwdagger import aggregate

    eval_dpath = ub.Path.appdir('kwdagger/demo1/pipeline_output').ensuredir()
    aggregate_config = kwutil.Yaml.coerce(
        """
        pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
        resource_report: 0
        io_workers: 0
        eval_nodes:
            - stage1_evaluate
        stdout_report:
            top_k: 100
            per_group: null
            macro_analysis: 0
            analyze: 0
            print_models: True
            reference_region: null
            concise: 1
            show_csv: 0
        plot_params:
            enabled: 0
        cache_resolved_results: False
        """
    )
    aggregate_config['target'] = [eval_dpath]
    aggregate_config['output_dpath'] = eval_dpath / 'full_aggregate'
    aggregate.__cli__.main(argv=False, **aggregate_config)
    return None


# ---------------------------------------------------------------------------
# The pipeline behind ``Pipeline.demo()``.
#
# This is throwaway scaffolding -- it writes three tiny scripts to a temp
# directory and wires them together -- so it lives with the rest of the demo
# data rather than in the core module.
# ---------------------------------------------------------------------------


def demodata_pipeline() -> Pipeline:
    """
    A simple test pipeline.

    Example:
        >>> # Self test
        >>> from kwdagger.demo.demodata import *  # NOQA
        >>> demodata_pipeline()
    """
    dpath = ub.Path.appdir('kwdagger/tests/pipeline').ensuredir()
    dpath.delete().ensuredir()
    script_dpath = (dpath / 'src').ensuredir()
    inputs_dpath = (dpath / 'inputs').ensuredir()
    runs_dpath = (dpath / 'runs').ensuredir()

    # Make simple scripts to stand in for the more complex processes that we
    # will orchestrate. The important thing is they have CLI input and output
    # paths / arguments.
    fpath1 = script_dpath / 'demo_script1.py'
    fpath2 = script_dpath / 'demo_script2.py'
    fpath3 = script_dpath / 'demo_script3.py'
    fpath1.write_text(
        ub.codeblock(
            """
        import ubelt as ub
        src = ub.Path(ub.argval('--src'))
        dst = ub.Path(ub.argval('--dst'))
        dst.parent.ensuredir()
        algo_param1 = ub.argval('--algo_param1', default='')
        perf_param1 = ub.argval('--perf_param1', default='')
        dst.write_text(src.read_text() + algo_param1)
        """
        )
    )
    fpath2.write_text(
        ub.codeblock(
            """
        import ubelt as ub
        src1 = ub.Path(ub.argval('--src1'))
        src2 = ub.Path(ub.argval('--src2'))
        dst1 = ub.Path(ub.argval('--dst1'))
        dst2 = ub.Path(ub.argval('--dst2'))
        dst1.parent.ensuredir()
        dst2.parent.ensuredir()
        algo_param2 = ub.argval('--algo_param2', default='')
        perf_param2 = ub.argval('--perf_param2', default='')
        dst1.write_text(src1.read_text() + algo_param2)
        dst2.write_text(src2.read_text() + algo_param2)
        """
        )
    )
    fpath3.write_text(
        ub.codeblock(
            """
        import ubelt as ub
        src1 = ub.Path(ub.argval('--src1'))
        src2 = ub.Path(ub.argval('--src2'))
        dst = ub.Path(ub.argval('--dst'))
        dst.parent.ensuredir()
        algo_param3 = ub.argval('--algo_param3', default='')
        perf_param3 = ub.argval('--perf_param3', default='')
        dst.write_text(src1.read_text() + algo_param3 + src2.read_text())
        """
        )
    )
    executable1 = f'python {fpath1}'
    executable2 = f'python {fpath2}'
    executable3 = f'python {fpath3}'

    # Now that we have executables we need to create a ProcessNode that
    # describes how each process might be run. This can be done via inheritence
    # or specifying constructor variables.
    node_A1 = ProcessNode(
        name='node_A1',
        in_paths={
            'src',
        },
        algo_params={
            'algo_param1': '',
        },
        perf_params={
            'perf_param1': '',
        },
        out_paths={'dst': 'out.txt'},
        executable=executable1,
    )
    node_A2 = ProcessNode(
        name='node_A2',
        in_paths={
            'src',
        },
        algo_params={
            'algo_param1': '',
        },
        perf_params={
            'perf_param1': '',
        },
        out_paths={'dst': 'out.txt'},
        executable=executable1,
    )
    node_B1 = ProcessNode(
        name='node_B1',
        in_paths={'src1', 'src2'},
        algo_params={
            'algo_param2': '',
        },
        perf_params={
            'perf_param2': '',
        },
        out_paths={'dst1': 'out1.txt', 'dst2': 'out2.txt'},
        executable=executable2,
    )
    node_C1 = ProcessNode(
        name='node_C1',
        in_paths={'src1', 'src2'},
        algo_params={
            'algo_param3': '',
        },
        perf_params={
            'perf_param3': '',
        },
        out_paths={'dst': 'out.txt'},
        executable=executable3,
    )

    # Given the process nodes we need to connect their inputs / outputs for
    # form a pipeline.
    node_A1.outputs['dst'].connect(node_B1.inputs['src1'])
    node_A2.outputs['dst'].connect(node_B1.inputs['src2'])
    node_A2.inputs['src'].connect(node_C1.inputs['src1'])
    node_B1.outputs['dst1'].connect(node_C1.inputs['src2'])

    # The pipeline is just a container for the nodes
    nodes = [node_A1, node_A2, node_B1, node_C1]
    dag = Pipeline(nodes=nodes)

    # Given a dag, there will often be top level input parameters that must be
    # configured along with any other algorithm or performance parameters

    # Create the inputs and configure the graph
    input1_fpath = inputs_dpath / 'input1.txt'
    input2_fpath = inputs_dpath / 'input2.txt'
    input1_fpath.write_text('spam')
    input2_fpath.write_text('eggs')

    dag.configure(
        {
            'node_A1.src': str(input1_fpath),
            'node_A2.src': str(input2_fpath),
            'node_A2.dst': dpath / 'DST_OVERRIDE',
            'node_C1.perf_param3': 'GOFAST',
        },
        root_dpath=runs_dpath,
        cache=False,
    )

    return dag


def demo_pipeline_run() -> None:
    """
    A simple test pipeline.

    CommandLine:
        xdoctest -m kwdagger.demo.demodata demo_pipeline_run

    Example:
        >>> # Self test
        >>> from kwdagger.demo.demodata import *  # NOQA
        >>> demo_pipeline_run()
    """
    dag = Pipeline.demo()

    dag.print_graphs()
    dag.inspect_configurables()

    # The jobs can now be submitted to a command queue which can be
    # executed or inspected at your leasure.
    status = dag.submit_jobs(
        queue=ub.udict(
            {
                'backend': 'serial',
            }
        )
    )
    queue = status['queue']
    # queue.print_commands(exclude_tags='boilerplate', with_locks=False)
    queue.print_commands(
        with_status=False, with_gaurds=False, with_locks=1, exclude_tags=None
    )
    queue.run()

if __name__ == '__main__':
    __cli__.main()
