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
import numpy as np
import kwplot


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

        # Hack to note that 0 and 1 are the same
        df.loc[df['config.concurrency'] <= 1, 'config.concurrency'] = 0
        group_keys = ['config.model', 'cold_start', 'config.concurrency', 'machine.host']
        for group_values, group in df.groupby(group_keys):
            group_id = dict(zip(group_keys, group_values))
            print(f'group_id={group_id}')
            description = group.describe()
            print(description)

        plot_dpath = ub.Path('./plots')

        plot_ollama_overviews(df, plot_dpath)
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


def _prep_concurrency_labels(df):
    """
    Match your hack: 0 and 1 treated as '0' (no concurrency),
    but make labels nice strings for seaborn.
    """
    df = df.copy()
    df.loc[df['config.concurrency'] <= 1, 'config.concurrency'] = 0
    df['concurrency_label'] = df['config.concurrency'].astype(int).astype(str)
    return df


def plot_ollama_overviews(df, plot_dpath):
    """
    Build a few overview plots for the ollama benchmark dataframe.

    Args:
        df (pd.DataFrame): your per-trial table
        plot_dpath (PathLike): where to write PNGs
    """
    from kwdagger.utils import util_kwplot
    sns = kwplot.autosns()
    plt = kwplot.autoplt()
    plot_dpath = ub.Path(plot_dpath).ensuredir()
    df = _prep_concurrency_labels(df)

    # --- 1. TTFT by model (cold vs warm) ---
    kwplot.close_figures()
    finalize = util_kwplot.FigureFinalizer(
        dpath=plot_dpath,
        size_inches=np.array([6.4, 4.8]) * 1.0,
        verbose=True,
    )
    fig = kwplot.figure(fnum=1, doclf=True)
    ax = sns.boxplot(
        data=df,
        x="config.model",
        y="ttft_sec",
        hue="cold_start",
    )
    ax.set_title("TTFT by model (cold vs warm)")
    ax.set_xlabel("model")
    ax.set_ylabel("TTFT (s)")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    finalize.finalize(fig, "ttft_by_model_cold_vs_warm.png")

    # --- 2. Throughput vs concurrency by model (warm only) ---
    warm = df[~df["cold_start"]].copy()

    kwplot.close_figures()
    finalize = util_kwplot.FigureFinalizer(
        dpath=plot_dpath,
        size_inches=np.array([6.4, 4.8]) * 1.0,
    )
    fig = kwplot.figure(fnum=2, doclf=True)
    ax = sns.boxplot(
        data=warm,
        x="concurrency_label",
        y="tokens_per_sec",
        hue="config.model",
    )
    ax.set_title("Warm throughput vs concurrency by model")
    ax.set_xlabel("concurrency")
    ax.set_ylabel("tokens/sec")
    finalize.finalize(fig, "tps_vs_concurrency_warm_by_model.png")

    # --- 3. Latency vs throughput scatter, colored by concurrency ---
    kwplot.close_figures()
    finalize = util_kwplot.FigureFinalizer(
        dpath=plot_dpath,
        size_inches=np.array([6.4, 4.8]) * 1.0,
    )
    fig = kwplot.figure(fnum=3, doclf=True)
    ax = sns.scatterplot(
        data=warm,
        x="latency_total_sec",
        y="tokens_per_sec",
        hue="concurrency_label",
        style="config.model",
        alpha=0.7,
    )
    ax.set_title("Latency vs throughput (warm trials)")
    ax.set_xlabel("latency_total_sec (s)")
    ax.set_ylabel("tokens/sec")
    finalize.finalize(fig, "latency_vs_tps_warm_scatter.png")

    # --- 4. Prompt length vs latency (warm), colored by model ---
    kwplot.close_figures()
    finalize = util_kwplot.FigureFinalizer(
        dpath=plot_dpath,
        size_inches=np.array([6.4, 4.8]) * 1.0,
    )
    fig = kwplot.figure(fnum=4, doclf=True)
    ax = sns.scatterplot(
        data=warm,
        x="prompt_text_len",
        y="latency_total_sec",
        hue="config.model",
        alpha=0.7,
    )
    ax.set_title("Prompt length vs latency (warm trials)")
    ax.set_xlabel("prompt_text_len (chars)")
    ax.set_ylabel("latency_total_sec (s)")
    finalize.finalize(fig, "prompt_len_vs_latency_warm_scatter.png")

    # --- 5. Host comparison for a single model (example: use top model) ---
    if "config.model" in df.columns and df["config.model"].nunique() > 0:
        top_model = df["config.model"].value_counts().index[0]
        sub = df[(df["config.model"] == top_model) & (~df["cold_start"])]

        if len(sub):
            kwplot.close_figures()
            finalize = util_kwplot.FigureFinalizer(
                dpath=plot_dpath,
                size_inches=np.array([6.4, 4.8]) * 1.0,
            )
            fig = kwplot.figure(fnum=5, doclf=True)
            ax = sns.boxplot(
                data=sub,
                x="machine.host",
                y="tokens_per_sec",
                hue="concurrency_label",
            )
            ax.set_title(f"Throughput for {top_model} across hosts (warm)")
            ax.set_xlabel("machine.host")
            ax.set_ylabel("tokens/sec")
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
            finalize.finalize(fig, f"tps_by_host_{top_model.replace(':', '_')}.png")

    hosts = sorted(df['machine.host'].dropna().unique().tolist())
    cold_flags = [True, False]

    for host in hosts:
        for cold_flag in cold_flags:
            sub = df[(df['machine.host'] == host) & (df['cold_start'] == cold_flag)]
            if len(sub) == 0:
                continue

            cold_label = 'cold' if cold_flag else 'warm'
            safe_host = str(host).replace('.', '_').replace(':', '_')

            # --- 1. TTFT by model for this host + cold/warm ---
            kwplot.close_figures()
            finalize = util_kwplot.FigureFinalizer(
                dpath=plot_dpath,
                size_inches=np.array([6.4, 4.8]) * 1.0,
            )
            fig = kwplot.figure(fnum=1, doclf=True)
            ax = sns.boxplot(
                data=sub,
                x='config.model',
                y='ttft_sec',
                hue='concurrency_label',
            )
            ax.set_title(f"TTFT by model – host={host}, cold_start={cold_label}")
            ax.set_xlabel("model")
            ax.set_ylabel("TTFT (s)")
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
            fname = f"ttft_by_model_host={safe_host}_cold={cold_label}.png"
            finalize.finalize(fig, fname)

            # --- 2. Throughput (tokens/sec) by model for this host + cold/warm ---
            kwplot.close_figures()
            finalize = util_kwplot.FigureFinalizer(
                dpath=plot_dpath,
                size_inches=np.array([6.4, 4.8]) * 1.0,
            )
            fig = kwplot.figure(fnum=2, doclf=True)
            ax = sns.boxplot(
                data=sub,
                x='config.model',
                y='tokens_per_sec',
                hue='concurrency_label',
            )
            ax.set_title(f"Throughput by model – host={host}, cold_start={cold_label}")
            ax.set_xlabel("model")
            ax.set_ylabel("tokens/sec")
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
            fname = f"tps_by_model_host={safe_host}_cold={cold_label}.png"
            finalize.finalize(fig, fname)

    concs = sorted(df['concurrency_label'].dropna().unique().tolist())
    for host in hosts:
        for conc in concs:
            sub = df[(df['machine.host'] == host) &
                     (df['concurrency_label'] == conc) &
                     (~df['cold_start'])]  # warm-only for meaningful throughput

            if len(sub) == 0:
                continue

            safe_host = str(host).replace('.', '_').replace(':', '_')
            title = f"TTFT vs Throughput – host={host}, concurrency={conc}"
            fname = f"ttft_vs_tps_host={safe_host}_concurrency={conc}.png"

            kwplot.close_figures()
            finalize = util_kwplot.FigureFinalizer(
                dpath=plot_dpath,
                size_inches=np.array([6.4, 4.8]) * 1.0,
            )

            fig = kwplot.figure(doclf=True, fnum=1)
            ax = sns.scatterplot(
                data=sub,
                x="ttft_sec",
                y="tokens_per_sec",
                hue="config.model",
                alpha=0.7,
            )

            ax.set_title(title)
            ax.set_xlabel("TTFT (s)")
            ax.set_ylabel("Throughput (tokens/sec)")

            finalize.finalize(fig, fname)


__cli__ = OllamaCustomAggregateConfig

if __name__ == "__main__":
    __cli__.main()
