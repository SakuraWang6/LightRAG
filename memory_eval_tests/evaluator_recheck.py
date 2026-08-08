"""Compatibility entry point; use :mod:`memory_eval_tests.experiments.evaluator_recheck`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.experiments.evaluator_recheck")
if __name__ == "__main__":
    main()
