"""Compatibility entry point; use :mod:`memory_eval_tests.online.index_runner`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.online.index_runner")
if __name__ == "__main__":
    main()
