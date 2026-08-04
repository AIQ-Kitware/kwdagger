"""
Arbitration between requests that share one process identity.

Identity describes the computation, so two requests that hash alike run one
job in one result directory. But several things deliberately do *not* reach
identity -- ``perf_params``, ``__enabled__``, Slurm options, output overrides,
and, since delivery mechanism left the hash, which jobs must run first and how
the experiment was requested. When requests that share an identity disagree
about any of those, nothing in the payload can arbitrate, and whichever request
arrived first would silently decide what actually runs and what is recorded.

The load-bearing comparison is the requested-experiment record itself -- what
would be written to ``job_config.json``. Anything derived from it is a summary,
and a summary can only lose distinctions the record was keeping on purpose. The
prerequisite and delivery comparisons above it are kept because they name the
two common conflicts precisely; the record comparison is what makes the check
complete.

This lives in its own leaf because *both* scheduling paths need it and they
sit at opposite ends of the package: gather pipelines compile the whole matrix
up front, while ordinary pipelines configure and submit a row at a time. It
works on snapshots rather than nodes for the same reason -- the row-at-a-time
path reconfigures one ``ProcessNode`` in place, so by the time a duplicate is
seen the original request's state is gone unless it was captured.
"""

from __future__ import annotations

from typing import Any


def _normalize_enabled(value: Any) -> Any:
    """
    Reduce an ``__enabled__`` value to how ``submit_jobs`` actually reads it.

    ``submit_jobs`` only ever tests truthiness and equality against ``'redo'``,
    so ``1`` and ``True`` are the same execution state and must not be reported
    as a conflict.
    """
    if value == 'redo':
        return 'redo'
    return bool(value)


def _normalize_paths(paths: Any) -> dict[str, str]:
    return {k: str(v) for k, v in dict(paths).items()}


#: Declared state that changes how a process runs but is deliberately kept out
#: of ``process_id``. Requests sharing an identity must agree on all of it.
#: Each entry is the config key a user writes, the snapshot field, and how to
#: read a value down to what the scheduler acts on.
_UNHASHED_EXECUTION_STATE = [
    ('__enabled__', 'enabled', _normalize_enabled),
    ('__slurm_options__', 'slurm_options', dict),
    # perf_params reach the command but not identity, by design.
    ('perf_params', 'perf_config', dict),
    # Output paths are excluded from identity too, and an override moves both
    # the command and where results land.
    ('out_paths', 'out_paths', _normalize_paths),
]

#: State that ``process_id`` is supposed to determine. A disagreement here is
#: a defect in the identity payload, not a user error.
_FINALIZED_EXECUTION_STATE = [
    ('node directories', 'node_dpath'),
    ('commands', 'command'),
    ('setup commands', 'setup'),
    ('teardown commands', 'teardown'),
]


def execution_snapshot(node: Any) -> dict[str, Any]:
    """
    Capture everything a duplicate request must be compared against.

    Taken eagerly, because the row-at-a-time scheduler reuses one node object
    across matrix rows: read these lazily and you would compare a request
    against itself.
    """
    snapshot: dict[str, Any] = {
        'enabled': node.enabled,
        'slurm_options': node.slurm_options,
        'perf_config': node.final_perf_config,
        'out_paths': node.final_out_paths,
        'node_dpath': str(node.final_node_dpath),
        'setup': getattr(node, 'setup', None),
        'teardown': getattr(node, 'teardown', None),
        'prerequisites': sorted(
            pred.process_id
            for pred in node.effective_predecessor_process_nodes()
        ),
        # Finer than the prerequisite union: two requests can need the same
        # jobs while disagreeing about which inputs those jobs supply.
        'delivery': dict(node.delivery_signature()),
        # The record itself, which is what the other two summarize. Only one
        # job_config.json can be written for a result directory, so anything
        # that record distinguishes has to agree -- including distinctions no
        # summary of delivery carries, such as which of two aliases supplied
        # the value and which was outranked.
        'requested': dict(node.requested_provenance_record()),
    }
    try:
        snapshot['command'] = node.final_command()
    except Exception:  # pragma: no cover - diagnostics must not break work
        snapshot['command'] = None
    return snapshot


def _abbreviate(value: Any, limit: int = 400) -> str:
    """Keep a gather membership or a long path list readable in a message."""
    if value is None:
        return '<not requested>'
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


def _describe_record_conflict(
    canonical: dict[str, Any],
    duplicate: dict[str, Any],
    differing: list[str],
    canonical_label: str,
    duplicate_label: str,
    max_keys: int = 5,
) -> str:
    """Render the requested-record keys two requests disagree about."""
    lines = []
    for key in differing[:max_keys]:
        lines.append(f'  {key}:')
        lines.append(
            f'    {canonical_label}: '
            f'{_abbreviate(canonical["requested"].get(key))}'
        )
        lines.append(
            f'    {duplicate_label}: '
            f'{_abbreviate(duplicate["requested"].get(key))}'
        )
    remaining = len(differing) - max_keys
    if remaining > 0:
        lines.append(f'  ... and {remaining} more key(s)')
    return '\n'.join(lines) + '\n'


