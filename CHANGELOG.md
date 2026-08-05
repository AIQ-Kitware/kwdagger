# Changelog
We [keep a changelog](https://keepachangelog.com/en/1.0.0/).
We aim to adhere to [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.3.1 - Unreleased

### Changed

* **There is one scheduling path.** kwdagger used to schedule a batch two ways:
  a pipeline with a gather compiled the whole matrix and submitted a
  `CompiledPipeline`, while a pipeline without one configured and submitted a
  row at a time. Whether a pipeline contained a gather -- a *compilation*
  feature -- decided which execution architecture ran.

  This is removed. `build_schedule` compiles the matrix and submits the
  compiled graph, for every pipeline, and `Pipeline.submit_jobs` compiles the
  row it was configured with and submits that, so an interactive submission is
  a matrix of one rather than a second implementation.

  It is worth being explicit about why, because the individual symptoms were
  each fixed once already during 0.3.x and the list kept growing. Stale row
  state, divergent Slurm layering, and divergent normalization boundaries were
  one defect: **two authorities for one question, with nothing forcing them to
  agree.** Neither path was wrong; having two was.

* `build_schedule` now returns a `CompiledPipeline` for every pipeline, where
  it previously returned the template `Pipeline` unless a gather was present.
  A compiled pipeline holds the concrete processes, so `nodes` is keyed by
  `process_id` rather than by name. `CompiledPipeline.nodes_by_name` is added
  for the name lookup `Pipeline.node_dict` used to serve; it maps to a *list*,
  because a matrix expands one template into many processes.

* `submit_jobs` reports `node_status` keyed by `process_id` rather than by node
  name, and a batch now returns one matrix-wide summary instead of one summary
  per row. A name cannot key a compiled matrix without silently overwriting
  siblings, and `process_id` is the key `queue.named_jobs` and
  `CompiledPipeline.nodes` already use -- `compiled.nodes[pid].name` recovers
  the name.

* The effective Slurm request has one resolver,
  `kwdagger.pipeline.resolve_slurm_options`, and one home,
  `node.effective_slurm_options`. It was previously computed in three places
  from three different subsets of its four layers, which is why
  `node.slurm_options` meant "node-level options" on one scheduling path and
  "node-level plus row-global" on the other. That attribute now means the
  node's own layers on both. `kwdagger.pipeline._runtime.submit_jobs` no
  longer takes a `slurm_options` argument, and `CompiledPipeline` no longer
  carries a pipeline-wide copy.

* The scheduler crosses the configuration normalization boundary once, before
  the parameter matrix is expanded rather than after. Expanding first let
  `Path('/a')` and `'/a'` count as two points on an axis and then compile to
  one process, so the reported cardinality contradicted the compiled graph.

* Compiling a pipeline without gather connections is allowed;
  `compile_configurations` used to reject it. This is what forced the second
  scheduling path to exist, and nothing in the compiler was ever
  gather-specific.

### Fixed

* Configuring a batch and then inspecting the pipeline reported only the last
  row, because the row-at-a-time path reused one mutable node per name. A
  compiled pipeline clones per row, so every row is still described afterwards.


## Version 0.3.0 - Released 2026-08-04

### Added

* Connectable algorithm parameters. `ProcessNode.param_ports` exposes a port
  per declared `algo_param`, and `a.param -> b.param` (Python or YAML) shares
  an already-known value without creating an execution dependency.
* `GatherSpec.group_by` accepts `{'src': ..., 'dst': ...}` pairs so each side
  may name the same identity in its own vocabulary, and gather group keys may
  be node-qualified (`prepare.dataset`).
* `Pipeline.config_graph` records configuration-resolution ordering separately
  from `proc_graph`, which stays purely about execution.

### Changed

* The minimum `cmd_queue` is now 0.3.2, for its kwconf-native
  `CmdQueueConfigMixin`.
* **kwdagger is kwconf-only; `scriptconfig` is no longer a dependency.** Every
  CLI and config in the package -- `schedule`, `aggregate`, the modal entry
  point, and the demo CLIs -- is a `kwconf.Config`, and `scriptconfig` is gone
  from `requirements/runtime.txt`. The two libraries are close enough that the
  port is mechanical: `scfg.DataConfig` -> `kwconf.Config`, `scfg.Value` ->
  `kwconf.Value` with the same `nargs` / `position` / `isflag` / `type`
  keywords and the same flag semantics, and `scfg.ModalCLI` -> `kwconf.ModalCLI`
  with the same subclass form.

  Two call-signature differences affect user code that subclasses or invokes
  these CLIs: `Config.cli()` takes `argv=` rather than `cmdline=`, and its
  sys.argv toggle is a `bool` rather than scriptconfig's `int` -- so
  `main(argv=1)` becomes `main(argv=True)`. The tutorials and examples are
  updated accordingly.
* `ProcessNode._from_scriptconfig` is now `ProcessNode._from_kwconf`, taking a
  `kwconf.Config` subclass. It remains the one-class-per-node helper; defining
  `params` on the node class is still preferred.
* The `scriptconfig_pipeline` tutorial is now `kwconf_pipeline`.
* **`Pipeline` takes a sequence of nodes, not a mapping.** A node knows its own
  name, so the `{name: node}` form was a second place for that name to live and
  a second place for it to disagree: every graph, dotted config key, and result
  directory keys on `node.name`, while `node_dict` returned the caller's keys.
  `Pipeline.node_dict` builds the name index from the nodes themselves, and is
  what to ask for a name lookup -- it always has been, for list-built
  pipelines. Passing a mapping now raises `TypeError` naming the one-line
  migration, `list(nodes.values())`.

  This also fixes aggregation, which indexed `dag.nodes[name]` directly and so
  raised `TypeError: list indices must be integers` on any pipeline built from
  a list. It asks `node_dict` now, and works for either.
* `Node.connect()` no longer takes `param_mapping`, `src_map`, or `dst_map`.
  A node-level connection now means exactly one thing: every output whose name
  is also an input name of the target is connected, and nothing else. Renaming
  both sides into a shared intermediate namespace was more work than naming the
  ports directly, which is the only reason the convenience exists.
* A port edge is now validated. The three that mean something --
  `output -> input`, `input -> input`, `param -> param` -- are accepted and
  every other pair raises `TypeError`. An `output -> output` edge was
  previously recorded silently and then meant nothing to anything that reads
  the graph; YAML could produce one, since `_resolve_endpoint` prefers outputs
  when a node has an input and an output of the same name.
* **Process identities change in this release.** Input paths moved out of
  `final_algo_config` into `final_input_config`, `algo_id` now hashes the node
  name as part of its payload, and dependency-only edges contribute a
  predecessor `process_id`. Existing result directories will not be reused.
* An `input -> input` edge is an alias, not a dependency: it no longer creates
  a scheduling edge, process lineage, or `.pred` / `.succ` links.
* A declared `in_paths` default no longer overrides a connected upstream
  output; connections outrank defaults.

* `kwdagger/pipeline.py` is now the package `kwdagger/pipeline/`. Every
  documented import is unchanged -- `from kwdagger.pipeline import Pipeline,
  ProcessNode, GatherSpec, coerce_pipeline, ...` all resolve exactly as before,
  as do the private names the repository already imported from there. The
  implementation is split into private submodules layered in one direction:
  `_shell` / `_slurm` (leaves), `_runtime` (queue submission), `_connections`
  (ports, edges, gather specs), `_process` (`ProcessNode`), `_compile`
  (full-matrix compilation), `_logical` (`Pipeline`, graphs, coercion).
  `tests/test_import_compat.py` pins both the import surface and the direction.
* Queue submission moved out of `Pipeline` into `kwdagger.pipeline._runtime`.
  `Pipeline.submit_jobs()` and `CompiledPipeline.submit_jobs()` now call one
  shared function that takes the process graph, instead of a compiled pipeline
  being cast to a `Pipeline`. Both public methods behave as before.
* `demodata_pipeline` and `demo_pipeline_run` moved to `kwdagger.demo.demodata`.
  They remain importable from `kwdagger.pipeline`, so
  `--pipeline=kwdagger.pipeline.demodata_pipeline()` still resolves.

### Removed

* `kwdagger._cmd_queue_compat`. It was a local kwconf reimplementation of
  cmd_queue's CLI boilerplate, existing only so kwdagger could move to kwconf
  while still supporting cmd_queue <= 0.3.1, whose `CMDQueueConfig` is
  scriptconfig-based and cannot host kwconf fields. The minimum is now
  cmd_queue 0.3.2, which ships `CmdQueueConfigMixin` natively.
* `CompiledPipeline.node_dict`. It was an alias for `CompiledPipeline.nodes`,
  which is keyed by `process_id` rather than by name -- a compiled pipeline may
  hold several concrete instances of one template. Sharing an attribute name
  with `Pipeline.node_dict`, which *is* name-keyed, made the two look
  interchangeable when they are not. Use `.nodes`. `CompiledPipeline.nodes` is
  now derived from `proc_graph` rather than snapshotted beside it, so the graph
  is the one container.
* `ProcessNode.pred` and `ProcessNode.succ`. Only ports carry graph edges; a
  process's relationships are derived from its ports'. The process-level lists
  were written alongside the port edges and read by nothing (the compiler
  already cleared them on every clone), so they were an empty list that looked
  like an answer. `predecessor_process_nodes()`, `successor_process_nodes()`,
  and `Pipeline.print_graphs()` are the ways to ask. `ProcessNode.__nice__` no
  longer reports them.
* The historical template-output-discovery subsystem, which no longer had a
  consumer in scheduling, compilation, aggregation, tests, examples, or docs,
  and which overlapped with result loading: `ProcessNode.find_template_outputs`,
  `OutputNode.matching_fpaths`, and `glob_templated_path`. Use the aggregation
  loader to read completed runs. (`aggregate_loader` already carried its own
  independent implementation of the path-matching part.)
* `ProcessNode.outputs_exist`, an unused alias for `ProcessNode.does_exist`.
* `ProcessNode.template_root_dpath` and `ProcessNode.final_root_dpath`. The
  root has no template components, so both reduced to `root_dpath`. The
  `templates['root_dpath']` and `final['root_dpath']` records are unchanged.
* `bash_printf_literal_string`, which nothing called; `bash_heredoc_write_command`
  is how kwdagger writes literal text into a generated script.

### Fixed

* A node-level `a.connect(b)` paired the matched names *positionally*, over
  two dictionaries that each kept their own insertion order, so a producer and
  a consumer that enumerated the same names in a different order had their
  ports silently crossed. It needs two or more shared names to bite -- with one
  match, positional and by-name pairing coincide -- which is why it survived: a
  set-declared `in_paths` makes the order arbitrary. Pairing is now by name.
* `connect()` resolves and validates every pair before adding any edge, so a
  call that raises part-way through no longer leaves a half-connected graph.
* Requests sharing one `process_id` are now arbitrated against the
  requested-experiment record itself -- what would be written to
  `job_config.json` -- rather than against a summary of where each input's
  value came from. The summary covered produced origins only, so two requests
  could agree on identity, on prerequisites, and on producer origins while
  still asking for different things: which of two input aliases supplied the
  value and which was left unresolved, which parameter port forwarded a shared
  algorithm value, or a gather membership. Whichever request arrived first
  decided the persisted record, so reversing a matrix changed what was written.
  Delivery still does not reach `process_id`; the conflicting requests are the
  same computation and still hash alike.
* Slurm options layered differently on the two scheduling paths, so adding an
  unrelated gather to a pipeline could change what resources one of its nodes
  asked for. The compiler *substituted* a row-global `__slurm_options__` for a
  node's own instead of merging them: a node with any local option dropped
  every row-global key, and a node with none took the row-global mapping at
  node precedence. The four layers -- pipeline base, matrix-row global, node
  declared default, that row's per-node override -- are now defined once in
  `kwdagger.pipeline._slurm.layer_slurm_options` and combined key-wise by
  every site that combines them.
* The `schedule` CLI injected a parameter file's top-level `slurm_options` into
  every matrix row, guarded by a check for `slurm_options` rather than
  `__slurm_options__`, so it silently overwrote a row that requested its own.
  Those options are the pipeline base now, which is where the guard was trying
  to put them, so the injection is gone.
* The two scheduling paths crossed the configuration normalization boundary at
  different points. Full-matrix compilation read reserved keys and routed
  values from rows that had not been normalized at all, while
  `Pipeline.configure` normalized the row only *after* extracting
  `__slurm_options__` from it. So whether a mapping key was accepted, and
  whether a reserved key could even be seen, depended on which path a row took.
  Both now normalize the complete row first, before anything reads a reserved
  key out of it, and `coerce_slurm_options` normalizes too -- options arrive by
  four routes and only some of them have crossed the boundary already.
* A matrix row that omitted top-level `__slurm_options__` inherited the
  previous row's value, because `Pipeline.configure` defaulted to its own
  current value rather than to a baseline. "Explicit options" and "no options"
  were therefore indistinguishable in row order, which the new arbitration
  depends on telling apart. `Pipeline` now keeps `_base_slurm_options` as the
  persistent pipeline-wide default and resets to it on every `configure`, the
  way `ProcessNode` already did. As part of this, the `schedule` CLI's
  `--slurm_options` now reach ordinary pipelines: they were applied only on the
  gather path and silently dropped on the row-at-a-time one.
* The requested-record serializer used a `default=str` fallback that the writer
  of `job_config.json` does not have, so a value only one of them could
  serialize passed arbitration and failed at write time -- the reader
  disagreement the record comparison exists to remove. Both now refuse the
  same things.
* Requests sharing one `process_id` were arbitrated only on state stored on the
  node, so an ordinary pipeline never compared top-level `__slurm_options__`
  (which `Pipeline.configure` keeps on the pipeline and never copies onto a
  node) or the `log`, `enable_links`, `write_invocations`, and `write_configs`
  arguments to `submit_jobs`. A duplicate request returns before any of those
  is applied, so two rows requesting `gres: gpu:1` and `gres: gpu:4` submitted
  one job whose resources depended on row order, and submitting first with
  `write_configs=False` and then with `write_configs=True` wrote no
  `job_config.json` at all. The snapshot now takes pipeline-wide Slurm options
  from the submitter and merges them with the node's own, and compares the
  bookkeeping flags. `skip_existing` is deliberately not compared: it selects
  which requests are made rather than what a request asks for. The full-matrix
  path was unaffected, because the compiler copies a row-global value into each
  node configuration.
* **Configuration now has one internal representation, established when it is
  coerced:** every path-like object is a string and every mapping key is a
  string. The boundary is the new `kwdagger.pipeline._config_values`, and it
  covers row overrides, the pipeline's own requested row, and declared
  `in_paths` / `out_paths` / `algo_params` / `perf_params` defaults -- a
  default reaches identity and `job_config.json` by the same route an override
  does.

  A Python caller may still pass `pathlib.Path` values -- including as mapping
  keys -- and `os.fspath` converts them, converting spelling only, so a
  relative path stays relative. A `PathLike` whose `__fspath__` returns `bytes`
  is rejected rather than decoded with a guessed encoding.

  **Configuration mappings must now have string keys, including mappings loaded
  from YAML.** This is an intentional domain rule: mapping keys are variable
  identifiers. YAML decodes `0:`, `true:`, and `null:` into non-string Python
  keys, so a spec such as `class_weights: {0: 1.0, 1: 2.5}` is rejected with a
  `TypeError` where it previously became the JSON keys `"0"` and `"1"`. Two
  keys normalizing to the same string are reported as a collision.

  Previously the conversion was left to whichever serializer saw the value, and
  they disagree: a `PathLike` key raised `TypeError` when the requested record
  or `job_config.json` was written, an `int` key was renamed silently by
  `json.dumps` so the identity payload and the persisted record disagreed about
  what the configuration was, and a mixture of key types could not be sorted
  for comparison at all.
* Canonicalizing a mapping's keys relative to the cache root is many-to-one, so
  two distinct keys could land on one canonical key and the rebuilt dictionary
  silently dropped an entry -- leaving a mapping whose hashed payload matched a
  genuinely smaller one while the commands still differed, which no arbitration
  can catch across two separate schedules. A collision now raises a `ValueError`
  naming both keys. A `PathLike` mapping key is canonicalized like the
  equivalent string, which it previously was not, so such a key no longer keeps
  the absolute cache root in the hash.
* `compile_configurations` raised `KeyError` when a node forwarded a value to
  one of its own ports, which `Pipeline.configure` has always allowed.
* A wired algorithm parameter whose source never resolved a value wrote a
  literal `--<key>=None` onto the consumer's command line, and overwrote the
  consumer's own declared default. An unresolved port now supplies nothing.
* Shared values disappeared from a descendant's `job_config.json`. Since the
  source of a shared value is deliberately not lineage, nothing downstream
  mentioned the value at all, so aggregation lost it.
* The IO graph listed every declared algorithm parameter, including unwired
  ones, burying the data flow it exists to show. Only wired ports appear.
* `job_config.json` collapsed a gather's several concrete ancestors into one.
  Same-named instances that disagree on a dotted key now record a collection
  aligned to a new `__instances__.<node>` ordering, and a gather consumer's
  membership is propagated to descendants as `__gather__.<node>.<port>`.
* An input alias erased the producer standing behind it. `producer.output ->
  middle.input -> consumer.input` gave the consumer the produced path with no
  guarantee the producer had run: no queue ordering, no `.pred` / `.succ`
  links, no lineage. Producers are now recovered through alias chains and are
  real dependencies of the final consumer, while the process that lent the
  input is still not one. An alias with no produced origin is unchanged and
  stays configuration-only.
* **Process identity describes computation, not lineage.** A consumer whose
  effective input values are equal now has the same `process_id` whether those
  values came from a producer connection, an alias chain, or configuration by
  hand, and therefore shares a result directory. `depends` hashes this node's
  own `algo_id`, every input's canonical effective value, a gathered input's
  ordered member paths, and explicit ordering edges. It no longer contains
  producer process IDs, producer algorithm IDs, port names, or `source_kind`.

  This does not weaken invalidation: a produced path contains the producer's
  `process_id`, so reconfiguring a producer moves the path and the consumer
  follows. If a producer change leaves the output path unchanged, kwdagger
  treats the consumer's input as unchanged, exactly as it does for a stable
  hand-written path. It does not assert byte equality -- kwdagger's data
  identity is value/path based unless the user supplies an explicit content
  identifier. Producer lineage remains fully available in provenance and in
  scheduling.

  **Process identities change again in this release** for any node with a
  produced or gathered input.
* **Removed ancestor-ID substitution from path templates.** `condensed` no
  longer imports upstream nodes' ids, so `node_dpath` and output templates may
  use only the node's own `{<node>_id}` / `{<node>_algo_id}`. Naming another
  node's id let a process this one does not read decide where its results are
  written, which contradicts the identity invariant; such a template now raises
  an error explaining the migration. The placeholders never worked in practice
  -- a node configures itself during construction, before any connection
  exists, so an ancestor id raised `KeyError` there first.
* Compilation now asserts that matrix rows collapsing onto one `process_id`
  agree on their finalized commands, node directories, output paths, and
  setup/teardown, rather than letting the first row decide what runs.
* **Configured execution now follows the effective dependency set.** A
  predecessor is not merely an ordering hint -- a disabled or missing one
  suppresses its successor -- so gating a command on a producer it never reads
  could silently skip valid work. `will_exist` gating, queue dependencies,
  `.pred` / `.succ` links, and the compiled execution graph all ask
  `effective_predecessor_process_nodes()`. `Pipeline.proc_graph` stays
  structural: it answers which dependencies are *possible* before anything is
  configured, and `Pipeline.effective_execution_graph()` is its configured
  counterpart.
* Full-matrix compilation could make an overridden consumer's execution depend
  on matrix row order. Rows that compile to one consumer may be wired behind
  different producers; the first row's structural predecessors survived
  canonicalization, so reversing the matrix decided whether the consumer ran.
  Both the compiled graph and the recorded provenance are now row-order
  independent.
* An input's *effective* source is now resolved with the same precedence as
  its value (gather, explicit, forwarded, produced, default) wherever identity
  and provenance ask where a value came from, rather than reporting every
  structurally reachable producer. The *template* graph stays structural,
  because it is built before anything is configured and so cannot know what an
  override will resolve to; configured scheduling is effective, as the entry
  above describes. `ProcessNode.effective_predecessor_process_nodes` and
  `effective_ancestor_process_nodes` expose the stricter answer.
* An explicit value configured onto a connected input did not reach the
  consumer's identity. Explicit values outrank producers -- documented,
  deliberate precedence -- so the command read the override while identity
  still pointed at the producer and the override appeared nowhere. Two rows
  overriding the same input with different paths produced different commands,
  one `process_id`, and one result directory; compilation kept whichever row it
  saw first. Identity, provenance, and `final_input_config` now resolve the
  *effective* source of an input using the same precedence as value
  resolution, rather than every structurally reachable producer. This applies
  through a whole alias chain: an override on an intermediate port hides the
  producer wired behind it from everything downstream, not only from its
  immediate neighbour.
* A producer whose output was overridden before it was read still contributed
  its `algo_id` to its consumer's `process_id`, because identity walked the
  structural ancestry. Sweeping such a producer fanned out identical consumer
  jobs differing only in result directory. Identity now walks the effective
  ancestry.
* `job_config.json` recorded both that a producer supplied an input and that
  the command read an explicit override of it. The wiring is still recorded --
  it is part of what was requested -- but a source that did not supply the
  value is now marked `supplied: false`. Such a record names the wired *port*
  rather than a concrete instance, because several matrix rows can wire
  different producer instances into one deduplicated consumer.
* `Pipeline.proc_graph` described a forwarded gather manifest as
  configuration-only. The compiled graph had the edge, so this was never a
  race, but the logical graph a user reads before compiling was wrong. A
  template port knows it has a gather connection long before it knows the
  membership, which is enough to know the edge exists.
* Template lineage queries answered as though the pipeline had no edges.
  A `ProcessNode` memoizes `predecessor_process_nodes` and
  `ancestor_process_nodes` during its own construction, before it is connected
  to anything, and only `configure` cleared that cache; building the graph now
  clears it too.
* Forwarding a gathered input to another port of the same process raised
  `RecursionError`. It now raises a `ValueError` naming both ports and saying
  why: the manifest is written by that process, so it cannot also be one of
  its own inputs.
* A consumer that read a produced value through an input alias did not record
  *which* producer instance made it, so two producers running one algorithm
  over different data were indistinguishable in the consumer's record. The
  alias-recovered producer is now named in provenance as `origins` on the
  `__input__.<port>` record, and is a real scheduling dependency of the
  consumer. It does not enter the consumer's identity: the produced path
  already contains the producer's `process_id`, so two consumers reading
  different files hash differently through the value they read.
* Aliasing a gathered input handed out the path to a manifest with no
  dependency on the job that writes it, so the borrower could run first and
  read a file that did not exist yet. A gathered port's owner is now a real
  producer of that manifest: it is a scheduling dependency of anything
  borrowing the path, and part of that borrower's identity.
* A descendant with several same-named gathering ancestors kept only one of
  their memberships: every `__gather__.<node>.<port>` record shared one key, so
  the last instance visited overwrote its siblings. Each record now names the
  concrete consumer it describes (`consumer_process_id`), and several
  instances write a list aligned to `__instances__.<node>`. A single instance
  still writes a single record.
* A gather could not group on a structured parameter value. Grouping values are
  compared in sets, and only paths and sequences were canonicalized, so a
  mapping or set value raised `TypeError: unhashable`.
* Gather provenance exposed the internal `(src, dst)` tuple for a mapped
  `group_by` entry. It now writes the public `{'src': ..., 'dst': ...}` form
  via `GatherSpec.to_dict()`, so a recorded `group_by` round-trips through
  `GatherSpec.coerce()`.
* Shared-value provenance reported an unresolved source as `value: None`,
  making it indistinguishable from an explicitly requested `None`. An
  unresolved source now records `unresolved: true` and no `value`, and a value
  the target took from its own declaration default is no longer recorded as
  requested configuration.


## Version 0.2.6 - Released 2026-07-30

### Added

* Compile-time gather edges via `GatherSpec(group_by=..., order_by=...,
  require='all_success')` in Python and matching YAML edge syntax.
* Static newline-delimited path manifests, exact gather provenance in
  `job_config.json`, and a cross-validation gather tutorial.
* Explicit gather markers in logical Process/IO graphs and a compiled process
  cardinality graph that distinguishes direct, fan-out, and gather edges.
* Standalone gather execution: quoted-heredoc manifest writers are embedded in
  consumer commands and `invoke.sh`, avoiding `ARG_MAX` and hidden preparation.
* File-backed Slurm gather submission: gathered jobs use a short
  `bash invoke.sh` payload instead of placing a potentially large heredoc in
  `sbatch --wrap`.
* Fixed dependent serial/tmux jobs so cmd_queue does not indent generated
  heredoc delimiters inside dependency guards.
* Fixed gathered consumer grouping so cmd_queue logging wraps ``({ ... })``
  instead of producing the Bash arithmetic form ``(( ... ))``; generated
  commands retain explicit indentation while heredoc bodies remain column-zero.

### Fixed

* Reject matrix rows that compile to one process identity but disagree on
  `__enabled__` or `__slurm_options__`. Neither is part of process identity, so
  the first row silently won, making compilation row-order dependent. A
  disabled gather source stayed in the consumer's manifest membership while its
  output was never produced, and duplicate rows could silently run under the
  wrong partition, GPU count, memory, time limit, or account.
* Report the configured `root_dpath` from `build_schedule` instead of reading
  it back off the pipeline, which raised `AttributeError` on an empty parameter
  grid instead of exiting cleanly after the existing warning.
* Include port-resolved ordinary input provenance in process identity so gather
  consumers with different row-local bindings cannot be silently canonicalized.
* Preserve parallel gather and ordinary port semantics in compiled cardinality
  diagnostics.
* Fall back to the current directory when compiling a pipeline whose template
  nodes leave ``root_dpath`` unset.
* Refresh dependency locks for the new runtime ``kwconf`` dependency.


## Version 0.2.5 - Released 2026-06-25

### Added

* `ProcessNode` now accepts `setup` and `teardown` shell commands, forwarded through `Pipeline.submit_jobs` to the underlying cmd_queue job (`setup` as a gating precondition, `teardown` as always-run cleanup). This brackets a node with an external resource — e.g. acquire a GPU lease before a run and release it after, even on failure or signal — without modeling acquire/release as separate, skippable DAG nodes. Requires cmd_queue with `BashJob`/`SlurmJob` setup/teardown support (>= 0.3.1).
* The declarative YAML pipeline spec now supports `setup` and `teardown` node keys (a single shell string or a list), forwarded to the node's resource lifecycle and round-tripped by `dump_yaml_pipeline`.
* Backend execution tests (`tests/test_pipeline_execution.py`) that actually run a pipeline — including a YAML pipeline with `setup`/`teardown` — on the serial backend, and on tmux/slurm when those backends are available (skipped otherwise).


## Version 0.2.4 - Released 2026-06-19


## Version 0.2.3 - Released 2026-03-26


## Version 0.2.2 - Released 2026-01-15

### Added

* Support deriving ProcessNode IO/parameter groups from a scriptconfig schema via the new ``params`` class variable.

### Changed

* Increased hash size to reduce collision chance. This is a backwards incompatible change.


## Version 0.2.1 - Released 2026-01-07


### Changed

* YAML paths in grid values no longer auto-expand unless explicitly behind an `__include__` key. See docs for details.

### Fixed

* Dictionaries now work correctly as "scalar" values in a parameter grid


## [Version 0.0.1] -

### Added
* Initial version
