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
    What algorithm the node runs: ``config`` minus ``out_paths``,
    ``perf_params`` and ``in_paths``, plus ``algo_params`` defaults.
    Deliberately contains no paths.

``final_input_config``
    The resolved value of every input **no ancestor produced** --
    ``unconnected`` and ``aliased``. These have no producing instance to
    speak for them, so they enter identity here.

``final_perf_config``
    The ``perf_params``. Feeds the command line, never an id.

``depends``
    What ``process_id`` hashes: every ancestor's ``algo_id``, this node's
    own ``algo_id``, the provenance of each connected input, and for a
    gathered input its spec and the exact member process ids.

The two ids
-----------

``algo_id`` = ``hash({'__node__': name, **final_algo_config})``
    Which algorithm this is, independent of the DAG *and of how its data
    was wired in*. Two pipelines running the same computation share an
    ``algo_id`` even if one takes its dataset from the matrix and the
    other from an upstream node.

``process_id`` = ``hash(depends)``
    Identity of this computation *in this DAG*: every ancestor's
    ``algo_id``, this node's own ``algo_id``, ``__inputs__`` (the inputs
    nothing produced), connected-input provenance, and gather membership.
    Node output directories are named from it.

How an input is supplied
------------------------

Three wirings, which classify differently:

``unconnected``
    The matrix supplies the path. No predecessor. The value lands in
    ``final_input_config`` and reaches ``process_id`` via
    ``depends['__inputs__']``. It is **not** part of ``algo_id``.

``aliased`` (``a.inputs['x'].connect(b.inputs['x'])``)
    Another node consumes the same value; nothing produces it. Treated
    exactly like ``unconnected`` -- the value is in
    ``final_input_config``, and the source instance is deliberately *not*
    part of identity.
    (Recording the source instance made the consumer fan out over sweep
    axes it never reads.)

``connected`` (``a.outputs['x'].connect(b.inputs['x'])``)
    An ancestor produced it. The value is excluded from both configs,
    because the producing instance's identity is already folded into
    ``process_id`` via ancestor hashing. Including it would double-count.

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

Resolved inconsistencies
------------------------

These three were found by the audit and have since been fixed. They are
kept here because the reasoning explains why the model looks the way it
does.

Finding 1: ``algo_id`` was wiring-dependent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Running the identical algorithm on the identical data used to produce a
different ``algo_id`` depending on whether the path came from the matrix
or from an upstream node -- and in the latter case the algo config held no
data at all. ``algo_ids`` were therefore not comparable across pipelines
that wired a computation differently.

The cause was structural rather than accidental: an unconnected input has
no ancestor to carry its identity, so ``final_algo_config`` was the only
place it could live. ``depends`` carried a TODO naming exactly this gap.

Fixed by splitting the concept. ``final_algo_config`` is now algorithm
parameters only; ``final_input_config`` holds the inputs nothing produced,
and ``depends['__inputs__']`` carries them into ``process_id``. All three
wirings now share one ``algo_id``, and two runs over different data still
get different ``process_ids`` and different output directories.

Finding 2: empty algo configs hashed identically everywhere
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``algo_id`` hashed ``final_algo_config`` alone, with the node name only a
*prefix* on the resulting string, so any two nodes with empty algo configs
shared the hash portion across unrelated pipelines. The prefix kept full
ids distinct, so this was not a correctness bug, but the hash portion was
unusable on its own.

Fixed by hashing the node name as part of the payload.

Finding 3: the two ends of a gather had to agree on a port name
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

While a gather is being resolved its source is not yet an ancestor of its
target, so ``group_by`` cannot name the source node. On a realistic
detection + segmentation pipeline the scorer has no ordinary edge to the
predictor, and the only formulation that compiled required both nodes to
use the *same port name* -- a coupling that showed up only at compile
time.

Fixed by letting a group key name itself differently on each side::

    group_by:
      - src: dataset_fpath      # what the predictor calls it
        dst: truth_fpath        # what the scorer calls it

All three wirings of the audit's detection/segmentation case now compile
to the same instance counts.

Still open
----------

A gather *joins* its sources against a target axis that must already
exist in the matrix. It could instead *partition* its sources and induce
one target instance per group, which would remove the need for the target
to be swept on the grouping key at all. That is a change to the
compilation model rather than to the gather API -- the compile loop is
per-row, with one instance per template per row, and inducing N targets
from one row breaks that invariant -- so it is recorded rather than
attempted.

Reproducing
-----------

.. code:: bash

    python dev/audits/param_identity_audit.py
