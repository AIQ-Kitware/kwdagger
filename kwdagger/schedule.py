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

from __future__ import annotations

from typing import Any

import kwconf as kw
import ubelt as ub
from cmd_queue.cli_boilerplate import CmdQueueConfigMixin

from kwdagger.pipeline import (
    coerce_slurm_options as pipeline_coerce_slurm_options,
)
from kwdagger.utils import util_pandas


def _default_queue_name(pipeline) -> str:
    """A queue name derived from the pipeline, not shared by every card.

    The name is what cmd_queue's tmux backend uses to decide which sessions
    belong to "this queue" -- conflict detection matches on it. A constant
    meant every card on the machine shared one namespace, so starting an
    Incubilate run reported a Princeton run's sessions as conflicts and offered
    to kill them. Different pipelines are different work and should not
    collide; two runs of the SAME pipeline still should, because that is a real
    conflict worth noticing.

    ``pipeline`` is a spec like ``pkg.mod.lift_pipeline()`` or a registered
    name, so the trailing identifier is the distinguishing part. Anything
    unparseable falls back to the historical name rather than failing: a queue
    name is not worth raising over.
    """
    import re

    text = str(pipeline or '').strip()
    if not text:
        return 'schedule-eval'
    text = text.split('(', 1)[0]          # drop call syntax and its arguments
    ident = text.rsplit('.', 1)[-1]       # keep the final identifier
    ident = re.sub(r'[^A-Za-z0-9_.-]', '', ident)
    return f'schedule-{ident}' if ident else 'schedule-eval'


