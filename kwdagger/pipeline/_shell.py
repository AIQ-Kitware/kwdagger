"""
Pure shell serialization.

Turning text into a command that writes that text back out is needed in two
places -- a process builds a gather manifest as part of its own command, and
the runtime writes ``invoke.sh`` -- so it lives below both of them. Nothing
here knows what a node or a queue is.
"""

from __future__ import annotations

import os


def bash_heredoc_write_command(
    text: str,
    output_fpath: str | os.PathLike[str],
    *,
    label: str = 'KWDAGGER_DATA',
    filter_command: str | None = None,
    command_indent: str = '',
    chain: bool = False,
) -> str:
    r"""Build a quoted-heredoc command that writes text without using argv.

    When the returned command is read from a script file, the heredoc body is
    script input rather than command arguments, so even a very large gather
    collection does not consume ``ARG_MAX``. Callers that transport the whole
    command through argv (for example ``sbatch --wrap``) must use a file-backed
    invocation instead. Quoting the delimiter disables parameter expansion,
    command substitution, and backslash processing inside the body.

    ``command_indent`` affects only the shell commands. The heredoc body and
    closing delimiter deliberately remain in column zero. ``chain=True`` adds
    ``&&`` after the directory and ``cat`` commands so a caller can compose the
    writer into a fail-fast brace group without enabling global ``set -e``.
    """
    import hashlib
    import shlex

    if '\x00' in text:
        raise ValueError('Bash heredocs cannot contain NUL bytes')
    output_fpath = os.fspath(output_fpath)
    parent = os.path.dirname(output_fpath) or '.'
    digest = hashlib.sha256(text.encode('utf8')).hexdigest()[:16].upper()
    safe_label = (
        ''.join(
            char if char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_' else '_'
            for char in label.upper()
        ).strip('_')
        or 'KWDAGGER_DATA'
    )
    delimiter_base = f'{safe_label}_{digest}'
    delimiter = delimiter_base
    body_lines = set(text.splitlines())
    suffix = 0
    while delimiter in body_lines:
        suffix += 1
        delimiter = f'{delimiter_base}_{suffix}'
    if text and not text.endswith('\n'):
        text += '\n'
    if filter_command is None:
        cat_line = f"cat > {shlex.quote(output_fpath)} <<'{delimiter}'"
    else:
        cat_line = (
            f"cat <<'{delimiter}' | {filter_command} > "
            f'{shlex.quote(output_fpath)}'
        )
    suffix = ' &&' if chain else ''
    return '\n'.join(
        [
            command_indent + f'mkdir -p -- {shlex.quote(parent)}' + suffix,
            command_indent + cat_line + suffix,
            text + delimiter,
        ]
    )
