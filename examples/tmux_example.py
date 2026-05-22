#!/usr/bin/env python3
r"""
Demonstrates the ``monitor`` kwarg on the tmux backend through a kwdagger
pipeline, mirroring the ``cmd_queue/examples/tmux_example.py`` use-case.

Four monitor modes are illustrated:

    * ``monitor='hybrid'`` (default) — the live status table renders in
      the current shell *and* a detached ``cmd_queue monitor`` tmux
      session is spawned alongside. Press ``[a]`` from the inline UI to
      attach, ``[q]`` to stop watching.

    * ``monitor='inline'`` — only the in-shell live UI; no tmux session
      is spawned.

    * ``monitor='tmux'`` — only the detached tmux session, no inline UI.
      Useful when you want the visible status table to survive the
      calling shell closing.

    * ``monitor='none'`` — no live UI; ``run()`` headless-blocks until
      jobs finish. Useful in non-interactive scripts. The reattach hint
      is still printed so a human can reattach via ``cmd_queue monitor``.

The kwdagger pipeline has four logical levels:

    Level 1 (prep):    prep_a  prep_b  prep_c  prep_d   (parallel, 5-8s)
    Level 2 (proc):    proc_a  proc_b  proc_c  proc_d   (each after one prep, 3-5s)
    Level 3 (merge):   merge_x (after proc_a + proc_b)
                       merge_y (after proc_c + proc_d)  (parallel, 3-4s)
    Level 4 (final):   final   (after both merges, 2s)

By default proc_a is forced to fail so the failure summary (and
dependency-skip cascade) is visible. Pass ``--failures=0`` for a clean
run.

The file is also the executable itself — the ``SleepJobCLI`` sub-command
is what kwdagger dispatches as each individual job.

CommandLine:
    # Default (hybrid): inline monitor + attachable tmux session
    python ~/code/kwdagger/examples/tmux_example.py

    # Inline-only, no side tmux session
    python ~/code/kwdagger/examples/tmux_example.py --monitor=inline

    # Spawn the monitor only in a tmux session (no inline view)
    python ~/code/kwdagger/examples/tmux_example.py --monitor=tmux

    # Run silently, reattach manually with `cmd_queue monitor <name>`
    python ~/code/kwdagger/examples/tmux_example.py --monitor=none

    # Force a clean run (no injected failures)
    python ~/code/kwdagger/examples/tmux_example.py --failures=0

    # Print commands only, do not run
    python ~/code/kwdagger/examples/tmux_example.py --run=False
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
    A self-contained job that sleeps for ``delay`` seconds, then either
    writes a small JSON output or exits with failure.

    This is dispatched by kwdagger as the executable for every ProcessNode
    in this example.
    """

    __command__ = 'sleep_job'

    label = scfg.Value('', type=str, help='human-readable label for log output')
    delay = scfg.Value(1, type=float, help='seconds to sleep')
    fail = scfg.Value(False, isflag=True, help='if True, exit non-zero')
    out_fpath = scfg.Value(None, type=str, help='path to write the output JSON')
    # Optional inputs forwarded by kwdagger; not used by this CLI.
    in_fpath = scfg.Value(None, type=str)
    in_fpath_a = scfg.Value(None, type=str)
    in_fpath_b = scfg.Value(None, type=str)

    @classmethod
    def main(cls, argv=1, **kwargs):
        import json
        import time

        config = cls.cli(argv=argv, data=kwargs, strict=True)
        label = config.label or 'job'

        # Coerce fail robustly — scriptconfig + ProcessNode may produce a
        # string like "False" when the value is passed through --fail=False.
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


class TmuxExampleModalCLI(scfg.ModalCLI):
    """Modal CLI that wraps the job sub-commands defined in this file."""

    sleep_job = SleepJobCLI


__cli__ = TmuxExampleModalCLI


# ---------------------------------------------------------------------------
# Pipeline definition — ProcessNode subclasses
# ---------------------------------------------------------------------------

from kwdagger.pipeline import Pipeline, ProcessNode  # noqa: E402

_THIS_FILE = str(ub.Path(__file__).absolute())
_EXECUTABLE = f'python {_THIS_FILE} sleep_job'


