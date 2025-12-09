"""
Ollama benchmark pipeline for kwdagger.

This mirrors the tutorial structure: a ProcessNode that runs a scriptconfig CLI,
and exposes summary metrics via load_result() for aggregation.
"""

import kwdagger
import ubelt as ub


# Reuse the EXAMPLE_DPATH pattern from the tutorial so we can run this
# both inside the installed example and from a dev checkout.
try:
    EXAMPLE_DPATH = ub.Path(__file__).parent
except NameError:
    EXAMPLE_DPATH = ub.Path(".").resolve()


class OllamaBenchmark(kwdagger.ProcessNode):
    """
    Run the Ollama benchmark CLI and expose metrics for aggregation.

    The CLI executable is cli/ollama_benchmark.py in this same package.
    """

    name = "ollama_benchmark"
    executable = f"python {EXAMPLE_DPATH}/ollama_benchmark.py"

    # The inputs / outputs here must match the scriptconfig field names
    # in OllamaBenchmarkCLI.
    in_paths = {
        "prompt_fpath",
    }
    out_paths = {
        "dst_fpath": "ollama_benchmark.json",
        "dst_dpath": ".",
    }
    primary_out_key = "dst_fpath"

    # algo_params are knobs you might want to sweep logically (model, prompt_id, etc).
    algo_params = {
        "model": "llama3:8b",
        "cold_trials": 1,
        "warm_trials": 3,
        "ollama_url": "http://localhost:11434",
        "cold_reset_cmd": None,
    }

    def load_result(self, node_dpath):
        """
        Return metrics and configuration in a flattened dictionary.

        We follow the same pattern as SentimentEvaluate in the tutorial:
        - use new_process_context_parser on the ProcessContext object
        - attach our metrics under 'metrics'
        - flatten with util_dotdict
        """
        import json
        from kwdagger.aggregate_loader import new_process_context_parser
        from kwdagger.utils import util_dotdict

        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())

        # Last ProcessContext record (there's usually only one)
        proc_item = result["info"][-1]
        nest_resolved = new_process_context_parser(proc_item)

        # Attach our benchmark metrics
        nest_resolved["metrics"] = result["result"]["metrics"]

        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved

    def default_metrics(self):
        """
        Tell kwdagger which metrics exist and their objectives.
        """
        metric_infos = [
            {
                "metric": "ttft_mean",
                "objective": "minimize",
                "primary": True,
                "display": True,
            },
            {
                "metric": "latency_total_mean",
                "objective": "minimize",
                "primary": False,
                "display": True,
            },
            {
                "metric": "tokens_per_sec_mean",
                "objective": "maximize",
                "primary": False,
                "display": True,
            },
        ]
        return metric_infos

    @property
    def default_vantage_points(self):
        """
        Example vantage point: TTFT vs throughput.
        """
        vantage_points = [
            {
                "metric1": "metrics.ollama_benchmark.ttft_mean",
                "metric2": "metrics.ollama_benchmark.tokens_per_sec_mean",
            },
        ]
        return vantage_points


def ollama_benchmark_pipeline():
    """
    Create the one-node benchmark pipeline.

    This is what you'll point kwdagger's scheduler at.
    """
    nodes = {
        "ollama_benchmark": OllamaBenchmark(),
    }
    dag = kwdagger.Pipeline(nodes)
    dag.build_nx_graphs()
    return dag
