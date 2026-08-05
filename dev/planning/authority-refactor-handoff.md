# The single-scheduling-path (authority) refactor

Written 2026-08-05 as a handoff with Phase 1 committed and phases 2-7 not
started. **Rewritten the same day, complete.** Phases 2-7 are committed, the
TA1 fingerprint the original could not locate has been found, built, and run,
and one decision is left open on purpose.

Read this with `AGENTS.md` -- "Core execution model and priorities",
"Authoritative pipeline representations", and "Process identity, scheduling,
and provenance" -- and the last few entries of `dev/journals/claude.md`.

---

## 1. What the refactor was

kwdagger scheduled a batch two ways:

* a pipeline **with** a gather compiled the whole matrix up front and submitted
  a `CompiledPipeline`;
* a pipeline **without** one configured and submitted a row at a time.

Two implementations of the same job, with a *compilation* feature deciding
which execution architecture ran. Every review round in the 0.3.x series found
them disagreeing somewhere -- stale row state, divergent Slurm layering,
divergent normalization boundaries. Each was fixed individually. They were the
same defect wearing different hats: **two authorities for one question, with
nothing forcing them to agree.**

The dual scheduler is gone. `build_schedule` compiles the matrix and submits
the compiled graph for every pipeline, and `Pipeline.submit_jobs` compiles the
row it was configured with and submits that, so an interactive submission is a
matrix of one rather than a second implementation.

## 2. The authority map, closed

Each row of the original finding, and where the answer lives now.

| question | was | is |
|---|---|---|
| which batch engine runs? | `schedule.py:254` branched on `has_gather_connections` | there is one engine |
| may this pipeline compile? | `_logical.py:549` rejected gather-free compilation; `:752` rejected gather submission | everything compiles. `Pipeline.submit_jobs` still refuses a *gather* pipeline, which is a statement about the request (a one-row collection is not the collection), not about the compiler |
| what is a normalized row? | `Pipeline.configure` and `_compile_pipeline_configurations` each called `normalize_config` | still both, which was always the intent -- one leaf, entered before any reserved key is read. The scheduler additionally crosses it *before* matrix expansion now, so grid cardinality and compiled cardinality cannot disagree |
| what are the effective Slurm options? | compiler injected row-global into node config; runtime layered pipeline-wide on top; arbitration re-layered the halves | `resolve_slurm_options`, called only by compilation, stored as `node.effective_slurm_options`, read verbatim by everyone else |
| are two equal-identity requests compatible? | `_compile.py` and `_runtime.py` with separate registries | compilation arbitrates; the runtime registry is a defensive backstop for separately compiled graphs sharing a queue, and for the submission flags no compilation can see |
| what are the runtime dependencies? | `effective_execution_graph()` on one path, `proc_graph` on the other | `CompiledPipeline.proc_graph`, walked by submission. Nothing re-derives ancestry from node state |

## 3. Commits

One reviewable commit per phase. Phases 2-7 were written on `dev/0.3.1`;
`dev/0.4.0` was branched from Phase 7 once the version question below was
settled, and the version bump sits on top of it. The phase commits are shared
by both branches and unchanged.

| phase | commit | what |
|---|---|---|
| 1 | `346ac18` | characterization tests (pre-existing) |
| 2 | `b1e3562` | allow a gather-free pipeline to compile |
| 3 | `87bacd2` | route every batch through compilation |
| 4 | `0390d0d` | one Slurm resolver, one normalization boundary |
| 5 | `d627555` | compilation arbitrates, submission is a backstop |
| 6 | `9c883da` | the compiled graph is the only dependency authority |
| 7 | this commit | remove the last duplicate, update the docs |

## 4. What a caller sees

Documented in `CHANGELOG.md` under 0.4.0, where the four breaking ones are
marked as such. They are:

* `build_schedule` returns a `CompiledPipeline` for every pipeline, not just a
  gathering one. Its `nodes` are keyed by `process_id`; `nodes_by_name` is the
  name lookup, mapping to a **list**.
* `submit_jobs` reports `node_status` keyed by `process_id`, and a batch
  returns one matrix-wide summary rather than one per row.
* `kwdagger.pipeline._runtime.submit_jobs` no longer takes `slurm_options`,
  and `CompiledPipeline` no longer carries a pipeline-wide copy.
* `ProcessNode.__slurm_options__` is removed -- an unread duplicate of
  `slurm_options`.

`aiq-magnet` already read `self.dag.nodes.values()` and commented that
`build_schedule` returns "configured instances keyed by process id", so it was
written against the compiled shape and is *fixed* by this rather than broken:
that assumption previously only held when the card gathered.

## 5. The open questions, resolved

