# Notes for Future Agents

## Repository tour
- `kwdagger/pipeline.py` holds the `Pipeline` and `ProcessNode` abstractions along with the logic for building process and IO graphs via networkx. The demo pipeline can be constructed via `Pipeline.demo()` which wires together the nodes defined in `kwdagger/demo/demodata.py`.
- `kwdagger/schedule.py` exposes the `ScheduleEvaluationConfig` scriptconfig CLI (also accessible through `python -m kwdagger`) for expanding a YAML/JSON parameter grid and dispatching jobs with cmd_queue backends (Slurm, tmux, or serial).
- `kwdagger/aggregate.py` contains `AggregateEvluationConfig`, which loads pipeline outputs, computes parameter hash IDs, aggregates metrics, and can emit text or plotting reports. Supporting utilities live in `kwdagger/aggregate_loader.py` and `kwdagger/aggregate_plots.py`.
- `kwdagger/demo/demodata.py` is the most complete runnable example: it defines per-stage CLIs, constructs a sample pipeline, and documents end-to-end commands for scheduling and aggregating demo runs.
- Sphinx docs live under `docs/` (see `docs/source/index.rst`), including a minimal external user module in `docs/source/manual/example` that mirrors the demo.
- Tests in `tests/` cover pipeline wiring, scheduler behavior, aggregation, and import sanity checks; they are invoked by `python run_tests.py`.

## Command entry points
- ``python -m kwdagger.schedule`` / ``kwdagger schedule`` – schedule a pipeline over a parameter matrix using `ScheduleEvaluationConfig`.
- ``python -m kwdagger.aggregate`` / ``kwdagger aggregate`` – aggregate results via `AggregateEvluationConfig`.
- ``python -m kwdagger`` – modal CLI configured in `kwdagger/__main__.py` to expose the above commands.
- The demo pipeline in `kwdagger/demo/demodata.py` provides runnable CLI snippets suitable for smoke testing or onboarding.

## Development workflow
- Install dependencies with ``pip install -e . -r requirements.txt`` (a virtualenv is recommended).
- Run the full test suite (PyTest with coverage and xdoctest) via ``python run_tests.py``.
- Lint fatal errors with ``./run_linter.sh`` and execute doctests with ``./run_doctests.sh``.
- Build the Sphinx documentation with ``make -C docs html``.

## Unclear or TODO
- The README and docs reference historical names (e.g., geowatch) and some TODOs in the codebase mention future refactors; if you need authoritative guidance on pipeline registration/discovery beyond the demo, please check with maintainers.
