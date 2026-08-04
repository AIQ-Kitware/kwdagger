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

- **Lesson:** Deferring a type conversion to the serializer makes the readers
  disagree. JSON object names are strings, and `json.dumps` renames an `int`
  key silently, refuses a `Path` key, and cannot sort a mixture -- so a mapping
  key left unconverted hashes as one thing, compares as another, and persists
  as a third (or crashes). Normalizing at the point the value is stored gives
  the stored configuration, the identity payload, the requested record, and the
  file on disk one answer, and turns `1` versus `'1'` into a reportable
  collision rather than a silent overwrite.
  - **Evidence / MWE:** `tests/test_identity_model.py`
    `test_a_pathlike_mapping_key_survives_submission` and
    `test_keys_that_name_one_json_key_are_refused`; `_json_object_key`.
  - **Applies when:** adding a value shape to configuration, or relying on a
    serializer to coerce something the rest of the system also reads.
