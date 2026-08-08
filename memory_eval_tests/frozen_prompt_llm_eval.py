"""Compatibility entry point; use :mod:`memory_eval_tests.experiments.frozen_prompt_llm_eval`."""
from memory_eval_tests._compat import reexport
reexport(globals(), "memory_eval_tests.experiments.frozen_prompt_llm_eval")
if __name__ == "__main__":
    main()
