"""Compatibility entry point; use :mod:`memory_eval_tests.offline.performance_audit`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.offline.performance_audit")
if __name__ == "__main__":
    main()
