"""Compatibility entry point; use :mod:`memory_eval_tests.experiments.evidence_selector_experiment`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.experiments.evidence_selector_experiment")
if __name__ == "__main__":
    main()
