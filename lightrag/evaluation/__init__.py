"""
Standalone RAGAS compatibility module.

This is not the product evaluation workflow exposed by the WebUI and API.
That workflow lives in ``memory_eval_tests``.  The RAGAS entry point remains
available for existing standalone users while it is kept as a separate,
compatibility-focused interface.

Usage:
    from lightrag.evaluation import RAGEvaluator

    evaluator = RAGEvaluator()
    results = await evaluator.run()

Note: RAGEvaluator is imported lazily to avoid import errors
when ragas/datasets are not installed.
"""

__all__ = ["RAGEvaluator"]


def __getattr__(name):
    """Lazy import to avoid dependency errors when ragas is not installed."""
    if name == "RAGEvaluator":
        from .eval_rag_quality import RAGEvaluator

        return RAGEvaluator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
