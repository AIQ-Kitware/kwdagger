#!/usr/bin/env python3
r"""
The simplest backend driven through a kwdagger pipeline: ``backend='serial'``.

Like the tmux example, this calls ``ScheduleEvaluationConfig.main`` directly,
exercising the same code path as ``python -m kwdagger.schedule`` -- only the
backend differs.

The serial backend writes the DAG to a single bash script and runs the jobs
one at a time in topological order, in the current process. No tmux, no
scheduler, nothing to install -- it works anywhere. That makes it the natural
"level 1" of the serial -> tmux -> slurm progression: the same kwdagger
pipeline you define here scales out unchanged to the other backends.

Because everything runs sequentially, the total runtime is the *sum* of every
job's duration, whereas the tmux and slurm backends run independent branches in
parallel. Watching the serial run is a good way to feel why the parallel
backends exist.

The pipeline has four logical levels (identical to the tmux/slurm examples so
the three can be compared directly):

    Level 1 (prep):   prep_a  prep_b  prep_c  prep_d   (independent)
    Level 2 (proc):   proc_a  proc_b  proc_c  proc_d   (each after one prep)
    Level 3 (merge):  merge_x (after proc_a + proc_b)
                      merge_y (after proc_c + proc_d)
    Level 4 (final):  final   (after both merges)

By default one proc job is forced to fail so the failure summary and
dependency-skip cascade are visible.  Pass ``--failures=0`` for a clean run.

The file is also its own executable: the ``sleep_job`` sub-command is what
kwdagger dispatches as each individual queue job.

CommandLine:
    # Run the demo DAG serially
    python ~/code/kwdagger/examples/serial_example.py

    # Clean run (no injected failures)
    python ~/code/kwdagger/examples/serial_example.py --failures=0

    # Print commands only, do not run
    python ~/code/kwdagger/examples/serial_example.py --run=False
"""

from __future__ import annotations

import sys

import kwconf as kw
import ubelt as ub

# ---------------------------------------------------------------------------
# CLI processes — the actual work each job performs
# ---------------------------------------------------------------------------


class SleepJobCLI(kw.Config):
    """
    A self-contained job that sleeps for ``delay`` seconds then either
    writes a small JSON output or exits non-zero.  Dispatched by kwdagger
    as the executable for every ProcessNode in this example.
    """

    __command__ = 'sleep_job'

    label = kw.Value('', type=str, help='human-readable label for log output')
    delay = kw.Value(1, type=float, help='seconds to sleep')
    fail = kw.Value(False, isflag=True, help='if True, exit non-zero')
    out_fpath = kw.Value(None, type=str, help='path to write the output JSON')
    # Optional inputs forwarded by kwdagger; ignored by this CLI.
    in_fpath = kw.Value(None, type=str)
    in_fpath_a = kw.Value(None, type=str)
    in_fpath_b = kw.Value(None, type=str)

    @classmethod
    def main(cls, argv=True, **kwargs):
        import json
        import time

        config = cls.cli(argv=argv, data=kwargs, strict=True)
        label = config.label or 'job'

        # Coerce fail robustly — ProcessNode may pass --fail=False as a string.
        fail = config.fail
        if isinstance(fail, str):
            fail = fail.strip().lower() not in ('false', '0', 'no', '')
        else:
            fail = bool(fail)

        print(f'[{label}] start (delay={config.delay}s)')
        time.sleep(float(config.delay))

        if fail:
            print(f'[{label}] FORCED FAILURE', file=sys.stderr)
            sys.exit(1)

        out_fpath = ub.Path(config.out_fpath)
        out_fpath.parent.mkdir(parents=True, exist_ok=True)
        out_fpath.write_text(json.dumps({'label': label, 'status': 'done'}))
        print(f'[{label}] done  out={out_fpath}')


class SerialExampleModalCLI(kw.ModalCLI):
    """Modal CLI that wraps the job sub-commands defined in this file."""

    sleep_job = SleepJobCLI


__cli__ = SerialExampleModalCLI


# ---------------------------------------------------------------------------
# Pipeline definition — ProcessNode subclasses
# ---------------------------------------------------------------------------

from kwdagger.pipeline import Pipeline, ProcessNode  # noqa: E402

_THIS_FILE = str(ub.Path(__file__).absolute())
_EXECUTABLE = f'python {_THIS_FILE} sleep_job'


class _SleepNode(ProcessNode):
    """Base class: runs SleepJobCLI and writes a single JSON output."""

    executable = _EXECUTABLE
    algo_params = {'label': '?', 'delay': 1, 'fail': False}
    perf_params = {}
    out_paths = {'out_fpath': 'output.json'}
    primary_out_key = 'out_fpath'


# ── Level 1: prep nodes ─────────────────────────────────────────────────────


class PrepA(_SleepNode):
    name = 'prep_a'
    algo_params = {'label': 'prep-A', 'delay': 5, 'fail': False}


class PrepB(_SleepNode):
    name = 'prep_b'
    algo_params = {'label': 'prep-B', 'delay': 7, 'fail': False}


class PrepC(_SleepNode):
    name = 'prep_c'
    algo_params = {'label': 'prep-C', 'delay': 6, 'fail': False}


class PrepD(_SleepNode):
    name = 'prep_d'
    algo_params = {'label': 'prep-D', 'delay': 8, 'fail': False}


# ── Level 2: proc nodes (one input each, may be forced to fail) ─────────────


class ProcA(_SleepNode):
    name = 'proc_a'
    algo_params = {'label': 'proc-A', 'delay': 3, 'fail': False}
    in_paths = {'in_fpath'}


class ProcB(_SleepNode):
    name = 'proc_b'
    algo_params = {'label': 'proc-B', 'delay': 4, 'fail': False}
    in_paths = {'in_fpath'}


