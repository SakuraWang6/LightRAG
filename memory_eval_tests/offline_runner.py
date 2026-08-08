"""Compatibility entry point; use :mod:`memory_eval_tests.offline.offline_runner`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.offline.offline_runner")
if __name__ == "__main__":
    main()
