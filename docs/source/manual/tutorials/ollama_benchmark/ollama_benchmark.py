#!/usr/bin/env python3
r"""
Benchmark Ollama models (TTFT + latency + tokens/sec, optional concurrency).

Intended to be callable:
  - directly:  python ollama_benchmark.py ...
  - via kwdagger.ProcessNode executable

Example
-------

.. code:: bash

    python ollama_benchmark.py \
      --prompt_fpath prompts.yaml \
      --dst_dpath runs/manual_llama3_8b \
      --model llama3:8b \
      --cold_trials 1 \
      --warm_trials 3 \
      --concurrency 4 \
      --append_jsonl runs/all_trials.jsonl

Prompt File Format (YAML)
-------------------------

We expect a YAML file with a top-level ``prompts`` list. Each entry should
contain at least ``text`` and optionally ``id``:

.. code:: yaml

    prompts:
      - id: short
        text: "Summarize this in one sentence: Artificial intelligence research has accelerated rapidly..."
      - id: long
        text: |
          You are a helpful assistant. Summarize the following passage in 3–5 sentences.
          Focus on clarity, conciseness, and capturing the core ideas.

          ---
          Artificial intelligence research has accelerated rapidly over the last decade...
          ---

If ``id`` is missing, a default of ``prompt_{index}`` is used.

Cold vs Warm
------------

- Cold trials:
    - Never use concurrency.
    - Always use *only the first prompt* in the YAML list.
    - Require a ``cold_reset_cmd`` that restarts Ollama and waits for it to be ready.

- Warm trials:
    - Use all prompts in the YAML.
    - May use concurrency (via ub.JobPool) if ``concurrency > 1``.
"""

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Dict, List

import kwutil
import safer
import requests
import scriptconfig as scfg
import ubelt as ub


@dataclass
class TrialResult:
    trial_id: str
    trial_idx: int
    cold_start: bool
    status: str
    error: str | None
    timestamp: str
    model: str
    # prompt info
    prompt_text_len: int
    prompt_tokens: int | None
    # latency / throughput
    ttft_sec: float | None
    latency_total_sec: float | None
    response_tokens: int | None
    tokens_per_sec: float | None
    # raw Ollama timing (nanoseconds)
    total_duration_ns: int | None
    load_duration_ns: int | None
    prompt_eval_duration_ns: int | None
    eval_duration_ns: int | None
    # extra metadata
    context_len: int | None
    done_reason: str | None


