#!/usr/bin/env python3
r"""
Helper for scheduling a set of prediction + evaluation jobs.

This is the main entrypoint for running a bunch of evaluation jobs over a grid
of parameters. We currently expect that pipelines are predefined in
smart_pipeline.py but in the future they will likely be an external resource
file.

TODO:
    - [ ] Differentiate between pixel models for different tasks.
    - [ ] Allow the output of tracking to feed into activity classification
    - [x] Rename to "schedule". The pipeline does not have to be an evaluation.
"""
import ubelt as ub
import scriptconfig as scfg
from cmd_queue.cli_boilerplate import CMDQueueConfig
from kwdagger.pipeline import coerce_slurm_options


class ScheduleEvaluationConfig(CMDQueueConfig):
    """
    Driver for KWDagger scheduling

    Builds commands and optionally executes them via slurm, tmux, or serial
    (i.e. one at a time). This is a [link=https://gitlab.kitware.com/computer-vision/cmd_queue]cmd_queue[/link] CLI.
    """
    params = scfg.Value(None, type=str, help='a yaml/json grid/matrix of prediction params')

    devices = scfg.Value(None, help=(
        'if using tmux or serial, indicate which gpus are available for use '
        'as a comma separated list: e.g. 0,1'))

    skip_existing = scfg.Value(False, help=(
        'if True dont submit commands where the expected '
        'products already exist'))

    pred_workers = scfg.Value(4, help='number of prediction workers in each process')

    root_dpath = scfg.Value('./kwdagger_output', help=(
        'Where do dump all results. If "auto", uses <expt_dvc_dpath>/dag_runs'))

    pipeline = scfg.Value(None, help=ub.paragraph(
        '''
        The name of the pipeline to run. Can also specify this in the params.
        This should be a name of an internally registered pipeline, or it can
        point to a function that defines a pipeline in a Python file. E.g.
        ``user_module.pipelines.custom_pipeline_func()`` or
        ``$HOME/my_code/my_pipeline.py::make_my_pipeline("arg")``.
        '''))

    enable_links = scfg.Value(True, isflag=True, help='if true enable symlink jobs')
    cache = scfg.Value(True, isflag=True, help=(
        'if true, each a test is appened to each job to skip itself if its output exists'))

    max_configs = scfg.Value(None, help='if specified only run at most this many of the grid search configs')

    queue_size = scfg.Value(None, help='if auto, defaults to number of GPUs')

    print_varied = scfg.Value('auto', isflag=True, help='print the varied parameters')

    def __post_init__(self):
        super().__post_init__()
        if self.queue_name is None:
            self.queue_name = 'schedule-eval'
        if self.queue_size is not None:
            raise Exception('The queue_size argument to schedule evaluation has been removed. Use the tmux_workers argument instead')
            # self.tmux_workers = self.queue_size
        self.slurm_options = coerce_slurm_options(self.slurm_options)

        devices = self.devices
        if devices == 'auto':
            GPUS = _auto_gpus()
        else:
            GPUS = None if devices is None else ensure_iterable(devices)
        self.devices = GPUS

    def main(argv=True, **kwargs):
        config = ScheduleEvaluationConfig.cli(argv=argv, data=kwargs, strict=True, verbose='auto')
        build_schedule(config)


