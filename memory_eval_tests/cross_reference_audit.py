"""Compatibility entry point; use :mod:`memory_eval_tests.offline.cross_reference_audit`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.offline.cross_reference_audit")
if __name__ == "__main__":
    main()
