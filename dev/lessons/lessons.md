# Lessons

Confirmed, reusable lessons only. See `AGENTS.md` for the format and the bar.

- **Lesson:** A `ProcessNode` memoizes `predecessor_process_nodes` while it is
  still being constructed, before any connection exists, and only `configure`
  clears that cache. So on a *template* (unconfigured) pipeline the predecessor
  query answers with a stale empty list. `Pipeline.build_nx_graphs` sees an
  ordinary output-to-input edge only because it also walks
  `successor_process_nodes`, whose cache is not populated during construction.
  Anything that must appear in the template graph has to be read from the ports
  directly, not from the memoized query — and the two directions in
  `build_nx_graphs` are not redundant.
  - **Evidence / MWE:** `dev/lessons/mwe/template_predecessor_staleness.py`;
    the docstring on `ProcessNode.successor_process_nodes`; commit
    "Recover produced lineage through input aliases".
  - **Applies when:** adding a new kind of execution edge, or considering
    folding the predecessor and successor passes in `build_nx_graphs` together.

- **Lesson:** Generated `invoke.sh` commands and the demo pipeline invoke
  `python`, not `sys.executable`. If the active virtualenv's `bin` is not on
  `PATH`, the end-to-end tests fail with empty output directories rather than
  with an import error, which reads as a code regression. Run the suite with
  the venv's `bin` on `PATH`, not by calling `.venv/bin/python` directly.
  - **Evidence / MWE:** `python3 -m pytest tests/` gives 4 failures at a green
    commit; `PATH=.venv/bin:$PATH pytest tests/` passes. Recorded in
    `dev/journals/claude.md` (2026-08-03).
  - **Applies when:** triaging an unexpectedly red baseline in this repo.