def check_execution_agreement(
    canonical: dict[str, Any],
    duplicate: dict[str, Any],
    *,
    template_name: str,
    process_id: str,
    canonical_label: str,
    duplicate_label: str,
) -> None:
    """
    Reject requests that share a process identity but disagree on state.

    Args:
        canonical (dict): snapshot of the request that was accepted.
        duplicate (dict): snapshot of a later request with the same identity.
        template_name (str): the logical node name, for the message.
        process_id (str): the shared identity, for the message.
        canonical_label (str): how to name the first request to a user, e.g.
            ``'row 0'`` when compiling a matrix.
        duplicate_label (str): likewise for the later request.

    Raises:
        ValueError: the requests disagree about state identity cannot
            arbitrate -- something the user can fix.
        AssertionError: they disagree about state identity is supposed to
            determine, which is a defect in the identity payload.
    """
    for config_key, field, normalize in _UNHASHED_EXECUTION_STATE:
        lhs = canonical[field]
        rhs = duplicate[field]
        if normalize(lhs) == normalize(rhs):
            continue
        raise ValueError(
            f'Conflicting {config_key} values for process {template_name!r}. '
            f'{canonical_label.capitalize()} and {duplicate_label} resolve to '
            f'the same process identity {process_id!r} but request {lhs!r} '
            f'and {rhs!r} respectively. Process identity does not include '
            f'{config_key}, so these requests cannot be distinguished. Give '
            f'them differing parameters, or make their {config_key} values '
            'agree.'
        )

    if canonical['prerequisites'] != duplicate['prerequisites']:
        raise ValueError(
            f'Conflicting execution prerequisites for process '
            f'{template_name!r}. {canonical_label.capitalize()} and '
            f'{duplicate_label} resolve to the same process identity '
            f'{process_id!r} -- the same command over the same inputs -- but '
            f'require different jobs to run first:\n'
            f'  {canonical_label}: {canonical["prerequisites"] or "nothing"}\n'
            f'  {duplicate_label}: {duplicate["prerequisites"] or "nothing"}\n'
            'This usually means one request takes an input from a producer '
            'while another supplies the same path directly. Identity '
            'describes the computation, not how the value arrives, so these '
            'requests cannot be told apart. Use the same delivery in both, or '
            'give them differing parameters.'
        )

    if canonical['delivery'] != duplicate['delivery']:
        differing = sorted(
            name
            for name in set(canonical['delivery']) | set(duplicate['delivery'])
            if canonical['delivery'].get(name)
            != duplicate['delivery'].get(name)
        )
        raise ValueError(
            f'Conflicting input delivery for process {template_name!r}. '
            f'{canonical_label.capitalize()} and {duplicate_label} resolve to '
            f'the same process identity {process_id!r} and need the same jobs '
            f'to run first, but disagree about where {differing} come from:\n'
            f'  {canonical_label}: '
            f'{ {k: canonical["delivery"].get(k) for k in differing} }\n'
            f'  {duplicate_label}: '
            f'{ {k: duplicate["delivery"].get(k) for k in differing} }\n'
            'One request takes an input from a producer while another supplies '
            'the same path directly. They are the same computation and hash '
            'the same, but only one requested-experiment record can be written '
            'for the result directory they share, so they cannot both be kept. '
            'Use the same delivery in both, or give them differing parameters.'
        )

    if canonical['requested'] != duplicate['requested']:
        differing = sorted(
            key
            for key in set(canonical['requested']) | set(duplicate['requested'])
            if canonical['requested'].get(key)
            != duplicate['requested'].get(key)
        )
        raise ValueError(
            f'Conflicting requested experiment for process {template_name!r}. '
            f'{canonical_label.capitalize()} and {duplicate_label} resolve to '
            f'the same process identity {process_id!r} -- the same command '
            f'over the same inputs, needing the same jobs to run first -- but '
            f'describe what was asked for differently:\n'
            + _describe_record_conflict(
                canonical,
                duplicate,
                differing,
                canonical_label,
                duplicate_label,
            )
            + 'Only one job_config.json can be written for the result '
            'directory they share, so keeping both would make the persisted '
            'record depend on which request arrived first. Ask for the same '
            'thing in both, or give them differing parameters.'
        )

    for label, field in _FINALIZED_EXECUTION_STATE:
        lhs = canonical[field]
        rhs = duplicate[field]
        if lhs == rhs:
            continue
        raise AssertionError(
            f'Internal consistency error: {canonical_label} and '
            f'{duplicate_label} resolve {template_name!r} to the same process '
            f'identity {process_id!r} but to different {label}:\n'
            f'  {lhs!r}\n  {rhs!r}\n'
            'Process identity is meant to determine the command and its '
            'outputs, so this is a defect in the identity payload rather than '
            'a problem with the pipeline. Please report it.'
        )