def build_schedule(config):
    r"""
    First ensure that models have been copied to the DVC repo in the
    appropriate path. (as noted by model_dpath)
    """
    import json
    import pandas as pd
    import rich
    import kwutil
    from kwutil import slugify_ext
    from kwutil import util_progress
    from kwdagger.pipeline import coerce_pipeline
    from kwdagger.utils.result_analysis import varied_values
    from kwdagger.utils.util_param_grid import expand_param_grid

    root_dpath = ub.Path(config['root_dpath'])
    pipeline = config.pipeline

    param_slurm_options = {}
    if config['params'] is not None:
        param_arg = kwutil.Yaml.coerce(config['params']) or {}
        if isinstance(param_arg, dict):
            param_slurm_options = coerce_slurm_options(param_arg.pop('slurm_options', None))
        pipeline = param_arg.pop('pipeline', config.pipeline)

    if param_slurm_options:
        config.slurm_options = ub.udict(config.slurm_options) | param_slurm_options

    # Load the requested pipeline
    dag = coerce_pipeline(pipeline)
    dag.print_graphs()
    dag.inspect_configurables()

    if config.run:
        kwdagger_meta = (root_dpath / '_kwdagger_schedule').ensuredir()
        # Write some metadata to help aggregate set its defaults automatically
        most_recent_fpath = kwdagger_meta / 'most_recent_run.json'
        data = {
            'pipeline': str(pipeline),
        }
        most_recent_fpath.write_text(json.dumps(data, indent='    '))

    queue = config.create_queue(gpus=config.devices)

    # Expand paramater search grid
    if config['params'] is not None:
        # print('param_arg = {}'.format(ub.urepr(param_arg, nl=1)))
        all_param_grid = list(expand_param_grid(
            param_arg,
            max_configs=config['max_configs'],
        ))
    else:
        all_param_grid = []

    if len(all_param_grid) == 0:
        print('WARNING: PARAM GRID IS EMPTY')

    # Configure a DAG for each row.
    pman = util_progress.ProgressManager()
    configured_stats = []
    with pman:
        for row_config in pman.progiter(all_param_grid, desc='configure dags', verbose=3):
            if param_slurm_options and 'slurm_options' not in row_config:
                row_config = ub.udict(row_config)
                row_config['__slurm_options__'] = param_slurm_options
            dag.configure(
                config=row_config,
                root_dpath=root_dpath,
                cache=config['cache'])
            summary = dag.submit_jobs(
                queue=queue,
                skip_existing=config['skip_existing'],
                enable_links=config['enable_links'])
            configured_stats.append(summary)

    print(f'len(queue)={len(queue)}')

    print_thresh = 30
    if config['print_varied'] == 'auto':
        if len(queue) < print_thresh:
            config['print_varied'] = 1
        else:
            print(f'More than {print_thresh} jobs, skip print_varied. '
                  'If you want to see them explicitly specify print_varied=1')
            config['print_varied'] = 0

    if 0 and config['print_varied']:
        # Print config info
        longparams = pd.DataFrame(all_param_grid)
        # FIXME: params don't have to be hashable.
        varied = varied_values(longparams, min_variations=2, dropna=False)
        relevant = longparams[longparams.columns.intersection(varied)]

        def pandas_preformat(item):
            if isinstance(item, str):
                return slugify_ext.smart_truncate(item, max_length=16, trunc_loc=0)
            else:
                return item
        displayable = relevant.applymap(pandas_preformat)
        rich.print(displayable.to_string())

    for job in queue.jobs:
        # TODO: should be able to set this as a queue param.
        job.log = False

    if config.run:
        ub.Path(dag.root_dpath).ensuredir()

    print_kwargs = {
        'with_status': 0,
        'style': "colors",
        'with_locks': 0,
        'exclude_tags': ['boilerplate'],
    }

    rich.print(f'\n\ndag.root_dpath: [link={dag.root_dpath}]{dag.root_dpath}[/link]')
    config.run_queue(queue, print_kwargs=print_kwargs, system=True)

    if not config.run:
        driver_fpath = queue.write()
        print('Wrote script: to run execute:\n{}'.format(driver_fpath))

    return dag, queue


def ensure_iterable(inputs):
    return inputs if ub.iterable(inputs) else [inputs]


def _auto_gpus():
    from kwdagger.utils.util_nvidia import nvidia_smi
    # TODO: liberate the needed code from netharn
    # Use all unused devices
    GPUS = []
    gpu_info = nvidia_smi()
    for gpu_idx, gpu_info in gpu_info.items():
        if len(gpu_info['procs']) == 0:
            GPUS.append(gpu_idx)
    return GPUS


__cli__ = ScheduleEvaluationConfig


if __name__ == '__main__':
    __cli__.main()