class _SleepNode(ProcessNode):
    """Base class: runs SleepJobCLI, writes a single JSON output."""

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
        >>> from kwdagger.examples.tmux_example import make_pipeline
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

    # Wire prep → proc (each proc depends on exactly one prep)
    nodes['prep_a'].outputs['out_fpath'].connect(nodes['proc_a'].inputs['in_fpath'])
    nodes['prep_b'].outputs['out_fpath'].connect(nodes['proc_b'].inputs['in_fpath'])
    nodes['prep_c'].outputs['out_fpath'].connect(nodes['proc_c'].inputs['in_fpath'])
    nodes['prep_d'].outputs['out_fpath'].connect(nodes['proc_d'].inputs['in_fpath'])

    # Wire proc → merge (each merge waits on two proc nodes)
    nodes['proc_a'].outputs['out_fpath'].connect(nodes['merge_x'].inputs['in_fpath_a'])
    nodes['proc_b'].outputs['out_fpath'].connect(nodes['merge_x'].inputs['in_fpath_b'])
    nodes['proc_c'].outputs['out_fpath'].connect(nodes['merge_y'].inputs['in_fpath_a'])
    nodes['proc_d'].outputs['out_fpath'].connect(nodes['merge_y'].inputs['in_fpath_b'])

    # Wire merge → final (the whole pipeline converges here)
    nodes['merge_x'].outputs['out_fpath'].connect(nodes['final'].inputs['in_fpath_a'])
    nodes['merge_y'].outputs['out_fpath'].connect(nodes['final'].inputs['in_fpath_b'])

    dag = Pipeline(nodes)
    dag.build_nx_graphs()
    return dag


# ---------------------------------------------------------------------------
# Main example config and entry point
# ---------------------------------------------------------------------------

from cmd_queue.cli_boilerplate import CMDQueueConfig  # noqa: E402


class TmuxExampleConfig(CMDQueueConfig):
    """
    Run the kwdagger tmux-monitor example.

    Uses the tmux backend with a four-level pipeline DAG. The ``--monitor``
    flag controls which of the four monitor UIs is shown while jobs run.
    Pass ``--failures=0`` for a fully-green run.
    """

    # Override run to default True so the example actually runs out of the box.
    run = scfg.Value(
        True,
        isflag=True,
        help='if False, only print commands; if True, execute them',
        group='cmd-queue',
    )

    # Override backend default to tmux for this example.
    backend = scfg.Value('tmux', help='queue backend', group='cmd-queue')

    # Override monitor to expose all four modes (CMDQueueConfig only has 2).
    monitor = scfg.Value(
        'hybrid',
        choices=['hybrid', 'inline', 'tmux', 'none'],
        help=(
            "Where the live status UI runs. "
            "'hybrid' = inline + attachable tmux session; "
            "'inline' = inline only; "
            "'tmux' = detached tmux session only; "
            "'none' = headless (reattach hint still printed)."
        ),
        group='cmd-queue',
    )

    # Override other_session_handler to auto for convenience.
    other_session_handler = scfg.Value(
        'auto',
        help='how to handle conflicting tmux sessions',
        group='cmd-queue',
    )

    name = scfg.Value(
        'kwdagger-tmux-example',
        help='queue name; also the lookup key for `cmd_queue monitor <name>`',
    )
    workers = scfg.Value(4, type=int, help='number of parallel tmux workers')
    failures = scfg.Value(
        1,
        type=int,
        help=ub.paragraph(
            """
            Number of proc-* nodes to force into failure (0-4). Failures
            cascade: dependent merge/final jobs are skipped.
            """
        ),
    )
    root_dpath = scfg.Value(
        None,
        help='output root directory. Defaults to a per-user app-dir temp location.',
    )
    logs = scfg.Value(
        True,
        isflag=True,
        help='enable per-job log capture (pass log=True to submit_jobs)',
    )

    def __post_init__(self):
        super().__post_init__()
        if self.queue_name is None:
            self.queue_name = self.name
        self.tmux_workers = self.workers

    @staticmethod
    def main(argv=True, **kwargs):
        config = TmuxExampleConfig.cli(argv=argv, data=kwargs, strict=True)

        root_dpath = config.root_dpath
        if root_dpath is None:
            root_dpath = ub.Path.appdir('kwdagger/tmux-example').ensuredir()
        root_dpath = ub.Path(root_dpath).ensuredir()

        dag = make_pipeline()
        dag.print_graphs()

        # Determine which proc nodes should fail.
        proc_names = ['proc_a', 'proc_b', 'proc_c', 'proc_d']
        fail_names = set(proc_names[: max(0, min(int(config.failures), 4))])
        overrides = {f'{n}.fail': True for n in fail_names}

        dag.configure(config=overrides, root_dpath=root_dpath, cache=True)

        queue = config.create_queue()

        print(
            f'\nLaunching with monitor={config.monitor!r}, '
            f'workers={config.workers}, '
            f'failures={config.failures}, '
            f'logs={config.logs}\n'
        )

        if config.run and not queue.is_available():
            raise SystemExit('tmux backend not available on this machine')

        dag.submit_jobs(queue, log=bool(config.logs))

        print_kwargs = {
            'style': 'colors',
            'with_locks': 0,
            'with_status': 0,
            'exclude_tags': ['boilerplate'],
        }
        config.run_queue(
            queue,
            print_kwargs=print_kwargs,
            block=True,
            onfail='kill',
            system=True,
        )


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/kwdagger/examples/tmux_example.py
    """
    TmuxExampleConfig.main()
