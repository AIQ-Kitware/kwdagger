"""
Test that ``ProcessNode.setup`` / ``ProcessNode.teardown`` are plumbed
through ``Pipeline.submit_jobs`` into the underlying cmd_queue job and
rendered correctly.

This is the kwdagger half of the first-class resource-lifecycle feature
(the cmd_queue half lives in ``cmd_queue.backends.serial.BashJob`` /
``SlurmJob``). The intended use is bracketing a job with an external
resource -- e.g. acquiring a GPU lease before a HELM run and releasing
it after, even on failure or signal -- without modeling acquire/release
as separate, skippable DAG nodes.

The boundary tested here is::

    ProcessNode(setup=..., teardown=...)
        ↓
    Pipeline.submit_jobs(queue=queue)
        ↓
    queue.submit(command=..., setup=..., teardown=..., ...)   (extra_submitkw)
        ↓
    BashJob(..., setup=..., teardown=...)
        ↓
    BashJob.finalize_text():
        setup  → ``{ <setup> && PREAMBLE_OK=1; } || PREAMBLE_OK=0``  (gates command)
        teardown → ``trap __cmdq_teardown EXIT`` around the command

Following ``test_submit_jobs_log_flag.py``, we only verify the rendered
script here; the execution semantics (teardown runs on success / failure
/ signal; setup gates the command) are covered by cmd_queue's own tests.
"""
from __future__ import annotations

import inspect
from typing import Any

import cmd_queue
import pytest
import ubelt as ub

from kwdagger.pipeline import Pipeline

# The resource-lifecycle feature requires a cmd_queue whose BashJob accepts
# ``setup`` / ``teardown`` (cmd_queue >= 0.3.1). Feature-detect rather than
# version-parse so this stays correct regardless of how it is packaged. Skip
# the whole module on older cmd_queue (e.g. the currently pinned 0.2.3) instead
# of failing -- the kwdagger plumbing cannot be exercised without upstream
# support.
from cmd_queue.serial_queue import BashJob

_HAS_SETUP_TEARDOWN = (
    'teardown' in inspect.signature(BashJob.__init__).parameters
)

pytestmark = pytest.mark.skipif(
    not _HAS_SETUP_TEARDOWN,
    reason='requires cmd_queue with BashJob setup/teardown support (>= 0.3.1)',
)


def _build_demo(root_dpath: ub.Path) -> Pipeline:
    dag = Pipeline.demo()
    dag.configure(config={}, root_dpath=root_dpath, cache=False)
    return dag


def _first_real_job(queue: Any) -> Any:
    # The concrete queue/job types (SerialQueue/TMUXMultiQueue, BashJob) expose
    # ``jobs``/``log``/``preamble``/``teardown``/``finalize_text`` that are not
    # on the cmd_queue base classes -- and which vary by cmd_queue version (e.g.
    # ``teardown`` only exists in cmd_queue >= 0.3.1). Type as ``Any`` so this
    # introspection is decoupled from the installed cmd_queue version.
    for job in queue.jobs:
        if not getattr(job, 'bookkeeper', 0):
            return job
    raise AssertionError('queue produced no real (non-bookkeeper) jobs')


def test_setup_teardown_plumbed_to_job(tmp_path):
    """A node's setup/teardown must reach the BashJob and the rendered
    script (gating preamble + cleanup trap)."""
    dag = _build_demo(ub.Path(tmp_path) / 'st-on')
    # Attach a setup/teardown to the first node; these are arbitrary shell.
    node = dag.nodes[0]
    node.setup = 'echo ACQUIRE'
    node.teardown = 'echo RELEASE'

    queue = cmd_queue.Queue.create(backend='serial', name='kwd-st', size=1)
    dag.submit_jobs(queue=queue, log=False)

    job = _first_real_job(queue)
    assert job.preamble and 'echo ACQUIRE' in job.preamble, (
        'setup must be folded into the gating preamble; got {!r}'.format(
            job.preamble
        )
    )
    assert job.teardown == ['echo RELEASE'], (
        'teardown must reach BashJob.teardown; got {!r}'.format(job.teardown)
    )

    text = job.finalize_text(with_status=True, with_gaurds=True)
    # setup gates the command via the shared PREAMBLE_OK machinery
    assert 'echo ACQUIRE' in text and 'PREAMBLE_OK' in text
    # teardown is rendered as a per-job, signal-safe cleanup trap
    assert '__cmdq_teardown' in text and 'echo RELEASE' in text
    assert 'trap __cmdq_teardown EXIT' in text


def test_no_setup_teardown_is_unchanged(tmp_path):
    """Nodes without setup/teardown must not gain a cleanup trap or a
    gating preamble (no behavior change on the common path)."""
    dag = _build_demo(ub.Path(tmp_path) / 'st-off')
    queue = cmd_queue.Queue.create(backend='serial', name='kwd-nost', size=1)
    dag.submit_jobs(queue=queue, log=False)

    job = _first_real_job(queue)
    assert not job.teardown
    text = job.finalize_text(with_status=True, with_gaurds=True)
    assert '__cmdq_teardown' not in text


def test_setup_teardown_render_on_tmux_backend(tmp_path):
    """The same plumbing must hold for the tmux backend, which reuses the
    serial job rendering inside per-worker queues."""
    dag = _build_demo(ub.Path(tmp_path) / 'st-tmux')
    node = dag.nodes[0]
    node.setup = 'echo ACQUIRE'
    node.teardown = 'echo RELEASE'

    queue = cmd_queue.Queue.create(backend='tmux', name='kwd-st-tmux', size=2)
    dag.submit_jobs(queue=queue, log=False)

    # The tmux multi-queue holds the BashJob objects directly.
    job = _first_real_job(queue)
    assert job.teardown == ['echo RELEASE']
    assert job.preamble and 'echo ACQUIRE' in job.preamble
    text = job.finalize_text(with_status=True, with_gaurds=True)
    assert '__cmdq_teardown' in text and 'PREAMBLE_OK' in text


if __name__ == '__main__':
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        td_path = ub.Path(td)
        test_setup_teardown_plumbed_to_job(td_path / 't1')
        test_no_setup_teardown_is_unchanged(td_path / 't2')
        test_setup_teardown_render_on_tmux_backend(td_path / 't3')
    print('All kwdagger submit_jobs setup/teardown tests passed.')
