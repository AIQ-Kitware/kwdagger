Parameter Classification and Node Identity
==========================================

What a node carries, which of it becomes identity, and which identity.

This is the model the scheduler relies on for caching and for deciding
which jobs are the "same" job. It is worth reading before adding a
parameter to a node, because *which kind* you choose determines whether
changing it invalidates prior work.

The four kinds of parameter
---------------------------

``algo_params``
    Values that change what the computation produces. Identity-bearing.

``perf_params``
    Values that change only how fast or how parallel the computation is.
    Deliberately **not** identity-bearing: changing ``workers`` must not
    invalidate a result.

``in_paths``
    Data the node reads. May be supplied three ways, and the way matters
    (see `How an input is supplied`_).

``out_paths``
    Data the node writes. Never identity-bearing on their own -- their
    location is *derived from* identity, so including them would be
    circular.

The three configs
-----------------

``final_algo_config``
    ``config`` minus ``out_paths`` and ``perf_params``, plus
    ``algo_params`` defaults, **plus the resolved value of every input
    that is not produced by an ancestor**. That last clause is the
    surprising one; see `Finding 1`_.

``final_perf_config``
    The ``perf_params``. Feeds the command line, never an id.

``depends``
    What ``process_id`` hashes: every ancestor's ``algo_id``, this node's
    own ``algo_id``, the provenance of each connected input, and for a
    gathered input its spec and the exact member process ids.

The two ids
-----------

``algo_id`` = ``hash(final_algo_config)``
    Documented as "does NOT have a dependency on the larger DAG".

``process_id`` = ``hash(depends)``
    Identity of this computation *in this DAG*, including everything
    upstream. Node output directories are named from it.

How an input is supplied
------------------------

Three wirings, which classify differently:

``unconnected``
    The matrix supplies the path. No predecessor. The value lands in
    ``final_algo_config``, so it is part of ``algo_id``.

``aliased`` (``a.inputs['x'].connect(b.inputs['x'])``)
    Another node consumes the same value; nothing produces it. Treated
    exactly like ``unconnected`` -- the value is in ``final_algo_config``,
    and the source instance is deliberately *not* part of identity.
    (Recording the source instance made the consumer fan out over sweep
    axes it never reads.)

``connected`` (``a.outputs['x'].connect(b.inputs['x'])``)
    An ancestor produced it. The value is excluded from
    ``final_algo_config``, because the producing instance's identity is
    already folded into ``process_id`` via ancestor hashing. Including it
    would double-count.

``gathered``
    Excluded from ``final_algo_config``: the manifest path is derived from
    ``process_id``, so using it would be circular. Membership enters
    identity through ``depends['__gather__.<port>']`` instead.

Measured behaviour
------------------

From ``dev/audits/`` on a detection + segmentation pipeline where the
dataset path is simultaneously a predictor input, the gather key, and the
truth a scorer measures against:

===============================  ==============  ================
perturbation                     ``algo_id``     ``process_id``
===============================  ==============  ================
``detect.workers`` (perf)        unchanged       unchanged
``detect.thresh`` (algo)         detect only     detect + all downstream
``score_det.iou_thresh``         score_det only  score_det + summarize
add a model to the cohort        **unchanged**   consumers only
sibling branch parameter         unchanged       that branch + summarize
===============================  ==============  ================

Two properties worth naming, because they are the point of the design:

* A perf param moves nothing.
* Adding a cohort member leaves every existing instance's ids untouched,
  so previously computed work stays valid. Only the consumers that now
  gather one more member move.

Known inconsistencies
---------------------

.. _Finding 1:

Finding 1: ``algo_id`` is wiring-dependent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Run the identical algorithm on the identical data, wired three ways
(``dev/audits/case_wiring_equivalence.py``):

=============  =====================================  ============
variant        ``final_algo_config``                  ``algo_id``
=============  =====================================  ============
unconnected    ``{model, data_fpath}``                ``xaudwts5o0``
aliased        ``{model, data_fpath}``                ``xaudwts5o0``
produced       ``{model}`` -- no data at all          ``c45qpqiscp``
=============  =====================================  ============

So ``algo_id`` answers "this algorithm on this data" when the path comes
from the matrix, and "this algorithm" when an upstream node produced it.
Two pipelines running the same computation cannot be compared by
``algo_id`` if they wire it differently.

The reason is structural rather than accidental: for an unconnected input
there is no ancestor to carry the data identity, so ``final_algo_config``
is the only place it can live. ``depends`` carries a TODO acknowledging
exactly this -- *"We need to know what input paths have not been
represented"*.

A resolution consistent with the name ``algo_id`` would be to split the
concept:

* ``final_algo_config`` -- algorithm parameters only, no paths.
* ``final_input_config`` -- resolved values of inputs no ancestor
  produced (unconnected and aliased).
* ``depends`` gains the latter, replacing the TODO.

``algo_id`` then becomes wiring-independent and comparable across
pipelines, while ``process_id`` keeps distinguishing datasets exactly as
it does now. All three variants above would share one ``algo_id``.

Finding 2: an empty ``final_algo_config`` hashes identically everywhere
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``algo_id`` hashes ``final_algo_config`` alone; the node name is only a
*prefix* on the resulting string. Any two nodes with empty algo configs
therefore share the hash portion -- ``summarize`` in one pipeline and
``report`` in an unrelated one both yield ``...rbmqz8lzuq5f``. The prefix
keeps full ids distinct, so this is not a correctness bug, but it makes
the hash portion useless as a standalone key and is surprising when
reading two runs side by side.

Finding 3: a gather's target cannot reach its own source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

While a gather is being resolved, its source is not yet an ancestor of
its target, so ``group_by`` cannot name it. On a realistic pipeline --
predictors fanning out over a dataset, scorers gathering them -- the
scorer has *no* ordinary edge to the predictor, so
``group_by: [detect.dataset_fpath]`` raises.

The working formulation requires the two nodes to use the **same port
name** so an unqualified key resolves independently on each side. That
reintroduces a naming coupling the qualified form was meant to remove,
and it is invisible until compile time. Worth revisiting: a gather could
plausibly *partition* its sources and induce one target per group, rather
than joining against a target axis that must already exist.

Reproducing
-----------

.. code:: bash

    python dev/audits/param_identity_audit.py
