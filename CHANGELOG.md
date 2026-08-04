# Changelog
We [keep a changelog](https://keepachangelog.com/en/1.0.0/).
We aim to adhere to [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.2.7 - Unreleased

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
  structurally reachable producer. Scheduling stays structural and
  conservative: a process wired to supply an input is still ordered ahead of
  its consumer even when the consumer does not read what it makes, because
  ordering a job that turns out not to matter costs nothing while missing one
  is a race. `ProcessNode.effective_predecessor_process_nodes` and
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
  *which* producer instance made it. The ancestor payload carries `algo_id`,
  which is deliberately blind to a producer's own inputs, so two producers
  running one algorithm over different data were indistinguishable there: both
  consumers hashed to one `process_id` and shared a result directory, and
  whichever compiled first supplied the surviving command. An input's producing
  ports now contribute `process_id` and port name to the consumer's identity,
  whether wired directly or reached through an alias.
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