class ProcC(_SleepNode):
    name = 'proc_c'
    algo_params = {'label': 'proc-C', 'delay': 5, 'fail': False}
    in_paths = {'in_fpath'}


class ProcD(_SleepNode):
    name = 'proc_d'
    algo_params = {'label': 'proc-D', 'delay': 3, 'fail': False}
    in_paths = {'in_fpath'}


# ── Level 3: merge nodes (two inputs each) ───────────────────────────────────


class MergeX(_SleepNode):
    name = 'merge_x'
    algo_params = {'label': 'merge-X', 'delay': 4, 'fail': False}
    in_paths = {'in_fpath_a', 'in_fpath_b'}


class MergeY(_SleepNode):
    name = 'merge_y'
    algo_params = {'label': 'merge-Y', 'delay': 3, 'fail': False}
    in_paths = {'in_fpath_a', 'in_fpath_b'}


# ── Level 4: final node ──────────────────────────────────────────────────────


class Final(_SleepNode):
    name = 'final'
    algo_params = {'label': 'final', 'delay': 2, 'fail': False}
    in_paths = {'in_fpath_a', 'in_fpath_b'}


def make_pipeline() -> Pipeline:
    """
    Build the four-level DAG pipeline.

    Example:
        >>> from examples.serial_example import make_pipeline
        >>> dag = make_pipeline()
        >>> dag.print_graphs()
    """
    nodes = {
        'prep_a': PrepA(),
        'prep_b': PrepB(),
        'prep_c': PrepC(),
        'prep_d': PrepD(),
        'proc_a': ProcA(),
        'proc_b': ProcB(),
        'proc_c': ProcC(),
        'proc_d': ProcD(),
        'merge_x': MergeX(),
        'merge_y': MergeY(),
        'final': Final(),
    }

    # Wire prep → proc
    nodes['prep_a'].outputs['out_fpath'].connect(
        nodes['proc_a'].inputs['in_fpath']
    )
    nodes['prep_b'].outputs['out_fpath'].connect(
        nodes['proc_b'].inputs['in_fpath']
    )
    nodes['prep_c'].outputs['out_fpath'].connect(
        nodes['proc_c'].inputs['in_fpath']
    )
    nodes['prep_d'].outputs['out_fpath'].connect(
        nodes['proc_d'].inputs['in_fpath']
    )

    # Wire proc → merge
    nodes['proc_a'].outputs['out_fpath'].connect(
        nodes['merge_x'].inputs['in_fpath_a']
    )
    nodes['proc_b'].outputs['out_fpath'].connect(
        nodes['merge_x'].inputs['in_fpath_b']
    )
    nodes['proc_c'].outputs['out_fpath'].connect(
        nodes['merge_y'].inputs['in_fpath_a']
    )
    nodes['proc_d'].outputs['out_fpath'].connect(
        nodes['merge_y'].inputs['in_fpath_b']
    )

    # Wire merge → final
    nodes['merge_x'].outputs['out_fpath'].connect(
        nodes['final'].inputs['in_fpath_a']
    )
    nodes['merge_y'].outputs['out_fpath'].connect(
        nodes['final'].inputs['in_fpath_b']
    )

    dag = Pipeline(list(nodes.values()))
    dag.build_nx_graphs()
    return dag


# ---------------------------------------------------------------------------
# Main example config and entry point
# ---------------------------------------------------------------------------


class SerialExampleConfig(kw.Config):
    """
    Run the kwdagger serial-backend example.

    Calls ``ScheduleEvaluationConfig.main`` directly with ``backend='serial'``
    to exercise the standard kwdagger scheduling path with no scheduler or
    tmux involved.
    """

    failures = kw.Value(
        1,
        type=int,
        help='number of proc-* nodes to force into failure (0-4)',
    )
    root_dpath = kw.Value(
        None,
        help='output root directory (defaults to a per-user app-cache location)',
    )
    run = kw.Value(
        True,
        isflag=True,
        help='if False, only print commands without executing them',
    )
    logs = kw.Value(True, isflag=True, help='enable per-job log capture')

    @staticmethod
    def main(argv=True, **kwargs):
        import json

        from kwdagger.schedule import ScheduleEvaluationConfig

        config = SerialExampleConfig.cli(argv=argv, data=kwargs, strict=True)

        root_dpath = ub.Path(
            config.root_dpath or ub.Path.appdir('kwdagger/serial-example')
        ).ensuredir()

        # Reference the pipeline function defined in this file.
        pipeline_ref = f'{ub.Path(__file__).absolute()}::make_pipeline()'

        # Build a single-row matrix that sets the fail flag for the chosen nodes.
        proc_names = ['proc_a', 'proc_b', 'proc_c', 'proc_d']
        fail_names = set(proc_names[: max(0, min(int(config.failures), 4))])
        matrix = {f'{n}.fail': [n in fail_names] for n in proc_names}

        print(
            f'\nRunning serially: failures={config.failures}, '
            f'logs={config.logs}\n'
        )

        ScheduleEvaluationConfig.main(
            argv=False,
            pipeline=pipeline_ref,
            params=json.dumps({'matrix': matrix}),
            root_dpath=str(root_dpath),
            backend='serial',
            monitor='inline',
            run=bool(config.run),
            log=bool(config.logs),
            skip_existing=False,
        )


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/kwdagger/examples/serial_example.py
    """
    # This file is both the example orchestrator and the executable that
    # kwdagger dispatches for each job. Route the ``sleep_job`` sub-command
    # to the job CLI; otherwise run the orchestrator.
    if len(sys.argv) > 1 and sys.argv[1] == 'sleep_job':
        SerialExampleModalCLI.main()
    else:
        SerialExampleConfig.main()
