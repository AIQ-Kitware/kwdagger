# Handoff: the single-scheduling-path (authority) refactor

Written 2026-08-05, at the end of the session that released 0.3.0 and started
this work. Phase 1 is committed; phases 2-7 are not started.

Read this with `AGENTS.md` ("Core execution model and priorities", and "Process
identity, scheduling, and provenance") and the last few entries of
`dev/journals/claude.md`.

---

## 1. Where everything stands

| repo | branch | HEAD | state |
|---|---|---|---|
| `kwdagger` | `dev/0.3.1` | `346ac18` | 0.3.0 released; 0.3.1 open; Phase 1 committed |
| `cmd_queue` | `dev/0.3.3` | `c73365f` | 0.3.2 released to PyPI |
| `aiq-magnet` | `dev/kwdagger-gather-and-node-execution` | `b4fc8b4` | on kwdagger >= 0.3.0 |
| `aiq-ta1-incubilate` | `dev/kwdagger-pipeline` | `dc6669c` | on kwdagger >= 0.3.0 |
| `princeton-phase1-dry-run-eval` | `dev/rest-endpoint` | `ed6ac6d` | on kwdagger >= 0.3.0 |
| `aiq-eval-runner` (superproject) | — | `9812bc8` | submodule pointers updated |

**None of these branches are pushed** from the agent environment (no push
credentials; https remotes are anonymous read-only). The superproject's
gitlinks therefore reference commits nobody else can fetch. That is survivable
only because `aiq-eval-runner` is itself unpushed: **push every submodule
branch before pushing the superproject.**

`formalizations/aiq-drsb-formalization` and `submodules/infer-stack` are dirty
for reasons unrelated to this work.

---

## 2. The problem this refactor solves

kwdagger schedules a batch two ways:

* a pipeline **with** a gather compiles the whole matrix up front and submits a
  `CompiledPipeline`;
* a pipeline **without** one configures and submits a row at a time.

Two implementations of the same job. Whether a pipeline contains a gather —
a compilation feature — decides which execution architecture runs. Every
review round in the 0.3.x series found the two disagreeing somewhere:

* stale row state (a row omitting `__slurm_options__` inherited the previous
  row's);
* divergent Slurm layering (the compiler *substituted* a row-global mapping for
  a node's own, the row path merged key-wise — so an unrelated gather changed
  what resources an unrelated node asked for);
* divergent normalization boundaries (each path crossed it at a different
  point, so whether a reserved key was even visible depended on the path).

Each was fixed individually. They are the same defect wearing different hats:
**two authorities for one question, with nothing forcing them to agree.**

---

## 3. The authority map (as of `346ac18`)

This is the core finding. Each row is a question that currently has more than
one owner.

| question | authority A | authority B |
|---|---|---|
| which batch engine runs? | `schedule.py:254` branches on `dag.has_gather_connections` | — |
| may this pipeline compile? | `_logical.py:549` rejects gather-free compilation | `_logical.py:752` rejects gather submission |
| what is a normalized row? | `Pipeline.configure` calls `normalize_config` | `_compile_pipeline_configurations` calls it separately |
| what are the effective Slurm options? | compiler injects row-global into node-local config (`_compile.py`) | runtime layers pipeline + node (`_runtime.py`) |
| are two equal-identity requests compatible? | `_compile.py:771` `check_execution_agreement` | `_runtime.py:193` same function, separate registry |
| what are the runtime dependencies? | `Pipeline.submit_jobs` passes `effective_execution_graph()` (`_logical.py:765`) | `CompiledPipeline.submit_jobs` passes `self.proc_graph` (`_compile.py:316`) |

`layer_slurm_options` and `normalize_config` are already *shared functions* —
that work landed in 0.3.0. What remains is that they are **called from two
places at two different times**, which is the actual duplication.

---

## 4. What is done

**Phase 1 — characterization tests. Committed as `346ac18`.**

`tests/test_scheduler_parity.py`: 14 tests that build the same pipeline twice,
once with an unrelated gather bolted on purely to select the engine, and
compare everything observable about the nodes that gather has nothing to do
with — identities, commands, final paths, effective Slurm request, effective
predecessors, queue dependencies and command, requested provenance,
`job_config.json`, `invoke.sh`, enabled state. Across one row, several rows,
duplicate identities, manual-versus-produced equal paths, node defaults against
row overrides, a disabled producer, `PathLike` values, output overrides, perf
params, the bookkeeping flags, and cache-root relocation.

Two things it records rather than smooths over:

1. **Reading node state after the row-at-a-time loop only ever shows the last
   row**, because that path mutates one node object per row while the compiler
   clones. The harness collects per row for that reason. This also means
   "inspect the pipeline after scheduling" is misleading today on the
   non-gather path.
2. **`node.slurm_options` means different things on the two paths.** The
   effective request is equal either way (every other parity test asserts
   that), but the attribute is not. The last test in the file pins that
   difference explicitly and says it should become an *equality* in Phase 4.

---

## 5. What is left

Phases 2-7 of the original brief, unchanged. One reviewable commit each; do not
combine the behavioral switch with the cleanup.

### Phase 2 — allow gather-free compilation
Remove the restriction at `_logical.py:549`. The compiler must clone ordinary
nodes, configure all rows, canonicalize identical requests, preserve
dependency-only edges and aliases, build an effective DAG, and give cardinality
diagnostics without gathers. Gather-free must be the **ordinary case of the
same algorithm**, not a second branch.

### Phase 3 — route all batch scheduling through compilation
Replace the branch at `schedule.py:254` with: normalize and expand rows,
compile, submit the `CompiledPipeline`. `Pipeline.submit_jobs` (`_logical.py:752`)
should delegate to the same path rather than keeping independent semantics.
Batch summaries may become matrix-wide; document that rather than degrading
diagnostics.

### Phase 4 — consolidate normalization and Slurm resolution
One leaf `normalize_config_mapping(raw)` entered by both interactive configure
and compilation, called before reading reserved keys / building `DotDict` /
routing to nodes / reading row-global Slurm / computing matrix dimensions. One
pure resolver for the documented precedence — pipeline base → row global → node
declared default → row node override — surfaced as an inspectable
`node.effective_slurm_options` that runtime consumes directly. **The last test
in `test_scheduler_parity.py` should flip to an equality here.**

### Phase 5 — move arbitration entirely into compilation
Compiler becomes the primary authority; runtime keeps a defensive check only.
Row-order independence for every conflict class, with explicit tests.

### Phase 6 — runtime consumes the compiled graph
`CompiledPipeline.proc_graph` becomes the sole authority for concrete runtime
dependencies. Runtime stops calling structural/effective ancestry itself.
`CompiledPipeline.nodes` is already a derived `cached_property` (done in
0.3.0) — keep it derived, no competing container.

### Phase 7 — remove dual-path code, update docs
Repo-wide reference search first. Add the "Authoritative pipeline
representations" section to `AGENTS.md`. Update `CHANGELOG.md`, the scheduler /
hashing / Slurm / compilation docs, docstrings, and the journal. State
explicitly that the dual scheduler was removed because it created multiple
authorities for one request.

---

## 6. Open questions to resolve before finishing

* **The TA1 fingerprint fixture.** The brief asks for it to be run and for any
  change to be explained. I never located it — it does not appear to live in
  `kwdagger`. Find it (likely in a TA1 repo or `aiq-eval-runner`) and wire it
  into validation from Phase 2 onward, not at the end. Earlier journal entries
  refer to checking "TA1 fingerprints unchanged", so there is an established
  procedure somewhere.
* **Version target.** The brief says 0.4.0; the maintainer said keep it on
  0.3.1. The public-compatibility section allows intentional breaks — decide
  whether the batch-scheduling changes justify 0.4.0 after all, since
  `build_schedule()` consistently returning a `CompiledPipeline` is a visible
  behavior change.
* **Interactive `Pipeline.submit_jobs`.** Invariant 2 says it must not keep
  independent scheduling semantics, but must also not become "a hidden call
  into a large matrix compiler merely to inspect one row". A one-row compiled
  representation through a shared implementation is the suggested shape;
  confirm that is what is wanted before building it.

---

## 7. Validation

From the `kwdagger` checkout:

```bash
python run_tests.py
./run_doctests.sh          # or: uv run xdoctest kwdagger --style=google all
./run_linter.sh
ruff check kwdagger tests && ruff format --check kwdagger tests
uv run ty check ./kwdagger ./tests
uv run --with flake8 flake8 --select=E9,F63,F7,F82,F401,F811,F841 kwdagger tests
git diff --check
uv build --wheel
```

Baseline at `346ac18`: **391 passed, 18 skipped**; 92 doctests; ruff, `ty`, and
the wider flake8 selection all clean.

### Environment notes learned the hard way

* The venv had PyPI's `cmd_queue` while the code needed the unreleased 0.3.2;
  that is resolved now (0.3.2 is published and locked), but if a sibling
  checkout is ever needed again: `uv pip install -e ../cmd_queue --no-deps`,
  and use `uv run --no-sync` afterwards so `uv sync` does not undo it.
* `uv sync` re-resolves `uv.lock` ("removal of global exclude newer") and
  produces a large unrelated diff. Check `git status` after syncing and
  `git checkout uv.lock` if the churn was not intended.
* `uv lock` caches project metadata. After editing `requirements/runtime.txt`
  it will resolve in milliseconds *without noticing* — use
  `uv lock --refresh-package kwdagger`.
* The constraint files under `requirements/locks/` are `uv export` artifacts;
  regenerate with the exact commands recorded in their own headers, and only
  after the lock is correct.
* `git fetch` / `git ls-remote` hang in this environment. Do not use them.

---

## 8. Related work not in scope

* `cmd_queue` on `dev/0.3.3`: its PyPI page has **no description**, because
  `readme` is missing from `project.dynamic` in `pyproject.toml` (the
  `dynamic.readme.file` config below it is ignored without it). One line. The
  built metadata has no `Description` header and a zero-length body. The
  maintainer deferred this deliberately.
* `cmd_queue` will drop the deprecated scriptconfig `CMDQueueConfig` in 0.4.0.
  It emits a `DeprecationWarning` on subclassing as of 0.3.2. kwdagger is
  already kwconf-only in its own code; scriptconfig remains in the environment
  transitively through cmd_queue, which the maintainer has confirmed is fine.
* `Pipeline.node_dict` returns the caller's keys when built from a mapping —
  no longer possible since 0.3.0 requires a sequence, so this is now closed.
