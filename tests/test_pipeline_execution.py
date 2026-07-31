"""
End-to-end execution tests for kwdagger pipelines on each cmd_queue backend.

These build a tiny ``Pipeline``, submit it to a real queue, and **run** it,
verifying that the node command -- and the ``ProcessNode`` ``setup`` /
``teardown`` resource lifecycle that kwdagger forwards to cmd_queue --
actually executed.

* ``serial`` always runs.
* ``tmux`` and ``slurm`` are skipped unless the backend is available
  (``is_available()``), so a machine with a working slurm install exercises
  real ``sbatch`` submission and everywhere else skips cleanly.

Working directories live under ``ubelt.Path.appdir`` (``$HOME``) rather than
pytest's ``tmp_path`` so the marker files written by a slurm job on a compute
node are readable back by the submitting process (``$HOME`` is typically
shared across cluster nodes; ``/tmp`` often is not).

The setup/teardown forwarding boundary (ProcessNode -> submit_jobs ->
BashJob) is render-tested in ``test_submit_jobs_setup_teardown.py``; this
module checks the *runtime* behavior, and requires a cmd_queue new enough to
support setup/teardown (>= 0.3.1) -- skipped otherwise.
"""

from __future__ import annotations

import inspect

import cmd_queue
import pytest
import ubelt as ub

from kwdagger.pipeline import Pipeline
from kwdagger.yaml_pipeline import load_yaml_pipeline

# Feature-detect setup/teardown support in the installed cmd_queue.
from cmd_queue.serial_queue import BashJob

_HAS_SETUP_TEARDOWN = (
    'teardown' in inspect.signature(BashJob.__init__).parameters
)

# Computed once: which backends can actually run on this machine.
_AVAILABLE = set(cmd_queue.Queue.available_backends())


def _backend_param(name: str):
    return pytest.param(
        name,
        marks=pytest.mark.skipif(
            name not in _AVAILABLE,
            reason=f'{name} backend is not available on this machine',
        ),
    )


BACKENDS = [
    _backend_param('serial'),
    _backend_param('tmux'),
    _backend_param('slurm'),
]

_needs_setup_teardown = pytest.mark.skipif(
    not _HAS_SETUP_TEARDOWN,
    reason='requires cmd_queue with BashJob setup/teardown support (>= 0.3.1)',
)


def _work_dpath(slug: str) -> ub.Path:
    """A clean, shared-filesystem working directory for one test."""
    dpath = ub.Path.appdir('kwdagger/tests/pipeline_execution') / slug
    dpath.delete().ensuredir()
    return dpath


def _make_queue(backend: str, name: str, dpath: ub.Path):
    """Construct a queue with the per-backend kwargs each one expects."""
    kwargs: dict = {'backend': backend, 'name': name}
    if backend in {'serial', 'tmux'}:
        kwargs['dpath'] = dpath
        kwargs['rootid'] = 'test'
    if backend == 'tmux':
        kwargs['size'] = 1
    return cmd_queue.Queue.create(**kwargs)


def _run_blocking(queue, backend: str) -> None:
    """Run the queue and block until every job reaches a terminal state."""
    try:
        if backend == 'tmux':
            queue.run(
                block=True,
                monitor='none',
                with_textual=False,
                onfail='',
                other_session_handler='ignore',
            )
        elif backend == 'slurm':
            queue.run(block=True, monitor='inline', onfail='')
        else:
            queue.run(block=True, verbose=0)
    finally:
        kill = getattr(queue, 'kill', None)
        if callable(kill):
            try:
                kill()
            except Exception:
                pass


def _run_pipeline(dag: Pipeline, backend: str, dpath: ub.Path):
    dag.configure(config={}, root_dpath=dpath / 'root', cache=False)
    queue = _make_queue(backend, 'kwd-exec', dpath / 'q')
    dag.submit_jobs(queue=queue, log=False)
    _run_blocking(queue, backend)
    return queue


