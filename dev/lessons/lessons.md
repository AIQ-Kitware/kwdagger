# Lessons

Confirmed, reusable lessons only. See `AGENTS.md` for the format and the bar.

- **Lesson:** A `ProcessNode` memoizes lineage queries while it is still being
  constructed, before any connection exists. Those answers describe a graph
  that was never built. `Pipeline.build_nx_graphs` is the first moment the
  complete connection state is known, so it clears every node's
  `_configured_cache` before reading — without that, template
  `predecessor_process_nodes()` and `ancestor_process_nodes()` return empty
  lists on a fully connected pipeline.
  - **Evidence / MWE:** `dev/lessons/mwe/template_predecessor_staleness.py`;
    commit "Resolve identity from the effective input source".
  - **Applies when:** adding memoized lineage state to `ProcessNode`, or moving
    work out of `build_nx_graphs`. Supersedes an earlier form of this lesson
    which concluded that the successor pass in `build_nx_graphs` was
    load-bearing *because* of the staleness. It was, until the cache was
    cleared at the top of that method; the redundancy is now genuine, and
    `successor_process_nodes` is kept as public API rather than as a
    correctness crutch.

- **Lesson:** Two different questions get asked of an input port, and
  conflating them causes silent result-directory collisions. *Structural*
  ("what could supply this port?") is the only question answerable on a
  template pipeline, where nothing is configured, so the logical graph must
  use it. *Effective* ("what does supply it?", applying `_resolved_value`'s
  precedence: gather > explicit > forwarded > produced > default) is the only
  one identity may use, because a port wired to a producer but configured with
  an explicit path reads that path. `_produced_origins` and
  `_effective_origins` keep the two apart.
  - **Evidence / MWE:** `tests/test_review_regressions_extra.py`
    `test_overriding_a_connected_input_changes_identity`; commit
    "Resolve identity from the effective input source".
  - **Applies when:** adding anything to `depends`, `final_input_config`, or
    the template graph.

- **Lesson:** Generated `invoke.sh` commands and the demo pipeline invoke
  `python`, not `sys.executable`. If the active virtualenv's `bin` is not on
  `PATH`, the end-to-end tests fail with empty output directories rather than
  with an import error, which reads as a code regression. Run the suite with
  the venv's `bin` on `PATH`, not by calling `.venv/bin/python` directly.
  - **Evidence / MWE:** `python3 -m pytest tests/` gives 4 failures at a green
    commit; `PATH=.venv/bin:$PATH pytest tests/` passes. Recorded in
    `dev/journals/claude.md` (2026-08-03).
  - **Applies when:** triaging an unexpectedly red baseline in this repo.

- **Lesson:** When a check exists to protect an artifact, compare the artifact,
  not something derived from it. Arbitration between requests that share a
  `process_id` exists because one result directory holds one
  `job_config.json`. Three successive attempts compared derived summaries --
  an effective-ancestor set, a prerequisite union, then a per-input delivery
  signature of produced origins -- and each was defeated by a distinction the
  record kept and the summary dropped: a second output of the same producer, an
  input alias that supplied the value versus one left unresolved, a parameter
  port with no representation in the summary at all. Comparing the serialized
  record is complete by construction: two requests that would write the same
  file have nothing left to arbitrate. Derived summaries are still worth
  keeping *in front of* it for the specific error messages they can give, never
  in place of it.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_which_alias_supplied_the_value_is_a_requested_difference` and
    `test_which_parameter_port_supplied_the_value_is_arbitrated`; commit
    "Arbitrate on the requested record, not a summary of it".
  - **Applies when:** adding to `_agreement.py`, or replacing any comparison in
    it with a cheaper representation of the same information.

- **Lesson:** A many-to-one rewrite applied to mapping keys cannot be
  reassembled with a dict comprehension. Root-relative canonicalization maps
  several spellings of one path to one canonical key, so
  `{rewrite(k): rewrite(v) for ...}` silently drops entries and leaves a
  mapping whose hashed payload is identical to a genuinely smaller one. Nothing
  downstream can recover the difference, and two schedules that never meet
  cannot be arbitrated against each other, so the collision has to be refused
  where it happens.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_colliding_canonical_mapping_keys_are_refused`; the doctest on
    `_root_relative`.
  - **Applies when:** normalizing, canonicalizing, or rewriting anything used
    as a dictionary key in an identity payload.

