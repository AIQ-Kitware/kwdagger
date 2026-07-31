# Changelog
We [keep a changelog](https://keepachangelog.com/en/1.0.0/).
We aim to adhere to [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.2.7 - Unreleased


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
