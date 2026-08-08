"""Compatibility entry point; use :mod:`memory_eval_tests.experiments.structure_ablation`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.experiments.structure_ablation")
if __name__ == "__main__":
    main()
