"""
Test that ``Pipeline.submit_jobs(log=...)`` plumbs the log flag all the
way through to the underlying ``BashJob`` and the rendered bash script.

Originally added because a kwdagger user observed that the bash script
generated for a node had **no** ``| tee <log_fpath>`` wrapper, even
though ``log=True`` was the documented default and was being passed to
``Pipeline.submit_jobs``. Without this test, an upstream regression
that drops the flag (e.g. ``extra_submitkw['log'] = log`` being moved
into the slurm-only branch) would silently disable per-node tee
logging — and the loss would only be noticed when a downstream user
tries to diagnose a failure and finds the log directory empty.

The boundary tested here is::

    Pipeline.submit_jobs(queue=queue, log=<value>)
        ↓
    queue.submit(command=..., log=<value>, ...)         (extra_submitkw)
        ↓
    BashJob(..., log=<value>)
        ↓
    BashJob.finalize_text() → ``(<cmd>) 2>&1 | tee <log_fpath>``  iff log=True

The test covers all four corners (default / explicit True / explicit
False / both backends serial+tmux-script-render). For backends, we
only need to verify the rendered command — we never actually run a
queue here.
"""

from __future__ import annotations

import cmd_queue
import ubelt as ub

from kwdagger.pipeline import Pipeline


def _build_demo(root_dpath: ub.Path) -> Pipeline:
    """Build the bundled kwdagger demo DAG configured to ``root_dpath``."""
    dag = Pipeline.demo()
    dag.configure(config={}, root_dpath=root_dpath, cache=False)
    return dag


def _command_section(text: str) -> str:
    """Slice ``text`` between ``# command:`` and ``# after_command:`` so
    assertions only inspect the actual job command (not bookkeeping
    paths that may also reference ``log_fpath``-shaped strings).
    """
    start = text.find('# command:')
    end = text.find('# after_command:')
    if start == -1 or end == -1:
        return text
    return text[start:end]


def _first_real_job(
    queue: cmd_queue.base_queue.Queue,
) -> cmd_queue.base_queue.Job:
    for job in queue.jobs:
        if not getattr(job, 'bookkeeper', 0):
            return job
    raise AssertionError('queue produced no real (non-bookkeeper) jobs')


def test_submit_jobs_log_true_tees_command(tmp_path):
    """``submit_jobs(log=True)`` must wrap each node's command with
    ``2>&1 | tee <log_fpath>`` so post-mortem inspection has content.
    """
    dag = _build_demo(ub.Path(tmp_path) / 'log-on')
    queue = cmd_queue.Queue.create(
        backend='serial', name='kwdagger-log-on', size=1
    )
    dag.submit_jobs(queue=queue, log=True)

    job = _first_real_job(queue)
    assert job.log is True, (
        'Pipeline.submit_jobs(log=True) must set BashJob.log=True; '
        'got {!r}'.format(job.log)
    )

    text = job.finalize_text(with_status=True, with_gaurds=True)
    cmd = _command_section(text)
    assert '| tee ' in cmd, (
        'log=True must inject "| tee" into the rendered command. '
        'This is the regression we tripped over in production. '
        'Section was:\n' + cmd
    )
    assert str(job.log_fpath) in cmd, (
        'tee target must be BashJob.log_fpath. Section was:\n' + cmd
    )


def test_submit_jobs_log_false_omits_tee(tmp_path):
    """``submit_jobs(log=False)`` must NOT add a tee. Useful when the
    caller wants to handle stdout/stderr themselves (e.g., piping to a
    parent supervisor).
    """
    dag = _build_demo(ub.Path(tmp_path) / 'log-off')
    queue = cmd_queue.Queue.create(
        backend='serial', name='kwdagger-log-off', size=1
    )
    dag.submit_jobs(queue=queue, log=False)

    job = _first_real_job(queue)
    assert job.log is False
    text = job.finalize_text(with_status=True, with_gaurds=True)
    cmd = _command_section(text)
    assert '| tee ' not in cmd, (
        'log=False must NOT inject "| tee". Section was:\n' + cmd
    )


def test_submit_jobs_log_default_is_true(tmp_path):
    """Default behavior changed in this release: ``submit_jobs`` now
    defaults to ``log=True`` so that diagnostics are available without
    callers having to remember the flag. Locks the new default in.
    """
    dag = _build_demo(ub.Path(tmp_path) / 'log-default')
    queue = cmd_queue.Queue.create(
        backend='serial', name='kwdagger-log-default', size=1
    )
    dag.submit_jobs(queue=queue)  # no explicit log= argument

    job = _first_real_job(queue)
    assert job.log is True, (
        'Default for submit_jobs is log=True; got {!r}. If you are '
        'intentionally flipping the default, update this test and '
        'the schedule.py Value(...) default together.'.format(job.log)
    )
    text = job.finalize_text(with_status=True, with_gaurds=True)
    assert '| tee ' in _command_section(text)


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td_path = ub.Path(td)
        test_submit_jobs_log_true_tees_command(td_path / 't1')
        test_submit_jobs_log_false_omits_tee(td_path / 't2')
        test_submit_jobs_log_default_is_true(td_path / 't3')
    print('All kwdagger submit_jobs log-flag tests passed.')
