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
    Feed ``final_algo_config`` and therefore ``algo_id``. Each one has a
    port (:func:`ProcessNode.param_ports`), so it can be **wired** from
    another node: ``a.param_ports['x'].connect(b.param_ports['x'])``. A
    wired parameter carries a value, not a dependency -- the consumer does
    not inherit the producer's fan-out -- and the value outranks both the
    row config and the declared default.

``perf_params``
    Feed neither id. Deliberately not identity-bearing: changing
    ``workers`` must not invalidate a result.

``in_paths``
    Ports, so they **can** be connected. When nothing upstream produced
    the value it feeds ``final_input_config`` and reaches ``process_id``
    via ``depends``; it never reaches ``algo_id``. See `How an input is
    supplied`_.

``out_paths``
    Never identity-bearing on their own -- their location is *derived
    from* identity, so including them would be circular.

.. note::

    Where exactly the line between ``algo_params`` and ``in_paths`` should
    fall is **not settled**. The mechanical consequences above are exact;
    the principle for choosing is not.

    ``perf_params`` in particular is a name for a mechanism rather than a
    category: what it really means is *do not hash this*. Plenty of values
    have that property without being about performance -- a verbosity flag
    does not change results either, and calling it a performance parameter
    is a stretch.

    Sharing is no longer part of the distinction: both ``in_paths`` and
    ``algo_params`` have ports and can be wired. What remains is which
    identity a value belongs to -- the algorithm's or the data's -- and
    that line is clear in the mechanism if not always in a given case.

The three configs
-----------------

``final_algo_config``
    What algorithm the node runs: ``config`` minus ``out_paths``,
    ``perf_params`` and ``in_paths``, plus ``algo_params`` defaults, plus
    the value of any parameter whose port is wired (which outranks both).
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

The three kinds of edge
-----------------------

Every edge connects two ports, but they mean different things and only one
of them creates a scheduling dependency.

``a.outputs['x'] -> b.inputs['y']`` -- **production**
    ``a`` writes the file ``b`` reads. ``b`` waits for ``a``, and ``a``
    becomes an ancestor, so ``a``'s identity folds into ``b``'s
    ``process_id``. This is the only edge that orders execution.

``a.inputs['x'] -> b.inputs['y']`` -- **shared input**
    Both nodes read the same file; neither produces it. No dependency, no
    ancestry. ``b`` gets the value, and the value -- not the instance it
    came from -- is what enters ``b``'s identity. Declare a path once and
    wire it rather than restating it per consumer.

``a.param_ports['x'] -> b.param_ports['y']`` -- **shared parameter**
    Same semantics as a shared input, but for a value that is not data.
    It reaches ``final_algo_config`` and therefore ``algo_id``, because a
    parameter belongs to the algorithm's identity rather than the data's.
    Declare a label once and wire it rather than restating it per consumer
    in ``include``.

.. warning::

    A shared edge deliberately does **not** make its source an ancestor.
    Recording the source *instance* would make two consumers that read an
    identical value distinct, fanning the consumer out over sweep axes it
    never reads. Only the value is identity-bearing.

    The consequence worth internalising: a shared edge does not order
    execution. If ``b`` must wait for ``a``, connect an output to an input.

All three round-trip through the declarative YAML form; see
:doc:`yaml_pipeline_spec`.

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

How a parameter is supplied
---------------------------

``from the matrix``
    The row config gives it. Lands in ``final_algo_config``, reaches
    ``algo_id``.

``from a declared default``
    ``algo_params={'k': v}`` supplies it when the row does not. Same
    destination; the row outranks the default.

``wired`` (``a.param_ports['k'].connect(b.param_ports['k'])``)
    A peer supplies it. Outranks both the row and the default, so a
    consumer needs no entry of its own in the matrix or in ``include``.
    Carries a value, not a dependency.

Measured behaviour
------------------

From ``dev/audits/`` on a detection + segmentation pipeline where the
dataset path is simultaneously a predictor input, the gather key, and the
truth a scorer measures against:

===============================  =================  =======================
perturbation                     ``algo_id``        ``process_id``
===============================  =================  =======================
``detect.workers`` (perf)        unchanged          unchanged
``detect.thresh`` (algo)         detect only        detect + downstream
``score_det.iou_thresh``         score_det only     score_det + summarize
add a model to the cohort        **unchanged**      consumers only
sibling branch parameter         unchanged          that branch + summarize
relabel a wired parameter        source + consumer  source + consumer
===============================  =================  =======================

Three properties worth naming, because they are the point of the design:

* A perf param moves nothing.
* Adding a cohort member leaves every existing instance's ids untouched,
  so previously computed work stays valid. Only the consumers that now
  gather one more member move.
* A wired parameter moves the consumer's ``algo_id``, not just its
  ``process_id``. That is the intended difference from a shared input: the
  consumer really is running a differently-parameterized algorithm, where
  a node handed a different file is running the same one on other data.

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

``src``/``dst`` is a convenience, not the main answer. Where the two ends
share a value, wiring it -- an input for a path, a parameter port for a
label -- is usually better: the correspondence is then stated once, by the
edge, instead of restated by name in the gather spec.

Rejected: partition semantics
-----------------------------

During the audit a case was argued at length for changing gathers to
*partition* their sources -- grouping sources by the key and inducing one
target instance per group -- instead of joining against a target axis that
must already exist. It was rejected. It is recorded here with its
counter-evidence so it is not proposed again.

Four arguments were made for it. Each has a cheaper answer that already
exists:

*The sweep has to be declared once per consumer.*
    It does not. Wire the consumers' ports to the one that carries the
    value (``dev/audits/case_single_source_of_truth.py``). The dataset
    list appears once; ``segment.dataset_fpath``,
    ``score_det.truth_fpath`` and ``score_seg.truth_fpath`` are connected
    to it rather than restated.

*A restated axis can drift out of sync with the real one.*
    Only if you restate it. With one source of truth there is nothing to
    disagree.

*A gather's target cannot reach its source, so it cannot name it.*
    True, but a wire between them *is* a route. Aliasing gives the target
    the value directly, so nothing needs naming across the gather.

*Grouping by a non-path key forces ``include`` to restate every consumer.*
    This was the real gap, and it was closed by making algorithm
    parameters connectable rather than by changing how gathers compile.
    Wire the parameter and the value is declared once.
    ``dev/audits/case_shared_label.py`` measures it: with wiring the
    ``include`` block is 12 entries and stays 12 no matter how many
    consumers there are; without it, 24 entries for two consumers and 30
    for three.

The behaviour partitioning was meant to provide -- one target instance per
distinct group -- already falls out of value-based identity. Six ``detect``
instances produce three ``score`` instances because ``model_family`` takes
three distinct values and is identity-bearing. No compilation mode is
required.

Reproducing
-----------

.. code:: bash

    python dev/audits/param_identity_audit.py

.. note::

    Run the test suite with the environment's ``python`` on ``PATH``.
    Generated job scripts invoke ``python`` unqualified, so four tests that
    actually execute a scheduled job fail with an empty result table if it
    does not resolve -- which looks like an aggregation bug rather than a
    missing interpreter::

        # 4 failed
        /path/to/venv/bin/python -m pytest tests/

        # 99 passed
        PATH="/path/to/venv/bin:$PATH" /path/to/venv/bin/python -m pytest tests/
