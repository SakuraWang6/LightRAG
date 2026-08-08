"""Compatibility entry point; use :mod:`memory_eval_tests.offline.chunk_traceability`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.offline.chunk_traceability")
if __name__ == "__main__":
    main()
