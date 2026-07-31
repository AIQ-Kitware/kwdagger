Declarative YAML Pipeline Specification
=======================================

KWDagger pipelines have historically been defined in Python: you subclass
:class:`kwdagger.ProcessNode`, set class attributes, instantiate the nodes, wire
them together with ``connect``, and return a :class:`kwdagger.Pipeline`. The
``--pipeline`` argument to ``kwdagger schedule`` then points at the function that
builds that object, e.g.::

    kwdagger schedule --pipeline "my_module.pipelines.my_pipeline()" --params ...

This is flexible, but it has two drawbacks:

1. **Opacity.** The matrix keys (``predict.keyword``, ``evaluate.workers``, ...)
   only make sense if you already know the pipeline. The pipeline lives in one
   place (Python) and the thing that sweeps over it lives in another (the matrix
   YAML), so a reader cannot understand a ``--params`` file on its own.

2. **Arbitrary code execution.** Resolving ``"module.func()"`` ``eval``\ s a
   string. That is fine for a trusted local module but unsuitable for a hardened
   or sandboxed context where the pipeline definition is untrusted data.

The declarative YAML format addresses both. A pipeline is described purely as
data -- a set of nodes and the edges between them -- and that description can sit
**in the same document as the matrix**. Nothing about the scheduling, matrix
expansion, hashing, or aggregation machinery changes: the loader produces exactly
the same :class:`~kwdagger.Pipeline` / :class:`~kwdagger.ProcessNode` objects the
Python path produces.

.. note::

    The Python API is **not** deprecated. Anything the YAML format cannot
    express (custom ``command`` construction, bespoke result parsing, dynamic
    node generation) is still best done in Python. The YAML format is the
    recommended default for the common case where nodes are CLI tools invoked
    with ``--key value`` arguments.


Where a YAML pipeline can be specified
--------------------------------------

A YAML pipeline is accepted anywhere a pipeline is currently accepted. Both
``kwdagger schedule`` and ``kwdagger aggregate`` resolve their pipeline through
:func:`kwdagger.pipeline.coerce_pipeline`, which now understands three new
inputs in addition to a ``"module.func()"`` string:

* an inline mapping (a ``dict``) with ``nodes`` / ``edges`` keys,
* a path to a ``.yaml`` / ``.yml`` / ``.json`` file containing such a mapping,
* a :class:`~kwdagger.Pipeline` instance (unchanged).

So all three of the following are equivalent:

**1. Inline, in the same document as the matrix** (the recommended form -- the
sweep and the thing it sweeps over are co-located)::

    kwdagger schedule --params - <<'EOF'
    pipeline:
      nodes:
        predict:
          executable: "python predict.py"
          in_paths: [src_fpath]
          out_paths: {dst_fpath: predictions.json}
          algo_params: {keyword: great, case_sensitive: false}
          perf_params: {workers: 0}
        evaluate:
          executable: "python evaluate.py"
          in_paths: [true_fpath, pred_fpath]
          out_paths: {out_fpath: metrics.json}
      edges:
        - predict.dst_fpath -> evaluate.pred_fpath
        - predict.src_fpath -> evaluate.true_fpath
    matrix:
      predict.src_fpath: [reviews.json]
      predict.keyword: [great, awful, mediocre]
      predict.case_sensitive: [true, false]
    EOF

**2. A pipeline file referenced from the matrix** (keeps the command concise and
the pipeline reusable across matrices)::

    # params.yaml
    pipeline: ./pipeline.yaml
    matrix:
      predict.keyword: [great, awful, mediocre]

**3. Directly on the command line**::

    kwdagger schedule --pipeline ./pipeline.yaml --matrix ./matrix.yaml


Top-level schema
----------------

A pipeline document is a mapping with the following keys:

``nodes`` (required, mapping)
    Maps each node name to a *node spec* (see below). The key is the node's
    ``name``; do not also set ``name`` inside the spec.

