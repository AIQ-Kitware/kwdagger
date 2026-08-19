"""
The queue name decides which tmux sessions cmd_queue considers "ours".

A constant meant every card shared one namespace, so starting one pipeline
reported an unrelated pipeline's sessions as conflicts and offered to kill
them.
"""


def test_the_queue_name_distinguishes_pipelines():
    from kwdagger.schedule import _default_queue_name

    assert _default_queue_name(
        'operadic_consistency.magnet.pipelines.lift_pipeline()'
    ) == 'schedule-lift_pipeline'
    assert _default_queue_name(
        'cards.pipelines.drag_pipeline()'
    ) == 'schedule-drag_pipeline'
    # Two different pipelines must not collide; that is the whole point.
    assert _default_queue_name('a.b.lift_pipeline()') != _default_queue_name(
        'a.b.drag_pipeline()'
    )
    # ... but two runs of the SAME pipeline still share a name, because that
    # is a genuine conflict worth detecting.
    assert _default_queue_name('a.b.lift_pipeline()') == _default_queue_name(
        'other.module.lift_pipeline()'
    )


def test_the_queue_name_survives_odd_input():
    """A queue name is never worth raising over."""
    from kwdagger.schedule import _default_queue_name

    assert _default_queue_name('') == 'schedule-eval'
    assert _default_queue_name(None) == 'schedule-eval'
    assert _default_queue_name('registered_name') == 'schedule-registered_name'
    assert _default_queue_name('pkg.mod.fn(arg=1, other=2)') == 'schedule-fn'
    # Characters a tmux session name should not carry are dropped.
    assert '/' not in _default_queue_name('a.b.we/ird()')


def test_an_explicit_queue_name_still_wins():
    from kwdagger.schedule import ScheduleEvaluationConfig

    config = ScheduleEvaluationConfig(
        queue_name='my-own', pipeline='a.b.lift_pipeline()')
    assert config['queue_name'] == 'my-own'

    config = ScheduleEvaluationConfig(pipeline='a.b.lift_pipeline()')
    assert config['queue_name'] == 'schedule-lift_pipeline'