class ScheduleEvaluationConfig(CmdQueueConfigMixin):
    """
    Driver for KWDagger scheduling

    Builds commands and optionally executes them via slurm, tmux, or serial
    (i.e. one at a time). This is a [link=https://gitlab.kitware.com/computer-vision/cmd_queue]cmd_queue[/link] CLI.
    """

    # NOTE: ``queue_name`` and ``monitor`` are inherited from
    # ``CmdQueueConfigMixin``. kwconf does not smartcast, so ``--monitor=none``
    # stays the string 'none' without the type override the old base needed.

    # Textual monitor OFF by default, overriding the mixin's 'auto'.
    #
    # 'auto' means "on whenever `textual` imports", so whether a run takes over
    # the terminal is decided by a transitive dependency being installed rather
    # than by how the run is being driven. Nearly every kwdagger pipeline here
    # is driven non-interactively -- `bash scripts/...` with stdout teed to a
    # log, over ssh, inside docker, under an agent -- and in that setting a
    # full-screen app is actively harmful: the log fills with escape sequences
    # and redrawn frames instead of node output, and a prompt can block on a
    # keypress nobody is there to press.
    #
    # This changes only the DEFAULT. ``--with_textual=1`` opts back in, and an
    # interactive session that wants the dashboard can still ask for it.
    with_textual: Any = kw.Value(
        False,
        isflag=True,
        help='cmd-queue textual monitor (kwdagger defaults this OFF; pass 1 to enable)',
        group='cmd-queue',
    )

    params = kw.Value(
        None, parser=str, help='a yaml/json grid/matrix of prediction params'
    )

    devices = kw.Value(
        None,
        help=(
            'if using tmux or serial, indicate which gpus are available for use '
            'as a comma separated list: e.g. 0,1 (split in __post_init__; '
            'kwconf does not auto-split comma strings)'
        ),
    )

    skip_existing = kw.Value(
        False,
        help=(
            'if True dont submit commands where the expected '
            'products already exist'
        ),
    )

    pred_workers = kw.Value(
        4, help='number of prediction workers in each process'
    )

    root_dpath = kw.Value(
        './kwdagger_output',
        help=(
            'Where do dump all results. If "auto", uses <expt_dvc_dpath>/dag_runs'
        ),
    )

    pipeline = kw.Value(
        None,
        parser=str,
        help=ub.paragraph(
            """
        The name of the pipeline to run. Can also specify this in the params.
        This should be a name of an internally registered pipeline, or it can
        point to a function that defines a pipeline in a Python file. E.g.
        ``user_module.pipelines.custom_pipeline_func()`` or
        ``$HOME/my_code/my_pipeline.py::make_my_pipeline("arg")``.
        """
        ),
    )

    enable_links = kw.Flag(True, help='if true enable symlink jobs')
    cache = kw.Flag(
        True,
        help=(
            'if true, each a test is appened to each job to skip itself if its output exists'
        ),
    )
    log = kw.Flag(
        True,
        help=ub.paragraph(
            """
            If true (the default), every job's stdout/stderr is teed to a
            log file under the job's ``info_dpath/status/`` directory,
            so failures can be diagnosed after the queue runs. Set to
            false to skip the tee (the underlying subprocess output
            still streams to the parent terminal in serial mode).
            """
        ),
    )

    max_configs = kw.Value(
        None,
        help='if specified only run at most this many of the grid search configs',
    )

    queue_size = kw.Value(None, help='if auto, defaults to number of GPUs')

    print_varied: Any = kw.Value(
        'auto', isflag=True, help='print the varied parameters'
    )

    duplicate_policy = kw.Value(
        'first',
        help=ub.paragraph(
            """
            What to do when two matrix rows compile to one process. They are
            one job either way -- whatever they disagree about is something
            outside process_id, by construction. 'first' (the default) keeps
            the first row encountered and says nothing; 'warn' also describes
            what the later row differed about; 'error' refuses to compile.
            Execution is identical under 'first' and 'warn'.
            """
        ),
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.queue_name is None:
            self.queue_name = _default_queue_name(self.pipeline)
        if self.queue_size is not None:
            raise Exception(
                'The queue_size argument to schedule evaluation has been removed. Use the tmux_workers argument instead'
            )
            # self.tmux_workers = self.queue_size
        self.slurm_options = pipeline_coerce_slurm_options(self.slurm_options)

        devices = self.devices
        if devices == 'auto':
            GPUS = _auto_gpus()
        elif devices is None:
            GPUS = None
        else:
            # kwconf does not auto-split comma strings the way the old
            # smartcast layer did, so split a "0,1" string here (numeric ids
            # are coerced to int to match the historical behavior). A
            # pre-built list (e.g. from a programmatic call) passes through
            # unchanged.
            if isinstance(devices, str):
                devices = [
                    _coerce_device(p.strip())
                    for p in devices.split(',')
                    if p.strip() != ''
                ]
            GPUS = ensure_iterable(devices)
        self.devices = GPUS

    @staticmethod
    def main(argv: bool | list[str] = True, **kwargs: Any) -> None:
        config = ScheduleEvaluationConfig.cli(
            argv=argv,
            data=kwargs,
            strict=True,
            special_options=True,
            verbose='auto',
        )
        build_schedule(config)


def build_schedule(config: Any) -> tuple[Any, Any]:
    r"""
    First ensure that models have been copied to the DVC repo in the
    appropriate path. (as noted by model_dpath)
    """
    import json

    import kwutil
    import pandas as pd
    import rich
    from kwutil import slugify_ext

    from kwdagger.pipeline import coerce_pipeline, normalize_config
    from kwdagger.utils.result_analysis import varied_values
    from kwdagger.utils.util_param_grid import expand_param_grid

    root_dpath = ub.Path(config['root_dpath'])
    pipeline = config.pipeline

    param_slurm_options = {}
    param_arg: Any = {}
    if config['params'] is not None:
        param_arg = kwutil.Yaml.coerce(config['params']) or {}
        if isinstance(param_arg, dict):
            # The configuration boundary, crossed once and here: before the
            # matrix is expanded, before ``slurm_options`` is read out of it,
            # and before anything downstream sees a key. Expanding first would
            # let ``Path('/a')`` and ``'/a'`` count as two points on an axis
            # and then compile to one process, so the grid would report a
            # cardinality the compiled graph contradicts.
            param_arg = normalize_config(param_arg)
            param_slurm_options = pipeline_coerce_slurm_options(
                param_arg.pop('slurm_options', None)
            )
        pipeline = param_arg.pop('pipeline', config.pipeline)

    if param_slurm_options:
        config.slurm_options = (
            ub.udict(config.slurm_options) | param_slurm_options
        )

    # Load the requested pipeline
    dag = coerce_pipeline(pipeline)
    dag.print_graphs()
    dag.inspect_configurables()

    if config.run:
        kwdagger_meta = (root_dpath / '_kwdagger_schedule').ensuredir()
        # Write some metadata to help aggregate set its defaults automatically
        most_recent_fpath = kwdagger_meta / 'most_recent_run.json'
        # Serialize the resolved pipeline to its declarative form so the run is
        # self-describing and ``aggregate`` can reload it without re-running the
        # builder. This works even for Python-defined pipelines (custom nodes
        # become ``class:`` references). Fall back to the original reference if
        # the pipeline cannot be serialized (e.g. a node class in __main__).
        try:
            serialized_pipeline: Any = dag.to_yaml_spec()
        except Exception as ex:
            print(
                'Note: could not serialize the pipeline to its declarative '
                f'form ({ex}); storing the original reference instead.'
            )
            serialized_pipeline = (
                pipeline if isinstance(pipeline, (dict, str)) else str(pipeline)
            )
        data = {
            'pipeline': serialized_pipeline,
        }
        most_recent_fpath.write_text(json.dumps(data, indent='    '))

    queue = config.create_queue(gpus=config.devices)

    # Expand paramater search grid
    if config['params'] is not None:
        # print('param_arg = {}'.format(ub.urepr(param_arg, nl=1)))
        all_param_grid = list(
            expand_param_grid(
                param_arg,
                max_configs=config['max_configs'],
            )
        )
    else:
        all_param_grid = []

    if len(all_param_grid) == 0:
        print('WARNING: PARAM GRID IS EMPTY')

    # One scheduling path: the matrix is compiled, then the compiled graph is
    # submitted. Whether the pipeline contains a gather is a property of what
    # is being compiled, not a choice of how to schedule it -- deciding the
    # execution architecture from a compilation feature is what let the two
    # implementations drift apart.
    #
    # A pipeline-wide default, so it must be the *base* rather than the
    # effective value: a row that omits ``__slurm_options__`` then resets to
    # this instead of inheriting the previous row's request.
    dag._base_slurm_options = pipeline_coerce_slurm_options(
        config.slurm_options
    )
    dag.__slurm_options__ = dict(dag._base_slurm_options)
    compiled = dag.compile_configurations(
        all_param_grid,
        root_dpath=root_dpath,
        cache=config['cache'],
        duplicate_policy=config['duplicate_policy'],
    )
    # Print the concrete cardinality diagnostics before queue submission so
    # users can audit fan-in and fan-out before any execution is possible.
    compiled.print_cardinality_graph()
    print('Compilation summary:')
    for key, value in compiled.compile_summary.items():
        print(f'    {key}: {value}')
    # Matrix-wide rather than per-row: there is one submission now, so there
    # is one summary. Its ``node_status`` covers every concrete process, keyed
    # by ``process_id`` -- strictly more than the old loop reported, which was
    # one dict per row keyed by node name and overwritten as rows repeated.
    compiled.submit_jobs(
        queue=queue,
        skip_existing=config['skip_existing'],
        enable_links=config['enable_links'],
        log=config['log'],
    )
    dag = compiled

    print(f'len(queue)={len(queue)}')

    print_thresh = 30
    if config['print_varied'] == 'auto':
        if len(queue) < print_thresh:
            config['print_varied'] = 1
        else:
            print(
                f'More than {print_thresh} jobs, skip print_varied. '
                'If you want to see them explicitly specify print_varied=1'
            )
            config['print_varied'] = 0

    if 0 and config['print_varied']:
        # Print config info
        longparams = pd.DataFrame(all_param_grid)
        # FIXME: params don't have to be hashable.
        varied = varied_values(longparams, min_variations=2, dropna=False)
        relevant = longparams[longparams.columns.intersection(varied)]

        def pandas_preformat(item):
            if isinstance(item, str):
                return slugify_ext.smart_truncate(
                    item, max_length=16, trunc_loc=0
                )
            else:
                return item

        displayable = util_pandas.compat_applymap(relevant, pandas_preformat)
        rich.print(displayable.to_string())

    # NOTE: a previous version of this code unconditionally reset
    # ``job.log = False`` on every queued job here, with a TODO that
    # said this should be a queue param. The ``--log`` config option
    # plumbed through ``submit_jobs(log=config['log'])`` is that queue
    # param. The forced reset is removed; ``BashJob.log`` now reflects
    # the configured value as set during submission.

    # Report the local root_dpath: it is what compilation was given, and it is
    # meaningful even when the param grid was empty and nothing was compiled.
    if config.run:
        root_dpath.ensuredir()

    print_kwargs = {
        'with_status': 0,
        'style': 'colors',
        'with_locks': 0,
        'exclude_tags': ['boilerplate'],
    }

    rich.print(f'\n\ndag.root_dpath: [link={root_dpath}]{root_dpath}[/link]')
    config.run_queue(queue, print_kwargs=print_kwargs, system=True)

    if not config.run:
        driver_fpath = queue.write()
        print('Wrote script: to run execute:\n{}'.format(driver_fpath))

    return dag, queue


def ensure_iterable(inputs: Any) -> list[Any]:
    return inputs if ub.iterable(inputs) else [inputs]


def _coerce_device(item: str) -> Any:
    """Coerce a single device token to ``int`` when it looks numeric.

    Mirrors the historical smartcast behavior for ``--devices``
    (e.g. ``"0,1"`` -> ``[0, 1]``) while leaving non-numeric ids untouched.
    """
    try:
        return int(item)
    except (TypeError, ValueError):
        return item


def _auto_gpus() -> list[int]:
    from kwdagger.utils.util_nvidia import nvidia_smi

    # TODO: liberate the needed code from netharn
    # Use all unused devices
    gpus: list[int] = []
    gpu_info_by_idx = nvidia_smi()
    for gpu_idx, gpu_info in gpu_info_by_idx.items():
        if len(gpu_info['procs']) == 0:
            gpus.append(gpu_idx)
    return gpus


__cli__ = ScheduleEvaluationConfig


if __name__ == '__main__':
    __cli__.main()
