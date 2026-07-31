Hashes and IDs in KWDagger
==========================

KWDagger uses several short hash-like identifiers, but they serve different
purposes.  The most important distinction is between operational result reuse
and optional analytical grouping.

* **Pipeline node IDs** name hashed result directories and help the scheduler
  decide whether requested work can be reused.
* **Aggregation IDs** group or compare rows in a report.  They are one way to
  query results and are not the definition of execution identity.

These identifiers intentionally hash different inputs.  See
:doc:`parameter_identity` for the broader execution model and the limits of
what the hashes mean.

.. important::

    Pipeline IDs are operational proxies.  They hash declared parameters,
    paths, and lineage; they do not hash file contents and do not prove that a
    program is deterministic.  Re-running the same ``invoke.sh`` reproduces the
    requested command, not necessarily bit-identical output.

Summary of ID types
-------------------

``algo_id``
    Computed by ``ProcessNode.algo_id``.  A current implementation component
    derived from a node's identity-bearing parameters.  It has no strong
    standalone public contract.

``process_id``
    Computed by ``ProcessNode.process_id``.  The operational identity used for
    result directories and reuse under declared values and produced-artifact
    lineage.

``param_hashid``
    Computed by ``Aggregator.build_effective_params``.  The identity of a
    normalized parameter set used for optional reporting and grouping.

``macro_XX_...``
    Computed by ``hash_regions``.  A SMART/geowatch-oriented key for a macro
    group of ROI identifiers.

Common hashing scheme (base36 truncation)
----------------------------------------

KWDagger uses :func:`ubelt.hash_data` with base36 encoding and truncation.

* Pipeline IDs are produced through
  :func:`kwdagger.utils.reverse_hashid.condense_config`::

      ub.hash_data(other_opts, base=36)[0:12]

* Aggregation parameter IDs are produced by
  :func:`kwdagger.aggregate.hash_param`::

      ub.hash_data(row, base=36)[0:12]

* Macro region keys are produced by
  :func:`kwdagger.aggregate.hash_regions`::

      ub.hash_data(sorted(rois), base=36)[0:6]

The short strings improve filesystem and table ergonomics while accepting a
small nonzero collision probability.  See `Collision considerations`_.

Pipeline node IDs
-----------------

This section documents the current mechanics behind result-directory naming.
The intermediate names are implementation details; evaluate changes by their
observable effect on generated commands, reuse boundaries, result directories,
and ``.pred`` / ``.succ`` lineage.

Algorithm configuration: ``final_algo_config``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.final_algo_config` contains the current
node values treated as identity-bearing executable parameters.

Current behavior:

* output paths and ``perf_params`` are excluded;
* ``in_paths`` are excluded and handled separately;
* declared ``algo_params`` defaults are applied; and
* shared algorithm-parameter values are intended to be resolved before hashing.

The ``algo_params`` name and its boundary with input data are historical and may
be generalized later.

External input configuration: ``final_input_config``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.final_input_config` contains input values
that no concrete upstream process produced.  This includes directly supplied
inputs and input-to-input shared values.

These paths must still influence operational reuse, but they should not create
false process ancestry merely because another node's input port named the same
value.

Non-identity command values: ``final_perf_config``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.final_perf_config` contains values passed
to the command but excluded from result identity.  Despite the historical
``perf_params`` name, the mechanism means “assumed not to define a different
logical result.”

Algorithm ID: ``algo_id``
^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.algo_id` hashes a payload containing the
node name and ``final_algo_config`` through
:func:`kwdagger.utils.reverse_hashid.condense_config`.

``algo_id`` is useful to the current construction of process identity, but it
is not yet a stable promise that independently defines “the algorithm.”  Do not
rely on the hash suffix as a cross-pipeline scientific identifier.

Dependency summary: ``depends``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.depends` builds the payload hashed by
``process_id``.  In the current implementation it can include:

* this node's ``algo_id`` and ancestor algorithm IDs;
* externally supplied input values;
* exact produced-input bindings;
* gather policy and ordered member process IDs; and
* other lineage details needed to distinguish reusable work.

A known-value sharing relationship should contribute the effective value and
requested provenance without pretending that the source process executed or
materialized the value.

Process ID: ``process_id``
^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.process_id` hashes ``depends`` using
:func:`kwdagger.utils.reverse_hashid.condense_config`.

This is the directory-facing reuse identity.  Equal process IDs mean kwdagger
considers two requests to describe the same reusable work under its declared
model.  They do not establish content equality, external-file immutability, or
program determinism.

How pipeline directories are named
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The output tree is constructed by formatting templates with condensed ID
fields:

* :meth:`kwdagger.pipeline.ProcessNode.template_node_dpath` defines the
  directory template;
