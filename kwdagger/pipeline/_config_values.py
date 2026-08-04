"""
The configuration domain's one internal representation.

kwdagger accepts configuration from three places -- YAML, the command line, and
Python callers -- and they do not agree about types. This module is the
boundary that resolves that, and the invariant it establishes is what every
later reader may assume:

    After configuration coercion, every path-like object is a string and every
    mapping key is a string.

The second half is a domain rule, not merely the removal of a Python-only
shape: **configuration mappings must use string keys, including mappings loaded
from YAML.** YAML decodes ``0:``, ``true:`` and ``null:`` into non-string Python
keys, so ``class_weights: {0: 1.0, 1: 2.5}`` is rejected here. Mapping keys are
variable identifiers. ``os.PathLike`` values *and* keys are accepted as a
Python-side convenience and normalized to strings.

TODO:
    Raw ``bytes`` configuration values are not normalized and are not
    guaranteed to serialize -- they reach ``json.dumps`` unchanged and fail
    there, rather than being rejected here. kwdagger's YAML/CLI-oriented
    configuration domain is text based; callers should decode bytes to strings
    before configuring. A bytes-returning ``os.PathLike`` *is* rejected, by
    :func:`_fspath_str`, because that one arrives through a conversion this
    module performs. Adding bytes-specific traversal or an encoding policy is
    deliberately out of scope.

Identity, commands, provenance, arbitration, and the JSON written to
``job_config.json`` all read the same shape as a result. The alternative --
each of those separately understanding ``os.PathLike`` -- is what let a ``Path``
mapping key hash cleanly and then fail at serialization, and let ``json.dumps``
silently rename an ``int`` key so that the payload and the persisted record
disagreed about what the configuration was.

Normalization converts *spelling*, never meaning: ``os.fspath`` does not
resolve or absolutize, so a relative path stays relative until the
path-resolution stage deliberately interprets it. The caller's original type is
not retained or reproduced.

A leaf: it imports nothing from the package, because everything that stores
configuration needs it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any


def _fspath_str(value: os.PathLike[Any], what: str) -> str:
    """
    ``os.fspath``, restricted to the half of its contract kwdagger can use.

    ``__fspath__`` may return ``bytes``, and a bytes path is not a
    configuration value kwdagger can carry: it cannot be a JSON object name,
    it is not JSON-serializable as a value, and guessing an encoding for it
    here would silently invent the spelling that ends up in the hash and on
    the command line. The caller knows the encoding; kwdagger does not.

    Raises:
        TypeError: the path is a bytes path.
    """
    path = os.fspath(value)
    if isinstance(path, bytes):
        raise TypeError(
            f'{what} {value!r} is a bytes path. kwdagger records configuration '
            'as text -- in job_config.json, in the identity payload, and on '
            'the command line -- and will not guess an encoding for you. '
            'Decode it yourself, with os.fsdecode() or an explicit codec.'
        )
    return path


def normalize_config_key(key: Any) -> str:
    """
    The one form a mapping key takes once configuration has been coerced.

    Strings are what YAML and the CLI supply; a Python caller may pass a
    :class:`os.PathLike` as a convenience and it is converted here. Nothing
    else is accepted -- a JSON object name is a string, so any other key type
    is a Python-only shape that would be renamed by ``json.dumps`` on the way
    to disk and read back as something the configuration never contained.

    Raises:
        TypeError: the key is neither a string nor a path.

    Example:
        >>> import pathlib
        >>> [normalize_config_key(k) for k in ['a', pathlib.Path('b/c')]]
        ['a', 'b/c']
    """
    if isinstance(key, str):
        return key
    if isinstance(key, os.PathLike):
        return _fspath_str(key, 'Configuration mapping key')
    raise TypeError(
        f'Configuration mapping key {key!r} of type {type(key).__name__} must '
        'be a string or a path. Configuration is recorded as JSON, whose '
        'object names are strings, so any other key type cannot survive a '
        'round trip through job_config.json.'
    )


def normalize_config_value(value: Any) -> Any:
    """
    Coerce a configured value to kwdagger's one internal representation.

    The invariant this establishes, and that everything downstream may assume:
    **after configuration coercion every path-like object is a string and every
    mapping key is a string.** Identity, commands, provenance, arbitration, and
    the JSON on disk then all read the same shape, instead of each separately
    understanding :class:`os.PathLike`.

    The caller's original type is not retained or reproduced. Spelling is:
    ``os.fspath`` does not resolve or absolutize, so a relative path stays
    relative until the path-resolution stage deliberately interprets it.

    Raises:
        TypeError: a mapping key is neither a string nor a path.
        ValueError: two keys of one mapping normalize to the same key.
    """
    if isinstance(value, os.PathLike):
        return _fspath_str(value, 'Configuration value')
    if isinstance(value, Mapping):
        # Normalization is many-to-one -- ``Path('/a')`` and ``'/a'`` are one
        # key -- so rebuilding the mapping can drop an entry. That would
        # persist an ambiguous record and give two different configurations one
        # identity, so it is refused rather than resolved.
        normalized: dict[str, Any] = {}
        sources: dict[str, Any] = {}
        for key, item in value.items():
            name = normalize_config_key(key)
            if name in normalized:
                raise ValueError(
                    f'Configuration key collision after normalization: '
                    f'{sources[name]!r} and {key!r} are distinct in Python but '
                    f'both normalize to {name!r}. Keeping both would persist '
                    'an ambiguous record and give two different configurations '
                    'one identity. Use a single spelling of the key.'
                )
            normalized[name] = normalize_config_value(item)
            sources[name] = key
        return normalized
    if isinstance(value, (list, tuple)):
        # Sequences become lists: JSON has one array type, and a tuple that
        # survived to the payload would hash differently from the list it is
        # read back as.
        return [normalize_config_value(item) for item in value]
    return value


def normalize_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """
    Normalize a whole configuration mapping, keys and values alike.

    The keys here are parameter names, which are already strings in every
    supported source; running them through the same check keeps this one
    boundary responsible for the whole invariant rather than leaving the
    outermost level as the exception.
    """
    return normalize_config_value(dict(config))
