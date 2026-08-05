Process Parameters, Result Identity, and Shared Values
=====================================================

This page describes the current execution model and the design priorities that
should guide changes to kwdagger.  It intentionally distinguishes the stable
user-facing ideas from historical parameter names and current implementation
details.

The center of the system
------------------------

Kwdagger turns parameterized definitions of existing command-line programs into
a static graph of shell commands and hashed result directories.  Its primary
products are:

* the command that each process should run;
* the ordering between commands that exchange produced artifacts;
* a stable result directory for each requested computation;
* an ``invoke.sh`` that can rerun an individual process without kwdagger;
* ``.pred`` and ``.succ`` links that preserve the realized result graph; and
* enough requested configuration to understand how the graph was constructed.

Kwdagger normally hands the commands to :mod:`cmd_queue`, most commonly using
the tmux backend, but execution is deliberately separable from kwdagger.  A user
should be able to inspect, copy, rerun, or invalidate pieces of the generated
result graph using ordinary shell tools.

Aggregation is an important optional consumer of this result graph.  It is not
the definition of the core execution model, and SMART/geowatch-specific
aggregation conventions should not unnecessarily constrain scheduling.

``ProcessNode`` and command construction
----------------------------------------

A :class:`~kwdagger.pipeline.ProcessNode` describes how one kind of program is
invoked.  Given its current parameters, one of its most important jobs is to
produce the complete command string and the paths associated with that command.

The default convention emits named command-line arguments, but users may
subclass ``ProcessNode`` when an existing program uses positional arguments,
subcommands, conditional flags, or another convention.  Kwdagger wraps existing
programs; it should not require those programs to become kwdagger-specific.

The historical parameter groups
--------------------------------

The current API divides node values into four groups.  These names are useful
for describing today's behavior, but they are historical and should not be
mistaken for a final ontology.

``in_paths``
    Data on disk that the program reads.  A value may be supplied directly or
    may name an artifact produced by another process.

``out_paths``
    Data written by the program.  At least one output should be designated by
    ``primary_out_key`` (or be the only output) so kwdagger has a practical
    completion check for the process.

``algo_params``
    Parameters that kwdagger currently assumes can affect the computation or
    its result.  They therefore participate in operational result identity.
    The name is imperfect: input data also affects a computation, and the
    boundary between executable parameters and data is not always conceptually
    clean.

``perf_params``
    Parameters that kwdagger currently assumes do not define a different
    logical result.  They are passed to the command but excluded from result
    identity.  The name is also imperfect: verbosity is not performance, and a
    nominally operational setting such as CPU count can sometimes perturb a
    result.  The current system deliberately treats those effects as negligible
    unless the user models them with an identity-bearing parameter.

These groups may eventually be renamed or generalized.  Near-term changes
should preserve compatibility rather than attempting a taxonomy redesign.

Requested, effective, and resolved values
-----------------------------------------

It is useful to distinguish several views of a process configuration.

Requested values
    Values explicitly supplied by the matrix, ``include`` rules, or another
    scheduling configuration.  ``job_config.json`` records **the canonical
    request kwdagger actually selected and submitted for that result
    identity** -- its requested experiment description and dotted parameter
    lineage.  Omitted defaults need not appear as explicitly specified values.

    It is *not* an exhaustive history of every matrix row that mapped to the
    identity, a transaction log across submissions, proof that the artifact is
    current, or a record of every way the computation was requested.  When
    duplicate rows differ, it describes the first one -- see
    :ref:`duplicate_policy`.

Effective command values
    Values used by ``ProcessNode`` to construct the command after declared
    defaults and shared-value connections are resolved.

Resolved runtime values
    Values the executable reports that it actually used, often through
    ``ProcessContext`` metadata.  Script defaults, normalization, environment,
    and runtime-derived settings may appear here.

Requested and resolved values need not be identical.  Aggregation can preserve
both.  However, the requested record should still make it possible to understand
which node-qualified values and sharing declarations produced the command.

