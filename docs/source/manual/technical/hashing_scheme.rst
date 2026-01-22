Hashes and IDs in KWDagger
==========================

KWDagger uses multiple identifiers that are all “hash-like”, but they serve
different purposes:

* **Pipeline node IDs** (directory-facing): deterministic identifiers used to name node
  result folders in the pipeline output tree.

* **Aggregation IDs** (table-facing): identifiers used to group / compare rows in an
  aggregated report.

These IDs intentionally do not all hash the same inputs, but they now share a
consistent hash *encoding scheme* (base36, truncated to a fixed length).


Summary of ID types
-------------------

+-------------------+------------------------------+---------------------------------------------+
| ID / key          | Where it is computed         | What it represents                          |
+===================+==============================+=============================================+
| ``algo_id``       | ``pipeline.py``              | Identity of this node's algorithm-defining  |
|                   | ``ProcessNode.algo_id``      | configuration, independent of DAG ancestry. |
+-------------------+------------------------------+---------------------------------------------+
| ``process_id``    | ``pipeline.py``              | Identity of this node *within a pipeline*,  |
|                   | ``ProcessNode.process_id``   | including ancestor node identities.         |
+-------------------+------------------------------+---------------------------------------------+
| ``param_hashid``  | ``aggregate.py``             | Identity of a row's effective parameter set |
|                   | ``Aggregator.build_effective_params`` | (normalized / grouped for reporting).     |
+-------------------+------------------------------+---------------------------------------------+
| macro region key  | ``aggregate.py``             | Key for a macro-aggregated group of ROIs    |
| ``macro_XX_...``  | ``hash_regions``             | based on the ROI id set.                    |
+-------------------+------------------------------+---------------------------------------------+


Common hashing scheme (base36 truncation)
----------------------------------------

KWDagger uses :func:`ubelt.hash_data` with base36 encoding and truncation.

* Pipeline IDs are produced through :func:`kwdagger.utils.reverse_hashid.condense_config`
  (see below), which now uses::

      ub.hash_data(other_opts, base=36)[0:12]

  Reference: ``reverse_hashid.py`` :func:`condense_config`.

* Aggregation parameter IDs are produced by :func:`kwdagger.aggregate.hash_param`, which
  now uses::

      ub.hash_data(row, base=36)[0:12]

  Reference: ``aggregate.py`` :func:`hash_param`.

* Macro region keys are produced by :func:`kwdagger.aggregate.hash_regions`, which now uses::

      ub.hash_data(sorted(rois), base=36)[0:6]

  Reference: ``aggregate.py`` :func:`hash_regions`.

These choices are primarily to improve human ergonomics (short IDs) while keeping
collision probability low (see "Collision considerations").


Pipeline node IDs
-----------------

This section explains the IDs that back pipeline node directory naming.

Algorithm configuration: ``final_algo_config``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The algorithm-defining configuration is computed by
:meth:`kwdagger.pipeline.ProcessNode.final_algo_config`.

Key behavior:

* Output path keys and performance-tuning keys are excluded from the algorithm identity.
  (These are tracked by :attr:`ProcessNode.out_paths` and :attr:`ProcessNode.perf_params`.)

* Input paths that are *not connected* to upstream nodes (i.e. "root inputs") can be
  included in the algorithm identity.

Reference: ``pipeline.py`` :meth:`ProcessNode.final_algo_config`.


Algorithm ID: ``algo_id``
^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.algo_id` creates a deterministic ID for a node based
on its algorithm-defining configuration.

It calls :func:`kwdagger.utils.reverse_hashid.condense_config` on ``final_algo_config``.

Reference: ``pipeline.py`` :meth:`ProcessNode.algo_id`.
Reference: ``reverse_hashid.py`` :func:`condense_config`.


Dependency summary: ``depends``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.depends` builds a mapping used to incorporate
ancestry into the pipeline identity.

It constructs a dictionary of:

* each ancestor process-node name -> that ancestor's ``algo_id``
* plus this node's name -> this node's ``algo_id``

Reference: ``pipeline.py`` :meth:`ProcessNode.depends`.


Pipeline ID: ``process_id``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

:meth:`kwdagger.pipeline.ProcessNode.process_id` computes a deterministic ID for a node
*within the context of its pipeline ancestry*.

It hashes the mapping returned by :meth:`ProcessNode.depends` using
:func:`kwdagger.utils.reverse_hashid.condense_config`.

Reference: ``pipeline.py`` :meth:`ProcessNode.process_id`.
Reference: ``reverse_hashid.py`` :func:`condense_config`.


How pipeline directories are named
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The pipeline output tree is constructed by formatting templates with the node's
condensed ID fields:

* :meth:`kwdagger.pipeline.ProcessNode.template_node_dpath` defines the template that
  includes the ``{<node>_id}`` field.

* :meth:`kwdagger.pipeline.ProcessNode.condensed` constructs the dictionary of template
  substitutions, including:

  * ``<node>_id`` -> ``process_id``
  * ``<node>_algo_id`` -> ``algo_id``
  * plus substitutions for ancestor nodes

* :meth:`kwdagger.pipeline.ProcessNode.final_node_dpath` formats the template using
  the condensed substitutions.

References:
``pipeline.py`` :meth:`ProcessNode.template_node_dpath`,
``pipeline.py`` :meth:`ProcessNode.condensed`,
``pipeline.py`` :meth:`ProcessNode.final_node_dpath`.


condense_config formatting and behavior
---------------------------------------

:func:`kwdagger.utils.reverse_hashid.condense_config` is responsible for producing the
string representation used in pipeline IDs.

At a high level, it:

1. hashes the input config dict using base36 truncation::

      ub.hash_data(params, base=36)[0:12]

2. returns an ID string by prefixing the hash with the provided ``type`` (e.g.
   ``f"{type}_{suffix}"``).

Reference: ``reverse_hashid.py`` :func:`condense_config`.


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