``edges`` (optional, list)
    Connections between node ports. Omit when the pipeline is a single node or
    when nodes are connected implicitly by shared port names (not recommended;
    prefer explicit edges).


Node spec
---------

Every field of a node spec maps directly to a keyword argument of
:class:`kwdagger.ProcessNode`. The fields you will use most:

``executable`` (string, required)
    The shell command prefix for the node, e.g. ``"python predict.py"`` or
    ``"my-tool subcommand"``. ``command`` is accepted as an alias.

``in_paths`` (list of strings, or mapping)
    The input port names. As a list (``[src_fpath]``) the values are supplied by
    the matrix or by incoming edges. As a mapping (``{src_fpath: /abs/path}``)
    you may hard-code defaults.

``out_paths`` (mapping)
    Maps each output port name to the **filename** it produces inside the node's
    output directory, e.g. ``{dst_fpath: predictions.json}``. KWDagger prepends
    the hashed node directory; you only specify the leaf name.

``primary_out_key`` (string)
    Which output is the node's principal product (used for DAG chaining and for
    the generic result loader). Defaults to the sole entry when ``out_paths`` has
    exactly one key.

``algo_params`` (mapping)
    Algorithm knobs and their defaults. These **affect the node's hash**: two
    runs that differ in an ``algo_param`` get distinct output directories. This
    is what you usually sweep in the matrix.

``perf_params`` (mapping)
    Performance knobs (workers, device, batch size) and their defaults. These do
    **not** affect the hash, so changing them does not invalidate cached
    outputs.

``group`` (string)
    Optional subdirectory under the DAG root for organizing this node's outputs.

``slurm_options`` (mapping)
    Optional per-node SLURM options (``partition``, ``qos``, ``time``, ...).

The following fields are specific to the YAML format and support **evaluation
nodes** without requiring Python (see `Evaluation nodes`_):

``metrics`` (list of mappings)
    Metric metadata used by ``kwdagger aggregate``. Each entry:

    * ``metric`` (string, required) -- the metric name, relative to the node.
    * ``objective`` -- ``maximize`` (default) or ``minimize``.
    * ``primary`` -- mark the headline metric (bool, default false).
    * ``display`` -- show the column in the stdout table (bool, default false).
    * ``aggregator`` -- ``mean`` (default), ``gmean``, ``sum``, ``max``,
      ``min``, or ``ignore``.

``vantage_points`` (list of mappings)
    Pairs of metrics to plot against each other; each entry has ``metric1`` and
    ``metric2`` (fully-qualified, e.g. ``metrics.evaluate.accuracy``).

``result`` (mapping)
    Controls the generic result loader (see `Evaluation nodes`_):

    * ``metrics`` -- dotted path into the primary output JSON where the metric
      values live (default ``result.metrics``).
    * ``info`` -- dotted path to the ``ProcessContext`` list emitted by the tool
      (default ``info``; the last element is used). Set to ``null`` to skip
      context parsing.


Edges
-----

An edge connects an **output port** of one node to an **input port** of another
(the usual case), or an input port of one node to an input port of another
(input *forwarding* -- reusing the same data for two inputs). Each endpoint is
written ``node_name.port_name``. When resolving a port, the loader checks the
node's outputs first, then its inputs, so::

    edges:
      # output -> input: predictions feed the evaluator
      - predict.dst_fpath -> evaluate.pred_fpath
      # input -> input: the evaluator reuses the predictor's source as ground truth
      - predict.src_fpath -> evaluate.true_fpath

The string form ``"src.port -> dst.port"`` is preferred for readability. A more
explicit mapping form is also accepted and is easier to validate
programmatically::

    edges:
      - {src: predict, src_key: dst_fpath, dst: evaluate, dst_key: pred_fpath}
      # or the compact dotted variant:
      - {src: predict.dst_fpath, dst: evaluate.pred_fpath}

