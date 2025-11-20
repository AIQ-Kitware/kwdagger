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
from kwdagger.pipeline import ProcessNode
from kwdagger.pipeline import Pipeline
import ubelt as ub
import scriptconfig as scfg
import kwutil
import json

### EXECUTABLE PROCESS CODE


class Stage1PredictCLI(scfg.DataConfig):
    """
    The logic for the demo "prediction" process.
    """
    __command__ = "stage1_predict"

    src_fpath = scfg.Value(None, help='path to input file')
    dst_fpath = scfg.Value(None, help='path to output file')
    dst_dpath = scfg.Value(None, help='path to output directory')

    param1 = scfg.Value(None, help='some important parameter')
    workers = scfg.Value(0, help='number of parallel workers')

    @classmethod
    def main(cls, argv=1, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True,
                         verbose='auto')

        data = {
            'info': [],
            'result': None
        }

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
    __command__ = "stage1_evaluate"

    pred_fpath = scfg.Value(None, help='path to predicted file')
    true_fpath = scfg.Value(None, help='path to truth file')
    out_fpath = scfg.Value(None, help='path to evaluation file')
    workers = scfg.Value(0, help='number of parallel workers')

    @classmethod
    def main(cls, argv=1, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True,
                         verbose='auto')

        data = {
            'info': [],
            'result': None
        }

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
        size = (len(true_hashid) * 4)
        acc = (size - hamming_distance) / size

        metrics = {
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

    def load_result(self, node_dpath):
        import json
        from kwdagger.aggregate_loader import new_process_context_parser
        from kwdagger.utils import util_dotdict
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
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
    algo_params = {
    }
    perf_params = {
        'workers': 0,
    }

    def load_result(self, node_dpath):
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
        from kwdagger.utils import util_dotdict
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        nest_resolved['metrics'] = result['result']
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved

    def default_metrics(self):
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
            }
        ]
        return metric_infos

    @property
    def default_vantage_points(self):
        vantage_points = [
            {
                'metric1': 'metrics.stage1_evaluate.accuracy',
                'metric2': 'metrics.stage1_evaluate.hamming_distance',
            },
        ]
        return vantage_points


def my_demo_pipeline():
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
    nodes = {}
    nodes['stage1_predict'] = Stage1_Predict()
    nodes['stage1_evaluate'] = Stage1_Evaluate()

    # Next we build the edges

    # Outputs can be connected to inputs
    nodes['stage1_predict'].outputs['dst_fpath'].connect(nodes['stage1_evaluate'].inputs['pred_fpath'])

    # Inputs can be connected to other inputs if they are reused.
    nodes['stage1_predict'].inputs['src_fpath'].connect(nodes['stage1_evaluate'].inputs['true_fpath'])

    dag = Pipeline(nodes)
    dag.build_nx_graphs()
    return dag

### Programatic code to execute the pipeline that can be used in tests


def run_demo_schedule():
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
        r'''
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
        ''')
    schedule_config['root_dpath'] = eval_dpath
    # Specify files with absolute paths, so we dont need to cd
    fpath1 = (eval_dpath / 'file1.txt')
    fpath2 = (eval_dpath / 'file2.txt')
    fpath1.write_text('data1')
    fpath2.write_text('data2')
    schedule_config['params']['matrix']['stage1_predict.src_fpath'] = [
        fpath1, fpath2]
    schedule.__cli__.main(argv=False, **schedule_config)

    info = {
        'pipeline': 'kwdagger.demo.demodata.my_demo_pipeline()',
        'eval_dpath': eval_dpath,
    }
    return info


def run_demo_aggregate():
    # TODO: use these in doctests in a useful way where
    # the doctest has some control
    # Also load the results
    from kwdagger import aggregate
    eval_dpath = ub.Path.appdir('kwdagger/demo1/pipeline_output').ensuredir()
    aggregate_config = kwutil.Yaml.coerce(
        '''
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
        ''')
    aggregate_config['target'] = [eval_dpath]
    aggregate_config['output_dpath'] = eval_dpath / 'full_aggregate'
    aggregate.main(argv=False, **aggregate_config)


if __name__ == '__main__':
    __cli__.main()
