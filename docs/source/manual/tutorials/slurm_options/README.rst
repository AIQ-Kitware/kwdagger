Slurm options tutorial
======================

This tutorial demonstrates how to pass SLURM options through ``kwdagger`` at
three levels:

* **Global queue defaults** via ``__slurm_options__`` in the parameter grid.
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

Prepare your shell
------------------

From this folder, make the tutorial module importable:

.. code:: bash

    cd "$(dirname "$0")"
    export PYTHONPATH=.

Global queue options
--------------------

Add a ``__slurm_options__`` dictionary at the top level of your parameter grid
to set options for every job. These map directly to ``sbatch`` arguments such
as ``partition``, ``qos``, and ``account``.

.. code:: yaml

    __slurm_options__:
      partition: debug
      qos: normal
    pipeline: 'example_user_module.pipelines.build_pipeline()'
    matrix:
      cpu_prepare.src_fpath:
        - data/input.json

Per-node YAML overrides
-----------------------

Individual nodes can override or extend global options by adding
``<node_name>.__slurm_options__`` entries inside the matrix. Here, the Torch
step requests a GPU and a short walltime.

.. code:: yaml

    matrix:
      cpu_prepare.src_fpath:
        - data/input.json
      torch_infer.__slurm_options__:
        - gres: 'gpu:1'
          time: '00:10:00'

Per-node defaults in code
-------------------------

``example_user_module/pipelines.py`` sets ``slurm_options`` on the Torch node so
that GPU settings apply even without YAML overrides. The YAML layer still wins
if you provide per-node options in the grid.

Running a dry run (no SLURM needed)
-----------------------------------

Render the SLURM script without submitting jobs. This shows how global and
per-node options combine.

.. code:: bash

    kwdagger schedule \
      --backend=slurm \
      --run=0 \
      --root_dpath="$PWD/results" \
      --params="
        __slurm_options__:
          partition: debug
        pipeline: 'example_user_module.pipelines.build_pipeline()'
        matrix:
          cpu_prepare.src_fpath:
            - data/input.json
          torch_infer.__slurm_options__:
            - gres: 'gpu:1'
              time: '00:10:00'
      "

Inspect the generated ``sbatch`` commands to verify that:

* Global options (``partition``) appear on every job.
* ``torch_infer`` includes GPU and time settings.
* Per-node defaults defined in code remain in effect unless overridden in YAML.

Running on a SLURM cluster
--------------------------

Set ``--run=1`` to submit jobs. Ensure your PyTorch build matches the available
hardware. If GPUs are unavailable, set ``torch_infer.__slurm_options__`` to omit
``gres`` and pass ``torch_infer.device: cpu`` in the matrix so the node runs on
CPU.

For example, a CPU-only run looks like:

.. code:: bash

    kwdagger schedule \
      --backend=slurm \
      --run=1 \
      --root_dpath="$PWD/results" \
      --params="
        pipeline: 'example_user_module.pipelines.build_pipeline()'
        matrix:
          cpu_prepare.src_fpath:
            - data/input.json
          torch_infer.device:
            - cpu
          torch_infer.__slurm_options__:
            - {}
      "

If you prefer to stay entirely local, swap ``--backend=slurm`` with
``--backend=serial`` to bypass SLURM altogether.