- **Lesson:** A per-node snapshot cannot arbitrate request state that never
  reaches a node. `Pipeline.configure` keeps top-level `__slurm_options__` on
  the pipeline, and `log` / `enable_links` / `write_invocations` /
  `write_configs` are arguments to `submit_jobs`; a duplicate request returns
  before any of them is applied, so the comparison has to take them from the
  submitter. The full-matrix path was accidentally safe because the compiler
  copies a row-global value into each node config -- which is why a passing
  gather test did not imply the ordinary path was covered. When a check reads
  its inputs from one object, enumerate what the *caller* holds that the object
  does not.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_gather_free_pipeline_slurm_options_conflict` and
    `test_submission_bookkeeping_flags_must_agree`; commit "Arbitrate the state
    the submitter holds, and give mapping keys one policy".
  - **Applies when:** adding a `submit_jobs` argument, or moving configuration
    between the pipeline and its nodes.

- **Lesson:** Coerce at the configuration boundary, not in each reader.
  kwdagger accepts `os.PathLike` from Python callers and strings from YAML and
  the CLI; letting the Python-only type survive meant `_root_relative`,
  identity serialization, provenance, and the runtime JSON writer each had to
  understand it separately, and they disagreed -- `json.dumps` renames an `int`
  key silently, refuses a `Path` key, and cannot sort a mixture, so a key left
  unconverted hashed as one thing, compared as another, and persisted as a
  third or crashed. One normalization where the value is stored gives every
  reader the same shape, and makes the invariant statable: after coercion every
  path-like object is a string and every mapping key is a string.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_a_pathlike_mapping_key_survives_submission`,
    `test_a_path_and_its_string_are_one_key`; `_normalize_config_value`.
  - **Applies when:** adding a value shape to configuration, or relying on a
    serializer to coerce something the rest of the system also reads.

- **Lesson:** "Default to the current value" is how per-row state goes stale.
  `Pipeline.configure` wrote
  ``self.__slurm_options__ = coerce(config.pop('__slurm_options__', self.__slurm_options__))``,
  so a row that omitted the key inherited the previous row's request and
  "explicit options" became indistinguishable from "no options" in row order.
  The fix is the shape `ProcessNode` already had: keep a separate baseline and
  reset to it every configure. A per-row setter whose default is its own
  current value is always this bug; the default has to be the baseline.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_omitting_pipeline_slurm_options_does_not_inherit_them`; commit
    "Reset per-row state to a baseline, and refuse bytes paths".
  - **Applies when:** adding anything to `Pipeline.configure` or
    `ProcessNode.configure` that a matrix row may set.

- **Lesson:** Two serializers for one artifact must refuse the same things.
  Arbitration serialized the requested record with `json.dumps(..., default=str)`
  while the writer of `job_config.json` used plain `json.dumps`, so a value only
  the writer would reject passed the comparison and failed later, at write time
  -- the exact reader disagreement the record comparison exists to remove. A
  `default=` fallback in a checker is a way of not checking.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_the_requested_record_and_the_written_file_agree`.
  - **Applies when:** comparing a serialized form of something that is also
    written somewhere else.

- **Lesson:** Two code paths that combine the same layers must call the same
  function to do it. Slurm options are merged from four layers; the
  row-at-a-time path merged them key-wise while the gather compiler
  *substituted* a row-global mapping for a node's own, so adding an unrelated
  gather to a pipeline changed what resources an unrelated node requested. Both
  behaviours looked locally reasonable, and nothing compared them --
  `layer_slurm_options` now exists so there is one answer to combine with, and
  the parity tests assert the two shapes agree rather than asserting each in
  isolation.
  - **Evidence / MWE:** `tests/test_slurm_layering.py`, which fails four cases
    on the gather path if the old substitution is restored.
  - **Applies when:** adding a configuration layer, or adding a second path
    that resolves configuration.

- **Lesson:** A precondition that keeps a second implementation alive deserves
  a probe before a plan. `compile_configurations` refused a gather-free
  pipeline, which is the only reason the row-at-a-time scheduler existed --
  and nothing in the compiler was ever gather-specific. Bypassing the guard in
  a ten-line throwaway script produced a correct compiled graph on the first
  try, turning a phase budgeted as a rewrite into a five-line diff. The probe
  would have been just as valuable had it failed; what it removes either way is
  planning against a guess.
  - **Evidence / MWE:** `tests/test_compile_without_gather.py`.
  - **Applies when:** a refactor's cost estimate rests on what some code
    *cannot* do, and nothing has tested that claim.

- **Lesson:** A characterization test can pass through a refactor while
  asserting nothing. `submit_jobs` keyed `node_status` by node name on one
  scheduling path and by `process_id` on the other; the parity harness filtered
  statuses by name, so on the compiled side it filtered an empty dict and
  compared it to another empty dict. The field looked covered for as long as
  the two paths disagreed about it most. When comparing two implementations,
  assert that the collected records are non-empty before asserting they match.
  - **Evidence / MWE:** `tests/test_scheduler_parity.py`, whose `_assert_parity`
    checks the record *sets* are equal before comparing fields.
  - **Applies when:** writing parity or characterization tests that select
    subsets of a result by key.

