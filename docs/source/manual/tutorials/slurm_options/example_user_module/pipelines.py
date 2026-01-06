from pathlib import Path

import kwdagger


class CPUPrepare(kwdagger.ProcessNode):
    name = 'cpu_prepare'
    executable = f'python {Path(__file__).parent / "cli" / "cpu_prepare.py"}'
    in_paths = {'src_fpath'}
    out_paths = {'prepared_fpath': 'prepared.json'}
    primary_out_key = 'prepared_fpath'
    algo_params = {'uppercase': False}


class TorchInfer(kwdagger.ProcessNode):
    name = 'torch_infer'
    executable = f'python {Path(__file__).parent / "cli" / "torch_infer.py"}'
    in_paths = {'input_fpath'}
    out_paths = {'summary_fpath': 'summary.json'}
    primary_out_key = 'summary_fpath'
    algo_params = {'device': 'auto'}

    # Default SLURM options for GPU-friendly work; YAML overrides still apply.
    slurm_options = {
        'gres': 'gpu:1',
        'time': '00:15:00',
    }


def build_pipeline():
    nodes = {
        'cpu_prepare': CPUPrepare(),
        'torch_infer': TorchInfer(),
    }
    nodes['cpu_prepare'].outputs['prepared_fpath'].connect(
        nodes['torch_infer'].inputs['input_fpath']
    )
    dag = kwdagger.Pipeline(nodes)
    dag.build_nx_graphs()
    return dag
