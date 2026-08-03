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