Matrix correlation and pipeline relationships
----------------------------------------------

The matrix language describes an experiment campaign.  ``matrix``, ``include``,
and ``exclude`` are important mechanisms for independent variation and
conditional correlation.  ``submatrices`` are also useful, especially for
inheriting common parent values, but they began as a pragmatic extension and
should not be treated as the only or final way to express correlation.

A useful distinction is:

* a relationship that is always true for a pipeline belongs naturally in the
  pipeline definition; and
* a relationship that is specific to one campaign belongs naturally in the
  matrix or an ``include`` rule.

For example, if two ports always represent the same conceptual input, an edge is
clearer than repeating both values in every matrix.  A model-specific threshold
used only by one campaign is usually better expressed by ``include``.

Edge semantics
--------------

Produced artifact edges
~~~~~~~~~~~~~~~~~~~~~~~

``a.outputs['x'] -> b.inputs['y']`` means that ``a`` writes the artifact that
``b`` reads.  This relationship has all of the following consequences:

* ``b`` receives the concrete output path from ``a``;
* ``b`` must execute after that concrete instance of ``a``;
* the source process is part of the target's operational lineage; and
* the realized result graph records the relationship through ``.pred`` and
  ``.succ`` links.

A produced artifact edge is both data flow and execution dependency.

Shared known-value edges
~~~~~~~~~~~~~~~~~~~~~~~~

``a.inputs['x'] -> b.inputs['y']`` shares an already-known path.  The source
process does not produce that path.  Likewise,
``a.param_ports['x'] -> b.param_ports['y']`` shares an already-known parameter
value.

The intended semantics are:

* the value can be declared once and reused by consumers;
* sharing the value does not by itself require the source process to execute;
* the source process is not part of the consumer's process lineage merely
  because its port supplied the value; and
* unrelated sweep axes on the source node must not fan out the consumer.

These are configuration relationships, not produced-artifact relationships.
They may require an internal configuration-resolution order, but that order must
not be confused with execution order or persistent ``.pred`` lineage.

Treat the source as the canonical value.  A target should either omit its local
value or specify the same value.  Contradictory values should be rejected rather
than silently selecting one side.

Configuration resolution must also be independent of node insertion order and
of values left over from a previously configured matrix row.

Gather edges
~~~~~~~~~~~~

A gather edge is a static many-to-one produced-artifact relationship.  Kwdagger
selects a known collection of concrete source outputs, writes a path manifest,
and passes that manifest to an ordinary consumer input.

Gather is intentionally not runtime directory discovery.  The membership must
be fixed before the consumer runs, and it is obtained by compiling the complete
matrix before submission.  Every pipeline is compiled that way now, whether or
not it gathers; a gather-free matrix simply has no collections to resolve.
Gather needing whole-matrix knowledge is what made compilation necessary, but
it is a feature *of* a compiled matrix rather than a second way to schedule
one.  Gather is still a relatively new feature, so changes should be validated
against the established command, identity, and result-graph behavior rather
than treating its current implementation as settled architecture.

For grouping keys, prefer fully qualified ``node.parameter`` names.  An
unqualified name is convenient shorthand but can become ambiguous when a
pipeline grows.  A compiled or stored representation should use the qualified
name once resolution is known.

Operational result identity
---------------------------

The most important identity is the one used to decide whether requested work
can reuse an existing result directory.

``process_id``
    Names a concrete requested computation: this node's declared configuration
    and the effective values of its inputs.  It deliberately excludes lineage
    -- how a value was obtained does not change what is computed.  Used in
    hashed directory names and queue deduplication.  See
    :doc:`hashing_scheme` for the full invariant.

``algo_id``
    A current implementation component derived from a node's identity-bearing
    non-output parameters.  It can be useful for constructing ``process_id`` or
    for ad-hoc queries, but it does not yet have a strong, stable standalone
    semantic contract.  Documentation and new code should not elevate it above
    the observable result-reuse behavior.