class OllamaBenchmarkCLI(scfg.DataConfig):
    """
    CLI config for a single benchmark run.

    This is deliberately "flat" so kwdagger can just pass params as CLI flags.
    """

    # --- IO paths ---
    prompt_fpath = scfg.Value(
        None,
        help="Path to a YAML prompt file with a top-level 'prompts' list.",
    )
    dst_fpath = scfg.Value(
        None,
        help="Path to main JSON output (info + result). "
             "If not given, derived from dst_dpath.",
    )
    dst_dpath = scfg.Value(
        ".",
        help="Output directory (used if dst_fpath is not specified).",
    )
    append_jsonl = scfg.Value(
        None,
        help="Optional JSONL file to append per-trial rows for long-term analysis.",
    )

    # --- Model / server config ---
    model = scfg.Value("llama3:8b", help="Ollama model name/tag.")
    ollama_url = scfg.Value("http://localhost:11434", help="Base URL to Ollama.")

    # --- Benchmark behavior ---
    cold_trials = scfg.Value(
        1,
        type=int,
        help="Number of cold trials (using only the first prompt). "
             "Requires cold_reset_cmd.",
    )
    warm_trials = scfg.Value(
        1,
        type=int,
        help="Number of warm trials PER prompt.",
    )
    cold_reset_cmd = scfg.Value(
        None,
        help="Shell command to execute before EACH cold trial "
             "(e.g. 'docker compose restart ollama && sleep 15').",
    )
    concurrency = scfg.Value(
        0,
        type=int,
        help="Number of concurrent warm requests. "
             "0 and 1 means no concurrency; cold trials are always non-concurrent.",
    )
    prompt_id = scfg.Value(
        None,
        help=(
            "Optional base label for prompts. If provided, each prompt id will be "
            "'{prompt_id}:{local_id}'. Otherwise '{prompt_file_stem}:{local_id}'."
        ),
    )

    # --- Meta / bookkeeping ---
    notes = scfg.Value(
        "",
        help="Free-form notes about this run (driver version, experiment tag, etc).",
    )

    def __post_init__(self):
        self.prompt_fpath = ub.Path(self.prompt_fpath)
        self.dst_dpath = ub.Path(self.dst_dpath)
        if self.dst_fpath is not None:
            self.dst_fpath = ub.Path(self.dst_fpath)
        if self.append_jsonl is not None:
            self.append_jsonl = ub.Path(self.append_jsonl)
        else:
            # TODO: be nicer about default paths
            self.append_jsonl = self.dst_dpath / 'trials.jsonl'
        return self

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True, verbose="auto")

        if config.cold_trials and not config.cold_reset_cmd:
            raise Exception("cold_trials > 0 requires a cold_reset_cmd")

        data: Dict[str, Any] = {
            "info": [],
            "result": None,
        }

        proc_context = kwutil.ProcessContext(
            name="ollama_benchmark",
            type="process",
            config=kwutil.Json.ensure_serializable(dict(config)),
            track_emissions=False,
        )
        proc_context.start()

        trials: List[TrialResult] = []

        # ---- Load prompts from YAML ----
        prompt_data = kwutil.Yaml.load(config.prompt_fpath)
        if isinstance(prompt_data, dict) and "prompts" in prompt_data:
            raw_prompts = prompt_data["prompts"]
        elif isinstance(prompt_data, list):
            raw_prompts = prompt_data
        else:
            raise ValueError(
                f"Expected YAML to be a dict with 'prompts' key or a list, got: {type(prompt_data)}"
            )

        prompts = []
        base_prefix = config.prompt_id or config.prompt_fpath.stem

        for idx, item in enumerate(raw_prompts):
            if isinstance(item, dict):
                local_id = item.get("id") or item.get("name") or f"prompt_{idx}"
                text = item.get("text")
            else:
                local_id = f"prompt_{idx}"
                text = str(item)

            if not text:
                continue

            full_id = f"{base_prefix}:{local_id}"
            text_len = len(text)

            prompts.append(
                {
                    "id": full_id,
                    "local_id": local_id,
                    "text": text,
                    "text_len": text_len,
                }
            )

        if not prompts:
            raise ValueError("No prompts loaded from YAML file")

        # First prompt is special for cold start
        first_prompt = prompts[0]

        trial_idx_counter = 0
        pman = kwutil.ProgressManager()
        with pman:
            # ---- Cold trials: first prompt only, always sequential, no concurrency ----
            if config.cold_trials > 0:
                for _ in pman.progiter(
                    range(config.cold_trials), desc="Run Cold Trials"
                ):
                    trials.append(
                        _run_single_request(
                            cold=True,
                            trial_idx=trial_idx_counter,
                            prompt=first_prompt,
                            cold_reset_cmd=config.cold_reset_cmd,
                            model=config.model,
                            ollama_url=config.ollama_url,
                        )
                    )
                    trial_idx_counter += 1

            # ---- Warm trials: all prompts, optional concurrency ----
            warm_specs = []
            for prompt in prompts:
                for _ in range(config.warm_trials):
                    warm_specs.append((trial_idx_counter, prompt))
                    trial_idx_counter += 1

            if warm_specs:
                # Concurrent warm trials via JobPool (threads)
                pool = ub.JobPool(mode="thread", max_workers=config.concurrency)
                for trial_idx, prompt in pman.progiter(
                    warm_specs, desc="Submit Warm Trials"
                ):
                    pool.submit(
                        _run_single_request,
                        cold=False,
                        trial_idx=trial_idx,
                        prompt=prompt,
                        cold_reset_cmd=config.cold_reset_cmd,
                        model=config.model,
                        ollama_url=config.ollama_url,
                    )

                for job in pool.as_completed(desc="Collect Warm Trials"):
                    trials.append(job.result())

        # ---- Aggregate metrics over successful trials ----
        ok_trials = [t for t in trials if t.status == "ok"]
        if ok_trials:
            ttfts = [t.ttft_sec for t in ok_trials if t.ttft_sec is not None]
            lats = [t.latency_total_sec for t in ok_trials if t.latency_total_sec is not None]
            tps_list = [t.tokens_per_sec for t in ok_trials if t.tokens_per_sec is not None]

            prompt_lens = [t.prompt_text_len for t in ok_trials]
            prompt_token_counts = [t.prompt_tokens for t in ok_trials if t.prompt_tokens is not None]
            eval_token_counts = [t.response_tokens for t in ok_trials if t.response_tokens is not None]

            total_durs = [t.total_duration_ns for t in ok_trials if t.total_duration_ns is not None]
            load_durs = [t.load_duration_ns for t in ok_trials if t.load_duration_ns is not None]
            prompt_eval_durs = [t.prompt_eval_duration_ns for t in ok_trials if t.prompt_eval_duration_ns is not None]
            eval_durs = [t.eval_duration_ns for t in ok_trials if t.eval_duration_ns is not None]
            ctx_lens = [t.context_len for t in ok_trials if t.context_len is not None]
        else:
            ttfts = lats = tps_list = []
            prompt_lens = prompt_token_counts = eval_token_counts = []
            total_durs = load_durs = prompt_eval_durs = eval_durs = ctx_lens = []

        def _safe_mean(xs):
            return mean(xs) if xs else None

        metrics = {
            "ttft_mean": _safe_mean(ttfts),
            "ttft_min": min(ttfts) if ttfts else None,
            "ttft_max": max(ttfts) if ttfts else None,

            "latency_total_mean": _safe_mean(lats),
            "latency_total_min": min(lats) if lats else None,
            "latency_total_max": max(lats) if lats else None,

            "tokens_per_sec_mean": _safe_mean(tps_list),

            # prompt / token stats
            "prompt_text_len_mean": _safe_mean(prompt_lens),
            "prompt_tokens_mean": _safe_mean(prompt_token_counts),
            "eval_tokens_mean": _safe_mean(eval_token_counts),

            # raw Ollama timings (still in ns)
            "total_duration_mean_ns": _safe_mean(total_durs),
            "load_duration_mean_ns": _safe_mean(load_durs),
            "prompt_eval_duration_mean_ns": _safe_mean(prompt_eval_durs),
            "eval_duration_mean_ns": _safe_mean(eval_durs),

            "context_len_mean": _safe_mean(ctx_lens),

            "num_trials": len(trials),
            "num_ok_trials": len(ok_trials),
        }

        data["result"] = {
            "trials": [asdict(t) for t in trials],
            "metrics": metrics,
            "config_summary": {
                "model": config.model,
                "prompt_file": str(config.prompt_fpath),
                "ollama_url": config.ollama_url,
                "cold_trials": config.cold_trials,
                "warm_trials": config.warm_trials,
                "concurrency": config.concurrency,
                "notes": config.notes,
            },
        }

        obj = proc_context.stop()
        data["info"].append(obj)

        # Determine primary output path
        if config.dst_fpath is not None:
            dst_fpath = config.dst_fpath
        else:
            dst_fpath = config.dst_dpath / "ollama_benchmark.json"

        # Optional append-only JSONL per-trial log
        if config.append_jsonl is not None:
            config.append_jsonl.parent.ensuredir()
            with config.append_jsonl.open("a", encoding="utf8") as f:
                for t in trials:
                    row = {
                        **asdict(t),
                        "notes": config.notes,
                    }
                    f.write(json.dumps(row) + "\n")
            print(f"Appended {len(trials)} rows to JSONL: {config.append_jsonl}")

        dst_fpath.parent.ensuredir()
        # Atomic write: write to a temp file and then replace the target on success
        with safer.open(dst_fpath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Wrote benchmark JSON to: {dst_fpath}")

        return data


def _run_single_request(
    *,
    cold: bool,
    trial_idx: int,
    prompt: Dict[str, Any],
    cold_reset_cmd=None,
    ollama_url=None,
    model=None,
) -> TrialResult:

    prompt_id = prompt["id"]
    prompt_text = prompt["text"]
    prompt_len = prompt["text_len"]

    trial_id = f"{'cold' if cold else 'warm'}_{trial_idx}"
    status = "ok"
    error = None
    ttft = None
    total_latency = None

    prompt_tokens = None
    resp_tokens = None
    tps = None

    total_duration_ns = None
    load_duration_ns = None
    prompt_eval_duration_ns = None
    eval_duration_ns = None
    context_len = None
    done_reason = None

    # Optional cold reset between cold trials (never concurrent)
    if cold and cold_reset_cmd:
        subprocess.run(
            cold_reset_cmd,
            shell=True,
            check=False,
        )
        if not _wait_for_ollama(ollama_url, timeout=60):
            status = "error"
            error = "Ollama did not become ready after cold_reset_cmd"
            return TrialResult(
                trial_id=trial_id,
                trial_idx=trial_idx,
                prompt_id=prompt_id,
                prompt_text_len=prompt_len,
                prompt_text_ntokens=None,
                cold_start=cold,
                status=status,
                error=error,
                timestamp=kwutil.datetime.now().isoformat(),
                model=model,
                ttft_sec=None,
                latency_total_sec=None,
                response_tokens=None,
                tokens_per_sec=None,
            )

    url = ollama_url.rstrip("/") + "/api/generate"
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": model,
        "prompt": prompt_text,
        "stream": True,  # streaming is more realistic and efficient
    }

    try:
        t0 = time.time()
        ttft_seen = False

        with requests.post(
            url,
            headers=headers,
            data=json.dumps(payload),
            stream=True,
            timeout=600,
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line.decode("utf-8"))
                if "response" in msg:
                    if not ttft_seen:
                        ttft = time.time() - t0
                        ttft_seen = True
                if msg.get("done"):
                    # final message: include server-side stats
                    total_duration_ns = msg.get("total_duration")
                    load_duration_ns = msg.get("load_duration")
                    prompt_tokens = msg.get("prompt_eval_count")
                    prompt_eval_duration_ns = msg.get("prompt_eval_duration")
                    resp_tokens = msg.get("eval_count")
                    eval_duration_ns = msg.get("eval_duration")
                    done_reason = msg.get("done_reason")
                    ctx = msg.get("context")
                    if isinstance(ctx, list):
                        context_len = len(ctx)
                    break

        total_latency = time.time() - t0

        if total_latency > 0 and resp_tokens is not None:
            tps = resp_tokens / total_latency

    except Exception as ex:
        status = "error"
        error = str(ex)

    return TrialResult(
        trial_id=trial_id,
        trial_idx=trial_idx,
        cold_start=cold,
        status=status,
        error=error,
        timestamp=kwutil.datetime.now().isoformat(),
        model=model,
        prompt_text_len=prompt_len,
        prompt_tokens=prompt_tokens,
        ttft_sec=ttft,
        latency_total_sec=total_latency,
        response_tokens=resp_tokens,
        tokens_per_sec=tps,
        total_duration_ns=total_duration_ns,
        load_duration_ns=load_duration_ns,
        prompt_eval_duration_ns=prompt_eval_duration_ns,
        eval_duration_ns=eval_duration_ns,
        context_len=context_len,
        done_reason=done_reason,
    )


def _wait_for_ollama(url: str, timeout: float = 60.0, interval: float = 2.0) -> bool:
    base = url.rstrip("/")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(base + "/api/tags", timeout=3)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


__cli__ = OllamaBenchmarkCLI

if __name__ == "__main__":
    __cli__.main()