- **Lesson:** The fix for a duplicate-authority defect must not introduce one.
  Making `Pipeline.submit_jobs` delegate to the compiler needs the row it was
  configured with; the obvious implementation reconstructs that row from node
  state afterwards. That is a second answer to "what was this row" inside a
  refactor whose purpose is removing second answers, and it silently loses the
  row-global `__slurm_options__`, which `configure` pops before the nodes ever
  see it. Remembering the input is cheaper and has no second authority in it.
  - **Evidence / MWE:** `tests/test_single_scheduling_path.py`
    `test_the_compiled_row_is_the_row_as_given`.
  - **Applies when:** a caller needs to re-drive a computation from state that
    was derived from an input it could have kept instead.

- **Lesson:** A claim that one source outranks another is untested until the
  two disagree. The compiled graph is the authority on execution dependencies,
  but the compiler builds its edges from node state, so in normal use the two
  always agree and any test of the claim passes for the wrong reason. Provoking
  the disagreement -- removing an edge from a compiled graph whose nodes still
  describe it -- looks artificial and is exactly what the claim means.
  - **Evidence / MWE:** `tests/test_compiled_graph_authority.py`
    `test_an_edge_removed_from_the_graph_is_not_waited_on`.
  - **Applies when:** asserting precedence between two representations that a
    single code path keeps in sync.

- **Lesson:** A validation procedure that lives only in journal prose is lost.
  The "TA1 fingerprint" gate was run for six review rounds and referenced in
  entry after entry, and the next session could not find it, because it had
  never been anything but a sequence of commands somebody typed. It is two
  cards compiled and dumped -- thirty lines. Write the gate down as a script in
  the repository the first time you rely on it, not the sixth.
  - **Evidence / MWE:** `dev/ta1_fingerprint.py`, named in `AGENTS.md`.
  - **Applies when:** a check earns a place in your routine.

- **Lesson:** Submitting a description of work must not edit the description.
  `skip_existing` wrote its per-call decision back as `node.enabled = False`,
  so a compiled pipeline stopped describing what was requested: a later
  submission with `skip_existing=False` still reported the node disabled, and
  resubmitting to the same queue compared the mutated node against the first
  snapshot and raised a conflict the user never created. Per-call decisions
  belong in per-call state. The invariant was stated in six commit messages
  and never asserted.
  - **Evidence / MWE:** `tests/test_compiled_pipeline_is_static.py`.
  - **Applies when:** an object is returned to a caller and also consumed by
    the operation that produced it.

- **Lesson:** Derived-once is not derived. Two lookup views were
  `cached_property` over the authoritative graph, which satisfied "no second
  container" on paper -- but the mapping they return is independently mutable,
  so a caller's edit survived in the cache while the graph the submitter walks
  knew nothing about it. If a derived view hands back a mutable object, either
  rebuild it on access or make it read-only; do not memoize it.
  - **Evidence / MWE:** `tests/test_compiled_pipeline_is_static.py`
    `test_mutating_a_returned_lookup_does_not_stick`.
  - **Applies when:** replacing a stored collection with a derived one.

- **Lesson:** Deep-copying one node of a connected graph copies the graph.
  Ports hold their peers and every port holds its `parent`, so cloning a wired
  node materialized every other node in the pipeline and then discarded them --
  quadratic once compilation clones per node per matrix row, and a latent
  failure whenever anything non-copyable was attached anywhere in the
  component. Detach the outward references, copy, restore: the copy is born
  disconnected. Measure before deciding it does not matter; per-clone cost
  grew 0.69 ms to 4.23 ms between a two-node and a thirty-two-node pipeline.
  - **Evidence / MWE:** `tests/test_compiled_pipeline_is_static.py`, the
    cloning section, including a per-clone cost guard.
  - **Applies when:** copying one member of a bidirectionally linked structure.

- **Lesson:** A memoization cache is part of the object graph. After detaching
  every obvious link -- ports, gather connections, dependency-only
  predecessors -- cloning still copied the whole pipeline, and the remaining
  route was `_configured_cache`, which holds a computed list of *other nodes*
  under the predecessor query. Reading attribute lists would not have found
  it; walking references from one object to another did, in about a minute.
  - **Evidence / MWE:** the `_configured_cache` entry in `_OUTWARD_LINKS`.
  - **Applies when:** isolating, pickling, or copying an object that memoizes
    graph queries.

- **Lesson:** `git checkout <file>` is not an undo for uncommitted work. Used
  to revert a deliberate one-line break while verifying a test was
  load-bearing, it silently discarded two other uncommitted fixes in the same
  file. Copy the file aside and copy it back.
  - **Applies when:** temporarily breaking code to prove a test fails.
