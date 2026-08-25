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

- **Lesson:** If you *do* compare two requests, compare the artifact and not
  something derived from it. Three successive attempts compared derived
  summaries -- an effective-ancestor set, a prerequisite union, then a
  per-input delivery signature of produced origins -- and each was defeated by
  a distinction the record kept and the summary dropped: a second output of
  the same producer, an input alias that supplied the value versus one left
  unresolved, a parameter port with no representation in the summary at all.
  The serialized record is complete by construction.
  - **Superseded in part (0.4.0):** the premise was that requests sharing a
    `process_id` must *agree*, because one directory holds one
    `job_config.json`. They need not. The first request wins and writes the
    record; comparison is an opt-in diagnostic (`warn`/`error`). What survives
    is the narrow point above, which is why `_duplicates.py` names
    `requested_provenance_record()` rather than a summary of it -- and why
    `delivery_signature()`, built purely to sharpen a rejection message, was
    deleted rather than kept. Read the first-wins policy in `AGENTS.md` before
    acting on this lesson.
  - **Applies when:** building a comparison in `_duplicates.py`, having first
    established that a comparison is wanted at all.

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

- **Lesson (superseded, 0.4.0 -- a wrong turn kept as a record):** that a
  per-node snapshot cannot *arbitrate* request state which never reaches a
  node, so the comparison must reach into the submitter for `log` /
  `enable_links` / `write_invocations` / `write_configs`. There is no
  cross-call arbitration any more: those are submission arguments, no
  compilation can know them, and two calls sharing a queue are two independent
  operational requests. The registry this lesson argued for is deleted. Do not
  restore it; see the duplicate-policy section of `AGENTS.md`.
  - What remains true and is worth carrying elsewhere: when a check reads its
    inputs from one object, enumerate what the *caller* holds that the object
    does not. The full-matrix path was accidentally safe here because the
    compiler copies a row-global value into each node config, which is why a
    passing gather test did not imply the ordinary path was covered.
  - **Applies when:** nothing in the duplicate-request area. Kept so the
    argument is recognizable if it is made again.

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

- **Lesson (superseded, 0.4.0 -- a wrong turn kept as a record):** that
  "what the computation requires" and "what this call queued" are two
  questions a request registry must hold both of, so `queued_prerequisites`
  had to join `prerequisites` and disagreeing `skip_existing` calls had to be
  refused in either order. The described behavior is real -- a consumer can be
  queued without a dependency on a producer a later call reruns, and can read
  the stale output -- but it is **not a defect**. It is a partial rerun of a
  research pipeline, and kwdagger makes no guarantee about data flow when
  nodes are reinvoked. This lesson was written one commit before the whole
  mechanism was deleted, and is the clearest example in this file of a
  correctly-reasoned safeguard for a guarantee the project does not offer.
  - **Applies when:** never, as written. Read it beside the
    "indistinguishable, so we cannot know which the user meant" lesson below,
    which is the general form of the mistake.

- **Lesson:** A comment asserting an invariant is a claim, and claims rot.
  `_runtime.py` said reversing two calls that differ in `skip_existing` leaves
  the same queue, and `AGENTS.md` repeated it. It was never tested and it was
  false. When a design note states a property, write the test in the same
  commit or write the note as an intention rather than a fact.
  - **Applies when:** documenting why something is safe to exclude from a
    check.

- **Lesson:** "These two requests are indistinguishable, so we cannot know
  which the user meant" is usually wrong. They meant the first one. Kwdagger
  spent four review rounds turning first-request-wins -- the correct behavior
  for a parameter-grid runner -- into a series of rejections, each
  individually well-argued from the premise that order-dependent selection is
  a defect. It is not. Deduplication is core behavior; rejection is an
  optional diagnostic. Before adding a safeguard, ask whether it is needed to
  execute the ordered grid in *this* compilation, or whether it is enforcing
  artifact integrity or coherence across independent executions. The latter is
  out of scope.
  - **Evidence / MWE:** `tests/test_duplicate_policy.py`; the policy section
    of `AGENTS.md`.
  - **Applies when:** two legitimate requests differ in something the identity
    model deliberately excludes.

- **Lesson:** Review pressure is directional, and reviewers optimize for the
  system they imagine. Two thorough external reviews of the authority refactor
  both pushed toward stricter arbitration, and both were internally correct --
  about a workflow engine with data-integrity guarantees, which kwdagger is
  not and does not want to be. Neither asked whether the guarantee was in
  scope. When a review says "add a check", the first question is whether the
  project promises the thing the check protects.
  - **Applies when:** acting on review findings that expand a contract rather
    than fix a violation of one.

- **Lesson:** Do not classify a policy rejection as an internal consistency
  error. The old arbitration raised `AssertionError: Internal consistency
  error` for two requests that finalized different commands under one
  identity. That is two legitimate requests differing -- policy -- but the
  wording made it look like a defect nobody could argue with, and it survived
  several reviews unquestioned for that reason. Reserve internal errors for
  contradictions *within* one request or in the compiled graph.
  - **Evidence / MWE:** `tests/test_duplicate_policy.py`, the internal
    invariants section.
  - **Applies when:** choosing an exception type for a check you are adding.
