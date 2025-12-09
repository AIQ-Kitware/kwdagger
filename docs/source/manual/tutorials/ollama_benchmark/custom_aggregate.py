#!/usr/bin/env python3
r"""
Custom aggregator for ollama_benchmark.json files.

This script:

* Recursively scans a root directory for ollama_benchmark.json files.
* Loads each file, reads:
    - `info`     (ProcessContext: config, machine info, timestamps)
    - `result`:
        - `trials`  (per-trial measurements)
        - `metrics` (run-level aggregate metrics)
* Produces a per-trial pandas.DataFrame with:
    - trial_* fields
    - cfg_* columns (run configuration)
    - machine_* columns (hardware / OS)
    - run_metric_* columns (aggregate metrics duplicated per trial)
    - bookkeeping columns (run_uuid, run_start_timestamp, source_fpath, etc.)

Usage
-----

.. code:: bash

    python custom_aggregate.py \
        --pattern="results_ollama/ollama_benchmark/*/ollama_benchmark.json"
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pandas as pd
import scriptconfig as scfg
import ubelt as ub
import rich
import glob


class OllamaCustomAggregateConfig(scfg.DataConfig):
    """
    CLI configuration for the ollama benchmark aggregator.
    """

    pattern = scfg.Value(
        "**/ollama_benchmark.json",
        help="Glob pattern (relative to root_dpath) to match benchmark JSON files.",
    )

    @classmethod
    def main(cls, argv=True, **kwargs):
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose="auto")

        df = aggregate_ollama_runs(pattern=config.pattern)

        rich.print(f"[green]Loaded {len(df)} trial rows[/green]")

        if len(df) > 0:
            # Show a quick preview
            with pd.option_context("display.max_columns", 20, "display.width", 120):
                print(df.head())

        # Do aggregation

        group_keys = ['config.model', 'machine.host', 'cold_start', 'config.concurrency']

        for group_values, group in df.groupby(group_keys):
            group_id = dict(zip(group_keys, group_values))
            print(f'group_id={group_id}')
            description = group.describe()
            print(description)

        return df


def aggregate_ollama_runs(
    pattern: str = "**/ollama_benchmark.json",
) -> pd.DataFrame:
    """
    Recursively glob `ollama_benchmark.json` files and aggregate them into
    a per-trial pandas DataFrame.

    Args:
        pattern: glob pattern.
        limit: optional max number of files to load.

    Returns:
        pandas.DataFrame with one row per trial.
    """
    # Use ubelt's glob which returns ub.Path objects
    all_files = sorted(map(ub.Path, glob.glob(pattern, recursive=True)))

    if not all_files:
        rich.print(f"[yellow]No files matched pattern {pattern!r}[/yellow]")
        return pd.DataFrame([])

    rows: List[Dict[str, Any]] = []

    prog = ub.ProgIter(all_files, desc="Aggregating ollama_benchmark.json files")
    for fpath in prog:
        try:
            data = json.loads(fpath.read_text())
        except Exception as ex:
            rich.print(f"[red]Failed to load {fpath}: {ex}[/red]")
            continue

        # ---- ProcessContext info (run-level) ----
        # Typically a list with one element: info[0]['properties']
        info_list = data.get("info", [])
        if not info_list:
            rich.print(f"[yellow]No 'info' in {fpath}[/yellow]")
            continue

        # Last item is usually the relevant ProcessContext
        proc_props = info_list[-1].get("properties", {})
        machine = proc_props.get("machine", {}) or {}
        cfg = proc_props.get("config", {}) or {}
        run_uuid = proc_props.get("uuid")
        run_name = proc_props.get("name")
        start_ts = proc_props.get("start_timestamp")
        stop_ts = proc_props.get("stop_timestamp")
        duration = proc_props.get("duration")

        # ---- Result (metrics + trials) ----
        result = data.get("result", {}) or {}
        trials = result.get("trials", []) or []

        # Basic prefixing to avoid key collisions
        machine_prefixed = {f"machine.{k}": v for k, v in machine.items()}
        cfg_prefixed = {f"config.{k}": v for k, v in cfg.items()}

        base_run_info = {
            "run.uuid": run_uuid,
            "run.name": run_name,
            "run.start_timestamp": start_ts,
            "run.stop_timestamp": stop_ts,
            "run.duration": duration,
            "run.fpath": str(fpath),
        }
        base_run_info.update(machine_prefixed)
        base_run_info.update(cfg_prefixed)

        # ---- Per-trial rows ----
        for trial in trials:
            # trial is expected to already be a flat dict with fields like:
            #   trial_id, trial_idx, prompt_id, ttft_sec, latency_total_sec, etc.
            row = dict(base_run_info)
            # trial keys will be columns like 'trial_id', 'ttft_sec', etc.
            row.update(trial)
            rows.append(row)

    if not rows:
        rich.print("[yellow]No trial rows extracted[/yellow]")
        return pd.DataFrame([])

    df = pd.DataFrame(rows)
    return df


__cli__ = OllamaCustomAggregateConfig

if __name__ == "__main__":
    __cli__.main()
