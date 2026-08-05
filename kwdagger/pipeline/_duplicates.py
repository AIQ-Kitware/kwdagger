"""
Optional diagnostics for matrix rows that compile to one process.

Two rows that produce one ``process_id`` are one job. Whatever they disagree
about is, by construction, something identity excludes -- ``perf_params``,
Slurm options, ``__enabled__``, output overrides, how a value was delivered.
Kwdagger runs the grid and records what it ran, so **the first request wins and
that is not an error**. The user chose the identity model when they chose which
fields reach the hash; kwdagger does not protect them from a collision they
allowed.

This module exists for the user who wants to be *told* anyway. It compares two
requests and formats what differs. It decides nothing: the caller picks a
policy and acts on the comparison.

A leaf. It knows how to describe a difference and nothing about scheduling.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeAlias

if TYPE_CHECKING:
    # Annotation only: ``_process`` sits above this leaf, and the layering
    # test in ``tests/test_import_compat.py`` allows an upward reference that
    # costs nothing at runtime and cannot create a cycle.
    from kwdagger.pipeline._process import ProcessNode

#: How later equal-identity requests are reported. Deliberately not named
#: "permissive" and "strict": ``error`` is an extra constraint a user asked
#: for, not a stricter definition of correct.
DuplicatePolicy: TypeAlias = Literal['first', 'warn', 'error']

#: Every accepted value, for coercion and for error messages.
DUPLICATE_POLICIES: tuple[str, ...] = ('first', 'warn', 'error')


def coerce_duplicate_policy(policy: Any) -> DuplicatePolicy:
    """
    Validate a duplicate policy, defaulting to ``'first'``.

    Args:
        policy (Any): ``'first'``, ``'warn'``, ``'error'``, or ``None``.

    Returns:
        DuplicatePolicy: the validated policy.

    Raises:
        ValueError: the policy is not one of the three.

    Example:
        >>> coerce_duplicate_policy(None)
        'first'
        >>> coerce_duplicate_policy('warn')
        'warn'
    """
    if policy is None:
        return 'first'
    if policy in DUPLICATE_POLICIES:
        return policy
    raise ValueError(
        f'Unknown duplicate_policy {policy!r}. Choose one of '
        f'{list(DUPLICATE_POLICIES)}: "first" (the default) keeps the first '
        'request for each process_id and reports nothing, "warn" also '
        'describes what later requests differed about, and "error" refuses '
        'the compilation.'
    )


def _normalize_enabled(value: Any) -> Any:
    """
    Reduce ``__enabled__`` to how submission actually reads it.

    Submission only tests truthiness and equality against ``'redo'``, so ``1``
    and ``True`` are the same state and must not be described as a difference.
    """
    if value == 'redo':
        return 'redo'
    return bool(value)


def _paths(value: Mapping[str, Any]) -> dict[str, str]:
    return {key: str(item) for key, item in dict(value).items()}


#: The non-identity state worth naming when two requests differ, as
#: ``(label a user recognizes, how to read it off a node)``. Diagnostic only:
#: nothing here reaches ``process_id``, and nothing here decides what runs.
_COMPARED: tuple[tuple[str, Callable[[ProcessNode], Any]], ...] = (
    ('perf_params', lambda node: dict(node.final_perf_config)),
    (
        '__slurm_options__',
        lambda node: dict(getattr(node, 'effective_slurm_options', None) or {}),
    ),
    ('__enabled__', lambda node: _normalize_enabled(node.enabled)),
    ('out_paths', lambda node: _paths(node.final_out_paths)),
    ('setup', lambda node: getattr(node, 'setup', None)),
    ('teardown', lambda node: getattr(node, 'teardown', None)),
    (
        'effective predecessors',
        lambda node: sorted(
            pred.process_id
            for pred in node.effective_predecessor_process_nodes()
        ),
    ),
    (
        'requested experiment',
        lambda node: dict(node.requested_provenance_record()),
    ),
    ('command', lambda node: _safe_command(node)),
)


def _safe_command(node: ProcessNode) -> Any:
    try:
        return node.final_command()
    except Exception:  # pragma: no cover - a diagnostic must not raise
        return None


def _abbreviate(value: Any, limit: int = 200) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


@dataclass
class DuplicateComparison:
    """What two requests sharing one ``process_id`` disagree about."""

    #: The shared identity.
    process_id: str
    #: The logical node name, for a message a user recognizes.
    template_name: str
    #: How to name the request that was kept, e.g. ``'row 0'``.
    canonical_label: str
    #: How to name the later request.
    duplicate_label: str
    #: ``{field: (canonical value, duplicate value)}``, in declared order.
    differences: dict[str, tuple[Any, Any]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.differences)

    def format_message(self) -> str:
        """Render the difference for a warning or an error."""
        lines = [
            f'{self.canonical_label.capitalize()} and {self.duplicate_label} '
            f'resolve {self.template_name!r} to the same process identity '
            f'{self.process_id!r}, but differ in state that identity does not '
            f'include:',
        ]
        for name, (lhs, rhs) in self.differences.items():
            lines.append(f'  {name}:')
            lines.append(f'    {self.canonical_label}: {_abbreviate(lhs)}')
            lines.append(f'    {self.duplicate_label}: {_abbreviate(rhs)}')
        lines.append(
            f'The {self.canonical_label} request is the one that runs and the '
            'one recorded in job_config.json. This is normal: those fields are '
            'outside process_id by design, so kwdagger keeps the first request '
            'for each identity. Put the distinction into identity-bearing '
            'configuration if the two should be separate results.'
        )
        return '\n'.join(lines)

    def to_error(self) -> ValueError:
        """The same message, as the exception ``'error'`` policy raises."""
        return ValueError(
            self.format_message()
            + '\n(Reported because duplicate_policy="error" was requested; '
            'the default policy keeps the first request silently.)'
        )


def compare_duplicate_requests(
    canonical: ProcessNode,
    duplicate: ProcessNode,
    *,
    template_name: str,
    process_id: str,
    canonical_label: str,
    duplicate_label: str,
) -> DuplicateComparison:
    """
    Describe how a later equal-identity request differs from the kept one.

    Called only under ``warn`` and ``error``. The default policy never builds
    a comparison, so the ordinary path pays nothing for this.

    Args:
        canonical (ProcessNode): the request that was kept.
        duplicate (ProcessNode): the later request with the same identity.
        template_name (str): the logical node name.
        process_id (str): the shared identity.
        canonical_label (str): how to name the kept request, e.g. ``'row 0'``.
        duplicate_label (str): how to name the later one.

    Returns:
        DuplicateComparison: falsy when the two requests agree.
    """
    comparison = DuplicateComparison(
        process_id=process_id,
        template_name=template_name,
        canonical_label=canonical_label,
        duplicate_label=duplicate_label,
    )
    for name, read in _COMPARED:
        lhs = read(canonical)
        rhs = read(duplicate)
        if lhs != rhs:
            comparison.differences[name] = (lhs, rhs)
    return comparison
