#!/usr/bin/env python3
r"""
Submitting a kwdagger pipeline to a real slurm scheduler (``backend='slurm'``).

Like the tmux and serial examples, this calls ``ScheduleEvaluationConfig.main``
directly, exercising the same code path as ``python -m kwdagger.schedule`` --
only the backend and a few scheduler-specific options differ.

This is the "level 3" backend: kwdagger converts the job DAG into ``sbatch``
submissions (with ``--dependency`` edges) and lets slurm schedule them across
the cluster. The same pipeline definition used by the serial and tmux examples
is reused here unchanged.

Partition / account
-------------------
On a shared cluster you typically must route jobs to a specific partition and
bill them to an account, e.g.::

    python ~/code/kwdagger/examples/slurm_example.py \
        --partition=general --account=my_project

When ``--partition``/``--account`` are omitted (the default) the options are
left off the ``sbatch`` command, so slurm uses the cluster's default partition
and no accounting. That is what lets this example run as-is on a vanilla
single-node slurm install (which usually exposes a default ``debug``
partition).

Monitoring
----------
Like the tmux example, ``--monitor`` selects where the live status UI runs:

    * ``hybrid`` (default) — inline table in this shell *and* an attachable
      detached tmux monitor session.
    * ``inline``           — in-shell live UI only.
    * ``tmux``             — detached tmux monitor session only.
    * ``none``             — headless; ``run()`` blocks until jobs finish.

The pipeline has four logical levels (identical to the serial/tmux examples so
the three can be compared directly):

    Level 1 (prep):   prep_a  prep_b  prep_c  prep_d   (parallel)
    Level 2 (proc):   proc_a  proc_b  proc_c  proc_d   (each after one prep)
    Level 3 (merge):  merge_x (after proc_a + proc_b)
                      merge_y (after proc_c + proc_d)
    Level 4 (final):  final   (after both merges)

By default one proc job is forced to fail so the failure summary and
dependency-skip cascade are visible.  Pass ``--failures=0`` for a clean run.

The file is also its own executable: the ``sleep_job`` sub-command is what
kwdagger dispatches as each individual queue job.

CommandLine:
    # Run on the cluster's default partition (works on a local install)
    python ~/code/kwdagger/examples/slurm_example.py

    # Target a specific partition / account on a shared cluster
    python ~/code/kwdagger/examples/slurm_example.py \
        --partition=general --account=my_project

    # Clean run (no injected failures)
    python ~/code/kwdagger/examples/slurm_example.py --failures=0

    # Print the sbatch commands without submitting anything
    python ~/code/kwdagger/examples/slurm_example.py --run=False
"""

from __future__ import annotations

import sys

import scriptconfig as scfg
import ubelt as ub

# ---------------------------------------------------------------------------
# CLI processes — the actual work each job performs
# ---------------------------------------------------------------------------


class SleepJobCLI(scfg.DataConfig):
    """
    A self-contained job that sleeps for ``delay`` seconds then either
    writes a small JSON output or exits non-zero.  Dispatched by kwdagger
    as the executable for every ProcessNode in this example.
    """

    __command__ = 'sleep_job'

    label = scfg.Value('', type=str, help='human-readable label for log output')
    delay = scfg.Value(1, type=float, help='seconds to sleep')
    fail = scfg.Value(False, isflag=True, help='if True, exit non-zero')
    out_fpath = scfg.Value(None, type=str, help='path to write the output JSON')
    # Optional inputs forwarded by kwdagger; ignored by this CLI.
    in_fpath = scfg.Value(None, type=str)
    in_fpath_a = scfg.Value(None, type=str)
    in_fpath_b = scfg.Value(None, type=str)

    @classmethod
    def main(cls, argv=1, **kwargs):
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


class SlurmExampleModalCLI(scfg.ModalCLI):
    """Modal CLI that wraps the job sub-commands defined in this file."""

    sleep_job = SleepJobCLI


__cli__ = SlurmExampleModalCLI


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
        >>> from examples.slurm_example import make_pipeline
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


class SlurmExampleConfig(scfg.DataConfig):
    """
    Run the kwdagger slurm-backend example.

    Calls ``ScheduleEvaluationConfig.main`` directly with ``backend='slurm'``
    so the DAG is submitted to the cluster via ``sbatch`` with dependency
    edges.
    """

    monitor = scfg.Value(
        'hybrid',
        type=str,
        choices=['hybrid', 'inline', 'tmux', 'none'],
        help='monitor mode passed through to cmd_queue',
    )
    partition = scfg.Value(
        None,
        help=ub.paragraph(
            """
            Slurm partition to submit to. If unset, the sbatch --partition
            option is omitted and the cluster's default partition is used.
            """
        ),
    )
    account = scfg.Value(
        None,
        help=ub.paragraph(
            """
            Slurm account to bill jobs to. If unset, the sbatch --account
            option is omitted.
            """
        ),
    )
    failures = scfg.Value(
        1,
        type=int,
        help='number of proc-* nodes to force into failure (0-4)',
    )
    root_dpath = scfg.Value(
        None,
        help='output root directory (defaults to a per-user app-cache location)',
    )
    run = scfg.Value(
        True,
        isflag=True,
        help='if False, only print the sbatch commands without submitting them',
    )
    logs = scfg.Value(True, isflag=True, help='enable per-job log capture')

    @staticmethod
    def main(argv=True, **kwargs):
        import json

        from kwdagger.schedule import ScheduleEvaluationConfig

        config = SlurmExampleConfig.cli(argv=argv, data=kwargs, strict=True)

        root_dpath = ub.Path(
            config.root_dpath or ub.Path.appdir('kwdagger/slurm-example')
        ).ensuredir()

        # Reference the pipeline function defined in this file.
        pipeline_ref = f'{ub.Path(__file__).absolute()}::make_pipeline()'

        # Build a single-row matrix that sets the fail flag for the chosen nodes.
        proc_names = ['proc_a', 'proc_b', 'proc_c', 'proc_d']
        fail_names = set(proc_names[: max(0, min(int(config.failures), 4))])
        matrix = {f'{n}.fail': [n in fail_names] for n in proc_names}

        # Only pass partition/account through to sbatch when the user actually
        # specified them; otherwise let slurm use its defaults.
        slurm_options = {}
        if config.partition is not None:
            slurm_options['partition'] = config.partition
        if config.account is not None:
            slurm_options['account'] = config.account

        print(
            f'\nSubmitting with monitor={config.monitor!r}, '
            f'partition={config.partition!r}, account={config.account!r}, '
            f'failures={config.failures}\n'
        )

        ScheduleEvaluationConfig.main(
            argv=False,
            pipeline=pipeline_ref,
            params=json.dumps({'matrix': matrix}),
            root_dpath=str(root_dpath),
            backend='slurm',
            slurm_options=slurm_options or None,
            monitor=config.monitor,
            run=bool(config.run),
            log=bool(config.logs),
            skip_existing=False,
        )


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/kwdagger/examples/slurm_example.py
    """
    # This file is both the example orchestrator and the executable that
    # kwdagger dispatches for each job. Route the ``sleep_job`` sub-command
    # to the job CLI; otherwise run the orchestrator.
    if len(sys.argv) > 1 and sys.argv[1] == 'sleep_job':
        SlurmExampleModalCLI.main()
    else:
        SlurmExampleConfig.main()