This mirrors the Python API exactly. ``predict.dst_fpath -> evaluate.pred_fpath``
is the data-level spelling of::

    nodes['predict'].outputs['dst_fpath'].connect(nodes['evaluate'].inputs['pred_fpath'])


Evaluation nodes
----------------

The one capability that genuinely needs Python in the class-based API is
``load_result`` -- the method an evaluation node implements to extract metrics
and context from its output for ``kwdagger aggregate``. The YAML format provides
a **generic** ``load_result`` so that the common case needs no Python:

1. It reads the node's primary output file (``out_paths[primary_out_key]``),
   which must be JSON.
2. If a ``ProcessContext`` list is present (``result.info``, default ``info``),
   the last element is parsed for resolved params, resources, and machine
   metadata -- the standard
   :func:`kwdagger.aggregate_loader.new_process_context_parser` conventions.
3. The metric values at ``result.metrics`` (default ``result.metrics``) are
   attached under ``metrics.<node_name>.<metric>``.

A complete evaluation node in YAML therefore looks like::

    evaluate:
      executable: "python evaluate.py"
      in_paths: [true_fpath, pred_fpath]
      out_paths: {out_fpath: metrics.json}
      result:
        metrics: result.metrics      # where evaluate.py writes its scores
      metrics:
        - {metric: accuracy,           objective: maximize, primary: true, display: true}
        - {metric: precision_positive, objective: maximize, display: true}
        - {metric: recall_positive,    objective: maximize, display: true}
      vantage_points:
        - {metric1: metrics.evaluate.accuracy, metric2: metrics.evaluate.precision_positive}

For tools whose output cannot be described this way, you can still mix
approaches: define the topology in YAML and subclass for the one node that needs
custom parsing, or fall back to a fully Python pipeline. The generic loader
covers any tool that follows the :class:`kwutil.ProcessContext` output
convention used throughout the tutorials.


Specialized nodes: referencing a Python class
----------------------------------------------

A node is *data* (name, executable, ports, parameters) plus *behavior* (how the
command line is built, how results are parsed). Data serializes to YAML
perfectly; behavior is code and cannot. Most nodes are pure data, but some need
behavioral overrides that the data schema cannot express:

* a **custom command** -- the standard invocation is
  ``executable --key=value ...``; a node that overrides ``command`` (or
  ``_make_argstr``) to emit positional arguments, a subcommand structure, or
  conditional flags cannot be described by ``executable`` + ``algo_params``
  alone;
* **bespoke result parsing** -- a ``load_result`` more involved than the generic
  loader (the ``load_result:`` dotted path above is the narrow form of this);
* **scriptconfig-derived groups** -- a node that sets ``params = SomeCLI`` to
  derive its port/parameter groups from a schema class.

Rather than serialize that code, **point at the class**. A node spec may set
``class`` to a dotted path naming a :class:`~kwdagger.ProcessNode` subclass; the
loader imports it (it does not ``eval`` anything) and instantiates it with the
YAML data. The class supplies behavior; the YAML supplies data::

    nodes:
      detect:
        class: "my_project.pipelines.WeirdCliNode"   # provides a custom command
        executable: "weird-tool"
        in_paths: [images]
        out_paths: {dets: detections.json}
        algo_params: {conf_thresh: 0.3}

This is the general case of the ``load_result:`` escape hatch -- where that
references a single method, ``class:`` brings the whole class, so one reference
covers a command override *and* custom parsing *and* metric metadata at once.

Rules:

* The referenced object must be a :class:`~kwdagger.ProcessNode` subclass.
* ``executable`` becomes optional (the class may define it, or override
  ``command`` so none is needed).
* The data-driven keys ``metrics`` / ``vantage_points`` / ``result`` /
  ``load_result`` are only accepted when the class is a
  :class:`~kwdagger.yaml_pipeline.YamlProcessNode` subclass (which knows how to
  consume them). A plain :class:`~kwdagger.ProcessNode` subclass is expected to
  define ``default_metrics`` / ``load_result`` itself; passing those YAML keys
  raises a clear error.