* :meth:`kwdagger.pipeline.ProcessNode.condensed` provides ``<node>_id`` from
  ``process_id``, ``<node>_algo_id`` from ``algo_id``, and ancestor
  substitutions; and
* :meth:`kwdagger.pipeline.ProcessNode.final_node_dpath` formats the final path.

The resulting directory, its ``invoke.sh``, and its ``.pred`` / ``.succ`` links
are more important user-facing contracts than any standalone interpretation of
``algo_id``.

``condense_config`` formatting and behavior
-------------------------------------------

:func:`kwdagger.utils.reverse_hashid.condense_config` hashes the input mapping
and prefixes the truncated suffix with the supplied type::

    suffix = ub.hash_data(params, base=36)[0:12]
    result = f'{type}_{suffix}'

Aggregation IDs (table-facing)
------------------------------

This section explains the ID used in aggregated result tables.

Where aggregation parameters come from
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The aggregation loader reads per-node metadata from each node's result directory.

* ``job_config.json`` is read to obtain the requested parameters.
* Keys are prefixed with ``params`` (DotDict prefixing).
* The loader constructs a "specified mask" where each requested key is marked as included.

Reference: ``aggregate_loader.py`` :func:`load_result_worker`.


Effective parameters and ``param_hashid``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:param:`param_hashid` is computed by :meth:`kwdagger.aggregate.Aggregator.build_effective_params`.

Key design points:

* Aggregation begins from :attr:`Aggregator.requested_params`.
* Certain columns may be normalized for grouping (notably paths), and a mapping may be
  kept so users can inspect the original values.
  Reference: ``aggregate.py`` :meth:`Aggregator.build_effective_params`
  (see the use of ``pandas_condense_paths``).

* Some columns are intentionally excluded from the hash to avoid fragmenting groups.
  Reference: ``aggregate.py`` :meth:`Aggregator.build_effective_params`
  (see construction of ``hashid_ignore_columns`` and ``param_cols``).

* ``param_hashid`` is computed per unique parameter group, but is further subdivided by
  the "specified parameter" inclusion mask. This means rows that differ only in which
  parameters are considered "active / specified" may intentionally receive different
  hash IDs.
  Reference: ``aggregate.py`` :meth:`Aggregator.build_effective_params`
  (see ``is_param_included = ...`` and the subsequent subgrouping).

* The actual hash string is produced by :func:`kwdagger.aggregate.hash_param` using::

      ub.hash_data(row, base=36)[0:12]

  Reference: ``aggregate.py`` :func:`hash_param`.


Macro region keys
^^^^^^^^^^^^^^^^^

Macro region aggregation uses a separate key format produced by
:func:`kwdagger.aggregate.hash_regions`.

It hashes the set of ROI ids (sorted) using base36 truncation and formats the final key
as ``macro_{len(rois):02d}_{suffix}``, where the suffix is::

    ub.hash_data(sorted(rois), base=36)[0:6]

Reference: ``aggregate.py`` :func:`hash_regions`.


Why ``param_hashid`` and pipeline folder suffixes can differ
------------------------------------------------------------

Even though the hash encoding scheme is now consistent (base36), mismatches between
``param_hashid`` and pipeline folder suffixes are still expected because they hash
different *inputs*:

* Pipeline folder suffixes are derived from :meth:`ProcessNode.process_id`, which hashes
  the ancestry mapping returned by :meth:`ProcessNode.depends`.

* ``param_hashid`` is derived from :meth:`Aggregator.build_effective_params`, which hashes
  a normalized subset of requested parameters (and may ignore some columns).

References:
``pipeline.py`` :meth:`ProcessNode.process_id`,
``pipeline.py`` :meth:`ProcessNode.depends`,
``aggregate.py`` :meth:`Aggregator.build_effective_params`,
``aggregate.py`` :func:`hash_param`.


Collision considerations
------------------------

All truncated hashes have a nonzero collision probability. The design intent is that
IDs are short enough to be readable while still being extremely unlikely to collide for
typical experimental scales.

Current truncation choices:

* 12 base36 characters for pipeline IDs and ``param_hashid``.
* 6 base36 characters for macro region suffixes.

For collision safety in critical contexts, consider implementing explicit collision
detection at the point where a hash is used as a unique key.

Two places where collisions would be most visible:

* Aggregation: :meth:`Aggregator.build_effective_params` stores a mapping from
  ``param_hashid`` to effective params.
  A defensive check can ensure that reusing an existing hashid with different content
  is treated as an error.
  Reference: ``aggregate.py`` :meth:`Aggregator.build_effective_params`.

* Pipeline results: if IDs are used to select a directory path, a collision could cause
  two distinct configurations to map into the same output folder. Storing and verifying
  the full config in metadata (e.g. job config / resolved config) can detect that
  situation.
