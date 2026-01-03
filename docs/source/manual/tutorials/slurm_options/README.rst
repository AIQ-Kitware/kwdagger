Slurm options tutorial
======================

This tutorial shows how to pass SLURM options through ``kwdagger`` at three
levels of specificity:

* **Global queue defaults** via ``__slurm_options__`` in the parameter grid.
* **Per-node overrides in YAML** via ``<node>.__slurm_options__``.
* **Per-node defaults in code** via the ``slurm_options`` attribute on
  ``ProcessNode`` subclasses or instances.

The examples below use the built-in demo pipeline
``kwdagger.demo.demodata.my_demo_pipeline()`` so you do not need to create any
additional files. All commands assume you are running from this folder and want
to perform a dry run (``--run=0``) to inspect the generated SLURM script.


Prerequisites
-------------

* Python environment with ``kwdagger`` installed.
* ``cmd_queue`` is installed as a dependency; SLURM does **not** need to be
  available for the dry-run examples.


Global queue options
--------------------

Add a ``__slurm_options__`` dictionary at the top level of your parameter grid
to set options that should apply to every job. These map directly to
``sbatch`` arguments such as ``partition``, ``qos``, and ``account``.

.. code:: yaml

    __slurm_options__:
      partition: debug
      qos: normal
    pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
    matrix:
      stage1_predict.src_fpath:
        - ./input_file1.txt
      stage1_predict.param1:
        - 123
      stage1_evaluate.workers: 2

When you run ``kwdagger schedule --backend=slurm --run=0``, the generated
driver script will include these options for every submitted job.


Per-node YAML overrides
-----------------------

Individual nodes can override or extend the global options by adding a
``<node_name>.__slurm_options__`` entry inside the matrix. This is useful for
setting per-step time limits or GPU counts.

.. code:: yaml

    matrix:
      stage1_predict.src_fpath:
        - ./input_file1.txt
      stage1_predict.param1:
        - 123
      stage1_evaluate.workers: 2
      stage1_predict.__slurm_options__:
        - time: '00:20:00'     # longer for the prediction step
      stage1_evaluate.__slurm_options__:
        - gres: 'gpu:1'        # GPU requirement for evaluation only

These node-level options are forwarded directly to the corresponding
``queue.submit()`` calls, so you will see them reflected in the ``sbatch``
commands for those steps.


Per-node defaults in code
-------------------------

You can also bake in SLURM defaults on the node definition itself. Set the
``slurm_options`` class attribute (or pass it to the constructor) on your
``ProcessNode`` subclass. YAML overrides still take precedence.

.. code:: python

    import kwdagger

    class GPUHeavyNode(kwdagger.ProcessNode):
        name = 'gpu_heavy'
        executable = 'python gpu_heavy.py'
        out_paths = {'out': 'result.json'}

        # Applied to every job for this node unless YAML overrides are provided.
        slurm_options = {
            'gpus': 1,
            'time': '01:00:00',
        }

    def build_pipeline():
        return kwdagger.Pipeline({'gpu_heavy': GPUHeavyNode()})


Putting it together (dry run)
-----------------------------

Combine the snippets above into a single ``kwdagger schedule`` invocation to
confirm how options stack. This command prints the SLURM script without running
any jobs:

.. code:: bash

    kwdagger schedule \
      --backend=slurm \
      --run=0 \
      --root_dpath="$PWD/results" \
      --params="
        __slurm_options__:
          partition: debug
          qos: normal
        pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
        matrix:
          stage1_predict.src_fpath:
            - ./input_file1.txt
          stage1_predict.param1:
            - 123
          stage1_predict.__slurm_options__:
            - time: '00:15:00'
          stage1_evaluate.__slurm_options__:
            - gres: 'gpu:1'
      "

Inspect the rendered ``sbatch`` commands to verify that:

* Global options (``partition``, ``qos``) appear on every job.
* ``stage1_predict`` uses the 15-minute time limit.
* ``stage1_evaluate`` requests a GPU.
* Any ``slurm_options`` defined on node classes are still applied unless
  overridden by YAML.

Once satisfied, switch to ``--run=1`` to submit the jobs to your SLURM cluster.