The trust model is the same as the ``load_result:`` hatch and the
``module.func()`` pipeline form: a named module is imported (so it must be on
``PYTHONPATH``), but no arbitrary expression is evaluated. A pipeline whose nodes
are all pure data or all reference classes is therefore as safe to load as the
classes it names.


Equivalence to the Python API
------------------------------

The loader is a thin, auditable translation. For each ``name: spec`` in
``nodes`` it constructs a :class:`~kwdagger.ProcessNode` (a
``YamlProcessNode`` subclass that adds the generic ``load_result`` and the
``metrics`` / ``vantage_points`` hooks) by forwarding the spec as keyword
arguments. For each entry in ``edges`` it resolves the two endpoints to their
:class:`~kwdagger.pipeline.IONode`\ s and calls ``connect``. It then returns
``Pipeline(nodes)``. No part of the spec is ``eval``\ ed, so the format is safe
to load from untrusted input.

Because the output is an ordinary ``Pipeline``, every downstream feature works
unchanged: matrix expansion (``predict.keyword: [...]``), the ``exclude`` /
``include`` / ``submatrices`` operators, ``skip_existing`` / ``cache``,
``__slurm_options__``, symlinking, and ``kwdagger aggregate``.


Serialization and round-tripping
--------------------------------

The mapping is also the *output* of serialization, not just the input.
:meth:`kwdagger.Pipeline.to_yaml_spec` (a.k.a.
:func:`kwdagger.dump_yaml_pipeline`) converts **any** pipeline -- including one
built entirely in Python -- back into this declarative form:

* pure-data nodes are emitted as data;
* custom :class:`~kwdagger.ProcessNode` subclasses are emitted as ``class:``
  references (their behavior is code, so it is referenced, not serialized);
* edges are recovered from the connected input ports.

Feeding the result back through :func:`~kwdagger.load_yaml_pipeline` reconstructs
an equivalent pipeline -- same node IDs, same commands, same node classes.

This is what lets a run be **self-describing**. ``kwdagger schedule`` serializes
the pipeline it ran to ``<root_dpath>/_kwdagger_schedule/most_recent_run.json``,
and ``kwdagger aggregate`` (whose ``--pipeline`` defaults to ``auto``) recovers
it from the target directory -- so you do not repeat the pipeline when
aggregating, whether it was defined inline as YAML or as a ``module.func()``
Python builder. The boundary is the same as everywhere else: a node class
referenced by ``class:`` must be importable (so a class defined in ``__main__``
or a local scope cannot be serialized; ``to_yaml_spec`` raises a clear error
rather than emit a dangling reference), and reconstruction needs that class on
``PYTHONPATH`` -- exactly what re-running the Python builder would need too.


Worked example
--------------

See the ``twostage_pipeline`` tutorial, which ships the same two-stage
keyword-sentiment pipeline in both forms: ``pipelines.py`` (Python) and
``pipeline.yaml`` (declarative). They produce equivalent command queues --
identical node IDs, output directories, and resolved parameters -- differing
only in how each node's CLI is invoked (the YAML form uses ``python -m
example_user_module.cli....`` rather than an absolute script path, since a static
document cannot compute one).

Compile-time gather edges
-------------------------

A gather edge connects many configured instances of one source output to one
collection-valued target input. Membership is resolved when the entire parameter
matrix is compiled; no jobs are discovered or created at runtime.

The mapping edge form accepts a ``gather`` specification::

    edges:
      - src: train.checkpoint_fpath
        dst: build_ensemble.checkpoints_fpath
        gather:
          group_by: [algorithm, seed]
          order_by: [fold]
          require: all_success