**The TA1 fingerprint fixture -- found, built, run, and green.** It is the
`lift` and `lomo` cards in `aiq-ta1-incubilate`
(`cards/oc_lift_kwdagger.yaml`, `cards/oc_lomo_kwdagger.yaml`); the procedure
was only ever in journal prose, which is why it could not be located. It is
now `dev/ta1_fingerprint.py` and named in `AGENTS.md`. Across the entire
refactor -- `84cbb01` to now -- both cards compile to 53 concrete processes
with **byte-identical process ids, node directories, and commands**. No result
directory moves; no cache is invalidated.

Worth repeating what a previous entry learned the hard way: a green
fingerprint means "did not change what I care about", not "is correct". It
sees nothing in arbitration, provenance, or Slurm options.

**Interactive `Pipeline.submit_jobs` -- built as suggested.** It compiles the
row it was configured with, through the same compiler, and submits that. The
"hidden call into a large matrix compiler merely to inspect one row" worry
does not bite: the compiler's cost is per row, and one row is one row. The
shape is named rather than implicit -- `compile_current_configuration()`.

The row is *remembered* by `configure` before its reserved keys are popped,
rather than reconstructed from node state afterwards. A reconstruction would
have been a second answer to "what was this row", which is the kind of thing
this refactor removes, and it would have lost the row-global
`__slurm_options__`.

**Version target -- 0.4.0, on `dev/0.4.0`.** The brief said 0.4.0 and an
earlier note said keep 0.3.1; the maintainer settled it as 0.4.0 once the
evidence for each was written down:

* The changes in section 4 are breaking for a caller who reads `node_status`
  by name, or who expects the template back from `build_schedule`. Under the
  semver this repo aims at, that is a minor bump. This is what decided it.
* Against, and still true: no *identity* changed -- the TA1 fingerprint is
  byte-identical -- so nobody's results move, which is the break users
  actually feel. And the one known dependent, `aiq-magnet`, was already
  written against the new shape. The changelog leads with that, so a reader
  seeing a minor bump does not assume their cache is invalid.

## 6. Validation

From the `kwdagger` checkout:

```bash
python run_tests.py
./run_doctests.sh          # or: uv run xdoctest kwdagger --style=google all
ruff check kwdagger tests && ruff format --check kwdagger tests
uv run ty check ./kwdagger ./tests
uv run --with flake8 flake8 --select=E9,F63,F7,F82,F401,F811,F841 kwdagger tests
git diff --check
uv build --wheel
```

Baseline at `346ac18` was 391 passed / 18 skipped. Now **454 passed / 18
skipped**, 93 doctests, ruff / `ty` / flake8 clean. The 63 added tests are:

| file | pins |
|---|---|
| `test_compile_without_gather.py` | compilation is the ordinary case |
| `test_single_scheduling_path.py` | one path, and what a caller now sees |
| `test_slurm_resolution.py` | one resolver, one normalization boundary |
| `test_arbitration_authority.py` | every conflict class, in either row order |
| `test_compiled_graph_authority.py` | the graph decides, provoked where it can differ |

`test_scheduler_parity.py` is unchanged in structure and still runs: both of
its sides now reach the same compiler, so it asserts that a whole matrix and
its rows one at a time agree, and that declaring a gather changes nothing for
the nodes around it. Its last test flipped from recording a divergence to
asserting the equality, which is what it was written to do.

### Environment notes that still apply

* **`python` is not on `PATH` in the agent environment**, only `python3` and
  `.venv/bin/python`. Four tests generate commands that invoke `python` and
  fail for that reason alone. Run the suite as
  `PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests kwdagger`.
* `uv sync` re-resolves `uv.lock` and produces a large unrelated diff. Check
  `git status` after syncing and `git checkout uv.lock` if unintended.
* `uv lock` caches project metadata; after editing `requirements/runtime.txt`
  use `uv lock --refresh-package kwdagger`.
* The constraint files under `requirements/locks/` are `uv export` artifacts;
  regenerate with the commands in their own headers.
* `git fetch` / `git ls-remote` hang in this environment. Do not use them.
* **None of these branches are pushed** -- no push credentials here. The
  superproject's gitlinks reference commits nobody else can fetch, which is
  survivable only because `aiq-eval-runner` is itself unpushed. Push every
  submodule branch before pushing the superproject.

## 7. Related work still not in scope

* `cmd_queue` on `dev/0.3.3`: its PyPI page has no description, because
  `readme` is missing from `project.dynamic` in `pyproject.toml`. One line;
  the maintainer deferred it deliberately.
* `cmd_queue` will drop the deprecated scriptconfig `CMDQueueConfig` in 0.4.0.
  kwdagger is kwconf-only in its own code; scriptconfig remains in the
  environment transitively, which the maintainer has confirmed is fine.