The hashes are operational proxies, not content hashes or determinism proofs.
Kwdagger generally hashes declared values and paths, not the bytes stored at
those paths.  Equal IDs mean that kwdagger considers two requests reusable under
its model; they do not prove that an external file is unchanged or that the
program is deterministic.

When repeated stochastic realizations are desired, the user must include an
explicit seed, repetition, or enumeration parameter that changes identity, or
choose a different root directory.

.. _duplicate_policy:

Duplicate requests: the first one wins
--------------------------------------

Two matrix rows can produce the same ``process_id``.  They are one job, in one
result directory, and **the first row encountered is the one that runs**.
Later rows with that identity are duplicates.

This is normal, supported behavior rather than a fallback.  Anything two
equal-identity rows can disagree about is, by construction, something excluded
from identity -- ``perf_params``, Slurm options, ``__enabled__``, output-path
overrides, or how a value was delivered.  You chose that when you chose which
fields reach the hash.  Kwdagger runs your grid and records what it ran; it
does not try to protect you from a collision you allowed.

Matrix order selects the representative.  Compilation is deterministic for a
fixed ordered input, but reordering your matrix may select a different
representative, and that is intended.

If two requests must stay distinct, put the distinction into
identity-bearing configuration, or give them different output identities.

``duplicate_policy``
~~~~~~~~~~~~~~~~~~~~

Some users want to be told when duplicates differ.  One compile-time option
controls that, and *only* that -- execution is identical under the first two:

``first``
    The default.  The first request wins, silently.  Nothing is compared, so
    this costs nothing.

``warn``
    Identical execution, plus a warning naming the differing fields.

``error``
    Refuses the compilation, before any queue or result directory exists.  An
    extra constraint you asked for, not a stricter notion of correctness.

.. code-block:: bash

    kwdagger schedule --duplicate_policy=warn --params ...

.. code-block:: python

    compiled = pipeline.compile_configurations(rows, duplicate_policy='warn')

Worked example: differing performance parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``perf_params`` reach the command but not identity, so these two rows are one
process::

    row 1:  predict.workers = 4
    row 2:  predict.workers = 16

Under the default policy one job runs with ``--workers=4``, ``job_config.json``
records ``workers: 4``, and nothing is reported.  Under ``warn`` the same job
runs and a warning names ``perf_params``.  Under ``error`` compilation stops.

To run both, sweep something identity-bearing instead, or move ``workers`` out
of ``perf_params``.

Worked example: a produced and a manual path that are equal
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    row 1:  producer.output -> consumer.input     (resolves to /data/model.pt)
    row 2:  consumer.input  =  /data/model.pt     (typed directly)

The same configured path is the same effective data -- that is the governing
convention -- so both rows are one consumer identity.  The first row wins.
Provenance still records which one supplied the value; identity and scheduling
do not distinguish them.

Worked example: separate partial submissions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    call 1:  skip the producer, whose output exists; queue the consumer
    call 2:  rerun the producer

These are two independent operational requests, not one transaction.  The
consumer queued by call 1 is not retroactively made dependent on the producer
queued by call 2, and kwdagger does not reject either call for disagreeing
with the other.  ``skip_existing`` is per-submission state and never edits the
compiled request.

Kwdagger makes no guarantee about result data flow when individual nodes are
reinvoked and produce different outputs.  The design is to let a computation
run end to end, and then let you tweak things in the middle if you want.  If
you need data integrity, implement it; kwdagger is running your parameter grid.

The result directory graph
--------------------------

The hashed directory structure is a defining kwdagger feature.  A process
directory normally contains or participates in:

* one or more outputs, including a primary completion output;
* ``invoke.sh`` with the complete command needed to recompute the process;
* ``job_config.json`` with requested configuration and lineage information;
* logs and runtime metadata;
* ``.pred`` links to results this process consumed; and
* ``.succ`` links to results that consume this process.

