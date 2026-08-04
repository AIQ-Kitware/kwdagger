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

    Pipeline IDs are operational proxies.  They hash declared parameters and
    effective input values -- not lineage; see `Identity is computation, not
    lineage`_.  They do not hash file contents and do not prove that a
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
    result directories and reuse, under this node's declared values and its
    effective input values.

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

:meth:`kwdagger.pipeline.ProcessNode.final_input_config` contains the effective
resolved value of every input, whichever way it arrived: directly supplied,
forwarded from a peer port, or produced upstream.

The one exception is a gathered input, whose manifest lives inside this node's
own result directory and is therefore derived from ``process_id``.  Hashing that
path would be circular, so ``depends['__gather__.<port>']`` carries the
collection's ordered contents instead.

These values influence operational reuse without creating process ancestry:
sharing a value with another node's input port is not a claim that the other
node ran.

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
``process_id``.  It contains:

* this node's own ``algo_id``;
* every input's **effective resolved value**, canonicalized, whether that value
  came from a producer, an alias, or configuration;
* for a gathered input, the gather policy and the ordered member *paths* the
  manifest will contain; and
* ``__dependency__.<node>`` for an explicit ordering edge, which carries no
  value and so has no other way to reach identity.

It deliberately does **not** contain producer process IDs, producer algorithm
IDs, port names, or any record of how a value was delivered.  A known-value
sharing relationship contributes the effective value and its requested
provenance without pretending the source process executed or materialized it.

Identity is computation, not lineage
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A process is identified by *the computation it will perform*: its effective
algorithm configuration, its effective resolved input values, and anything else
that changes its command or outputs.  It is not identified by how those input
values were obtained.

So these two consumers are the same process::

    producer output path: /results/model.pt      # wired: producer -> consumer
    manual input path:    /results/model.pt      # typed into the config

    consumer process_id:  identical
    provenance:           different
    scheduling:           different

The wired consumer waits for the producer and records ``source_kind: output``;
the manual one waits for nothing and records no producer wiring at all.  Both
run the same command over the same input, so both reuse the same result
directory.

This does not weaken invalidation.  A producer still reaches its consumer
through the value it supplies: a produced path contains the producer's
``process_id``, so reconfiguring the producer moves the path and the consumer's
identity moves with it.  If a producer change leaves the output path unchanged,
kwdagger treats the consumer's input as unchanged -- exactly as it does for a
stable hand-written path.

.. warning::

   kwdagger treats configured paths and values as data identity.  It does not
   prove that two files at the same path contain the same bytes.  Users who
   require content identity must provide checksums, content-addressed paths, or
   another explicit artifact identifier as a parameter.  Path equality does not
   guarantee byte equality, and this tradeoff is intentional.

Because identity determines the computation, a converse holds too: **equal
``process_id`` implies equal command-defining state, apart from state that is
deliberately unhashed.**

The deliberate exceptions are ``perf_params``, ``__enabled__``, Slurm options,
and output-path overrides.  All of them change how a process runs without
changing what it computes, which is why they are excluded from identity -- and
precisely because identity cannot tell such rows apart, matrix rows that
collapse onto one process must *agree* on them.  Compilation reports a
disagreement as a user-facing ``ValueError``.

Delivery mechanism is the other case identity cannot arbitrate.  Two rows may
be the same computation while requiring different jobs to run first -- one
taking an input from a producer, another supplying the same path directly.
Compilation reports that as a conflict as well, rather than letting whichever
row compiled first decide what the process waits for.

Anything *else* that reaches the command or the node directory without
reaching identity is a defect in the payload, and compilation raises an
internal-consistency error for it.

Paths inside kwdagger's own root are hashed relative to that root, so moving a
cache does not change any identity.  Paths outside it are hashed as given: they
identify external data.  A hand-supplied path that happens to point inside the
root canonicalizes exactly as a produced one does, which is what keeps the two
delivery mechanisms equal.

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
  ``process_id`` and ``<node>_algo_id`` from ``algo_id``, **for this node
  only**; and
* :meth:`kwdagger.pipeline.ProcessNode.final_node_dpath` formats the final path.

Substituting an *ancestor's* id was removed.  It let a node this one does not
read decide where its results are written, so two processes with the same
identity could finalize different paths.  A template that names another node's
id now raises an error explaining the migration: key the directory on this
node's own parameters, since an upstream change already reaches it through the
input value it supplies.

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
  the effective-computation payload returned by :meth:`ProcessNode.depends`.

* ``param_hashid`` is derived from :meth:`Aggregator.build_effective_params`, which hashes
  a normalized subset of requested parameters (and may ignore some columns).

References:
``pipeline/_process.py`` :meth:`ProcessNode.process_id`,
``pipeline/_process.py`` :meth:`ProcessNode.depends`,
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
