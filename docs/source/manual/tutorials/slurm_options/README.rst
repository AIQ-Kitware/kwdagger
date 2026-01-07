Slurm options tutorial
======================

This tutorial demonstrates how to pass SLURM options through ``kwdagger`` at
three levels:

* **Global queue defaults** via ``slurm_options`` in the top level parameters.
* **Per-node overrides in YAML** via ``<node>.__slurm_options__``.
* **Per-node defaults in code** via the ``slurm_options`` attribute on
  ``ProcessNode`` subclasses or instances.

The example pipeline defined in ``example_user_module/pipelines.py`` contains a
CPU preprocessing step and a Torch-based step that can leverage GPUs when
available. Everything you need is inside this folder.

Prerequisites
-------------

* Python environment with ``kwdagger`` and ``cmd_queue`` installed.
* PyTorch (CPU or GPU build) to run the ``torch_infer`` node.
* SLURM is **not required** for the dry run shown below.

Files in this tutorial
----------------------

* ``data/input.json`` - tiny input used by the pipeline.
* ``example_user_module/cli/cpu_prepare.py`` - lightweight CPU-only preprocessing.
* ``example_user_module/cli/torch_infer.py`` - Torch script that prefers GPU if
  available.
* ``example_user_module/pipelines.py`` - pipeline wiring and node definitions,
  including per-node ``slurm_options`` defaults.

Per-node defaults in code
-------------------------

Note that the pipeline code can specify defaults for slurm. The file
``example_user_module/pipelines.py`` sets ``slurm_options`` on the Torch node
so that GPU settings apply even without YAML overrides. The YAML layer still
wins if you provide per-node options in the grid.

Running a dry run (no SLURM needed)
-----------------------------------

The fastest way to understand SLURM option layering in ``kwdagger`` is to render
the generated ``sbatch`` commands first (without submitting anything). In a dry
run, you can include:

* **Global queue defaults** via a top-level ``slurm_options`` dictionary (applies to every job).
* **Per-node defaults in code** via the ``slurm_options`` attribute on a node (used unless overridden in YAML).
* **Per-node YAML overrides** via ``<node>.__slurm_options__`` (only affects that node).

Run the command below to generate the SLURM scripts:

.. code:: bash

    export PYTHONPATH=.
    kwdagger schedule \
      --backend=slurm \
      --root_dpath="$PWD/results_gpu" \
      --virtualenv_cmd "echo 'source your venv if you want'" \
      --params="
        pipeline: 'example_user_module.pipelines.build_pipeline()'
        # Specify global slurm options for all nodes.
        slurm_options:
          partition: debug
        matrix:
          cpu_prepare.src_fpath:
            - data/input1.json
            - data/input2.json
          # Specify slurm options for a specific node:
          torch_infer.__slurm_options__:
            gres: 'gpu:1'
            time: '00:10:00'
      " \
      --run=0

NOTE: ``virtualenv_cmd`` is broken in ``cmd_queue<0.3.0`` and will be reworked
to a general ``preamble`` in the future.

Now inspect the generated job scripts / ``sbatch`` commands under
``$PWD/results`` and confirm the merge behavior you expect:

* The global queue defaults in ``__slurm_options__`` (e.g. ``partition``, ``qos``) appear on every job.
* The Torch node gets its YAML overrides (e.g. ``gres``, ``time``) in addition to any defaults set in ``example_user_module/pipelines.py`` via the node's ``slurm_options``.
* Any per-node defaults defined in code remain in effect unless explicitly overridden in the YAML grid.

If you have a GPU and slurm with the appropriate partitions you can modify the
``--run=0`` to ``--run=1`` and actually execute the jobs.


Running on a SLURM cluster
--------------------------

Set ``--run=1`` to submit jobs. Ensure your PyTorch build matches the available
hardware. If GPUs are unavailable, set ``torch_infer.__slurm_options__`` to omit
``gres`` and pass ``torch_infer.device: cpu`` in the matrix so the node runs on
CPU.

For example, a CPU-only run looks like:

.. code:: bash

    export PYTHONPATH=.
    kwdagger schedule \
      --backend=slurm \
      --root_dpath="$PWD/results_cpu" \
      --params="
        pipeline: 'example_user_module.pipelines.build_pipeline()'
        matrix:
          cpu_prepare.src_fpath:
            - data/input1.json
          torch_infer.device:
            - cpu
          torch_infer.__slurm_options__: {}
      " \
      --run=1

If you prefer to stay entirely local, swap ``--backend=slurm`` with
``--backend=serial`` to bypass SLURM altogether.