The links make the graph navigable after scheduling has finished.  They also
make downstream invalidation practical: when a result is removed or judged
invalid, users can discover which later results depended on it.

Re-running ``invoke.sh`` reproduces the requested command.  A nondeterministic
program can still produce different bytes, so recomputation guarantees should
be described in terms of the request rather than bitwise identity.

Dotted parameter lineage
------------------------

Node-qualified dotted keys are the concise representation used to join a
process with the requested parameters of its lineage.  This is important even
outside the built-in aggregator because it permits result directories to be
loaded into a table where each concrete result is a row and each relevant
node-qualified value is a column.

A shared known value should therefore remain understandable in the requested
record even though its source process is not an execution ancestor.  Recording
the fully qualified source and target ports, and the value supplied, preserves
that distinction without inventing false process lineage.

Aggregation
-----------

``kwdagger aggregate`` is one way to query the accumulated result graph.  It can
load requested and resolved parameters, flatten dotted lineage, parse metrics,
and construct comparison tables.  Metrics, vantage points, region-aware
macros, and analytical parameter hashes are valuable capabilities, but many of
them originated in the SMART/geowatch workflow and are secondary to the core
scheduling and result-directory contract.

The result graph should remain useful to custom scripts, shell tools, notebooks,
databases, and future inspection interfaces that do not use the built-in
aggregator.

Current implementation names
----------------------------

The current code uses several intermediate properties:

``final_algo_config``
    Identity-bearing executable parameters after defaults and parameter sharing
    are resolved.  The current branch excludes ``in_paths`` from this mapping.

``final_input_config``
    The effective value of every input, whether supplied directly, forwarded
    from a peer port, or produced upstream.  A gathered input is the exception:
    its manifest path derives from ``process_id``, so the collection's contents
    enter identity through ``depends`` instead.

``final_perf_config``
    Non-identity values that still enter the command.

``depends``
    The payload used to construct ``process_id``.  It summarizes the node's own
    identity-bearing values, the effective value of every input, and gather
    membership.  It contains no producer identities: upstream reaches it
    through the values those producers supply.

These names document today's mechanics.  They should be evaluated by whether
they produce the right commands, reuse boundaries, result directories, and
lineage—not treated as the permanent conceptual foundation of kwdagger.

Design priorities for future changes
------------------------------------

When modifying parameters, hashing, matrix compilation, or edges, preserve these
priorities:

#. Produce a complete, inspectable static command plan before execution.
#. Keep execution separable from kwdagger and preserve useful ``invoke.sh``
   files.
#. Reuse exactly the work that the declared configuration and effective input
   values say is equivalent.
#. Preserve the navigable hashed directory graph and correct ``.pred`` / ``.succ``
   relationships.
#. Keep dotted requested lineage understandable without inventing process
   dependencies for values that did not require materialization.
#. Keep one path from a matrix to submitted work.  A batch and an interactive
   single row differ in how many rows are compiled and in nothing else; a
   second implementation of scheduling is how the two used to disagree about
   Slurm layering, normalization, and arbitration.
#. Treat aggregation as an important consumer, but do not let one historical
   reporting workflow define the entire execution model.
#. Prefer compatibility and concrete exhibitions over broad taxonomy redesigns.

Open questions
--------------

The following remain legitimate design questions rather than resolved doctrine:

* whether the four historical parameter groups should eventually be renamed or
  replaced;
* whether ``algo_id`` merits a stable public contract or should remain an
  implementation detail;
* whether whole-matrix compilation should remain gather-specific or eventually
  become the general scheduler path;
* how compute-resource declarations should interact with cmd_queue and Slurm;
* whether future experiment-level labels are needed beyond node parameters and
  matrix correlations; and
* how best to represent shared and gathered values in generic result-querying
  tools.
