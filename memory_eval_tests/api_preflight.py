"""Compatibility entry point; use :mod:`memory_eval_tests.online.api_preflight`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.online.api_preflight")
if __name__ == "__main__":
    main()