``group_by`` (required)
    Keys identifying which slice of the sweep an instance belongs to. For each
    concrete target instance, kwdagger selects all source instances whose keys
    resolve to equal values. This follows the dataframe group-by intuition: one
    target collection is formed per distinct group. Source parameters not listed
    in ``group_by`` vary within the collection. An empty list gathers every
    source instance into one collection for each otherwise-distinct target
    instance.

    Prefer the **qualified** ``<node>.<param>`` form, which names the node the
    value lives on exactly as a matrix key does::

        group_by: [prepare.dataset]

    A qualified key is resolved on the named node -- the instance itself if the
    names match, otherwise its ancestor of that name -- looking in that node's
    algorithm parameters, then its input ports, then its output ports. Input and
    output paths are eligible deliberately: a connected path is excluded from a
    node's algorithm config so hashing does not double-count identity already
    captured by ancestor hashing, but that path is precisely the upstream
    identity a gather wants to group on.

    Because the key is resolved on the sources *and* on the target, it must name
    a node reachable from both -- in practice a common ancestor. Naming the
    target itself will not resolve on the sources, which are upstream of it.
    Note also that a gather's own source does not become an ancestor of its
    target until that gather is resolved, so the target needs an independent
    edge to whatever it groups by.

    When the two ends name the same value differently, say so rather than
    renaming a port::

        group_by:
          - src: dataset_fpath
            dst: truth_fpath

    ``src`` is resolved on the source instances and ``dst`` on the target. This
    matters when the target has no ordinary edge to the source -- a scorer
    gathering predictions, say -- because a qualified ``<node>.<param>`` cannot
    name a node the target cannot reach.

    An unqualified name stays supported. It resolves against the node itself and
    then against its ancestors, and raises if ancestors disagree rather than
    guessing. Prefer qualifying: declaring the same parameter on several nodes
    so an unqualified key resolves creates a second, independent sweep axis,
    and kwdagger takes the product of the two -- producing instances whose
    declared parameter value and actual upstream input disagree.

``order_by`` (optional)
    Source algorithm parameters used to order the paths in the generated
    manifest. When omitted, members are ordered by source process ID. Declare
    this whenever the consumer gives semantic meaning to input order.

``require``
    Completion policy. The initial implementation supports only
    ``all_success``: every statically selected source job is a dependency of the
    target, and the target runs only after all of them succeed.

The target CLI receives a normal key/value input whose value is a generated
newline-delimited path manifest::

    python build_ensemble.py \
        --checkpoints_fpath=.../_gather/checkpoints_fpath.txt \
        --ensemble_fpath=.../ensemble.pkl

The manifest creation is embedded in the target's complete executable shell
command and ``invoke.sh`` as a quoted heredoc. Heredoc contents are script
input, not command arguments, so executing the script does not consume
``ARG_MAX`` in proportion to collection size. Paths containing newlines are
rejected because the default format is one path per line.

Serial and tmux commands are already emitted through script files and may carry
the heredoc inline. Slurm ordinarily passes a job through ``sbatch --wrap``;
placing a large heredoc there would merely move the argument-length problem to
the Slurm submission command. Gathered Slurm consumers are therefore submitted
as a short ``bash <node>/invoke.sh`` command. The visible ``invoke.sh`` and
``job_config.json`` artifacts are materialized during queue compilation, and
the manifest itself is still generated by ``invoke.sh`` at execution time.

Logical member process IDs and gather policy participate in the target hash;
absolute cache-root paths do not define membership. Gather pipelines must be
compiled across the complete parameter matrix before submission; submitting a
single configured template row is rejected.

Logical Process and IO graph diagnostics insert display-only gather markers and
mark the receiving input as collection-valued. After compilation, kwdagger also
prints concrete edge cardinalities, such as ``gather 3:1`` and
``fan-out 1:2``, before queue submission. These markers describe graph
semantics; they are not executable nodes.

The equivalent Python API is::

    source.outputs['checkpoint_fpath'].connect(
        target.inputs['checkpoints_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=['algorithm', 'seed'],
            order_by=['fold'],
            require='all_success',
        ),
    )

See :doc:`../tutorials/gather_cross_validation/README` for a complete fan-out,
gather, and second fan-out example.
