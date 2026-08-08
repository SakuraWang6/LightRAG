"""Compatibility entry point; use :mod:`memory_eval_tests.reporting.scale_report`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.reporting.scale_report")
if __name__ == "__main__":
    main()