def _assert_real_jobs_failed(queue, backend: str) -> None:
    """Assert every real (non-bookkeeper) job was marked failed.

    serial/tmux record pass/fail as on-disk markers; slurm tracks job state
    through the scheduler (no fail marker), so the marker check is limited to
    the file-based backends -- slurm's "setup failure exits non-zero" is
    covered by cmd_queue's own slurm render/exec tests.
    """
    if backend == 'slurm':
        return
    real_jobs = [j for j in queue.jobs if not getattr(j, 'bookkeeper', 0)]
    assert real_jobs, 'no real jobs were submitted'
    for job in real_jobs:
        assert job.fail_fpath.exists(), 'a failing setup must fail the job'
        assert not job.pass_fpath.exists(), 'a failed job must not pass'


@pytest.mark.parametrize('backend', BACKENDS)
def test_pipeline_executes_simple(backend):
    """A single-node pipeline runs to completion and produces its output."""
    dpath = _work_dpath(f'simple-{backend}')
    out = dpath / 'node.out'

    dag = Pipeline()
    dag.submit(f'echo hi > "{out}"', name='node1')

    _run_pipeline(dag, backend, dpath)

    assert out.exists(), 'the node command did not run'
    assert out.read_text().strip() == 'hi'


@_needs_setup_teardown
@pytest.mark.parametrize('backend', BACKENDS)
def test_pipeline_executes_setup_teardown(backend):
    """A node's setup runs before its command and teardown runs after."""
    dpath = _work_dpath(f'setup-teardown-{backend}')
    setup_marker = dpath / 'setup.marker'
    cmd_marker = dpath / 'cmd.marker'
    teardown_marker = dpath / 'teardown.marker'

    dag = Pipeline()
    dag.submit(
        f'echo cmd > "{cmd_marker}"',
        name='bracketed',
        setup=f'echo s > "{setup_marker}"',
        teardown=f'echo t > "{teardown_marker}"',
    )

    _run_pipeline(dag, backend, dpath)

    assert setup_marker.exists(), 'setup should run before the command'
    assert cmd_marker.exists(), 'command should run after a successful setup'
    assert teardown_marker.exists(), 'teardown should run after the command'


@_needs_setup_teardown
@pytest.mark.parametrize('backend', BACKENDS)
def test_yaml_pipeline_executes_setup_teardown(backend):
    """A small *declarative YAML* pipeline carrying ``setup``/``teardown`` runs
    the lifecycle end to end (YAML spec -> load_yaml_pipeline -> run)."""
    dpath = _work_dpath(f'yaml-setup-teardown-{backend}')
    setup_marker = dpath / 'setup.marker'
    cmd_marker = dpath / 'cmd.marker'
    teardown_marker = dpath / 'teardown.marker'

    spec = {
        'nodes': {
            'bracketed': {
                'executable': f'echo cmd > "{cmd_marker}"',
                'setup': f'echo s > "{setup_marker}"',
                'teardown': f'echo t > "{teardown_marker}"',
            },
        },
    }
    dag = load_yaml_pipeline(spec)

    _run_pipeline(dag, backend, dpath)

    assert setup_marker.exists(), 'setup should run before the command'
    assert cmd_marker.exists(), 'command should run after a successful setup'
    assert teardown_marker.exists(), 'teardown should run after the command'


@_needs_setup_teardown
@pytest.mark.parametrize('backend', BACKENDS)
def test_yaml_pipeline_setup_failure_fails_job_and_skips_command(backend):
    """In a YAML pipeline, a failing setup marks the job failed, and gates the
    command (skipped) and teardown (not run)."""
    dpath = _work_dpath(f'yaml-setup-fail-{backend}')
    cmd_marker = dpath / 'cmd.marker'
    teardown_marker = dpath / 'teardown.marker'

    spec = {
        'nodes': {
            'bracketed': {
                'executable': f'echo cmd > "{cmd_marker}"',
                'setup': 'false',  # gating precondition fails
                'teardown': f'echo t > "{teardown_marker}"',
            },
        },
    }
    dag = load_yaml_pipeline(spec)

    queue = _run_pipeline(dag, backend, dpath)

    _assert_real_jobs_failed(queue, backend)
    assert not cmd_marker.exists(), 'command must be skipped when setup fails'
    assert not teardown_marker.exists(), (
        'teardown must not run when setup never succeeded'
    )
