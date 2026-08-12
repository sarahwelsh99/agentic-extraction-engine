"""Phase 3: Quality Feedback Loop

This phase validates extraction quality on a sample of the full dataset
and provides feedback for improvement:

1. Sample execution: Run validated extractors on sample batch
2. Quality evaluation: LLM judges extraction quality
3. Feedback: Identify failure patterns
4. Decision: Approve for Phase 4, or feed back to Phase 1 for improvement

Key modules:
- sampler.py: Sample data for quality evaluation
- evaluator.py: LLM-driven quality judgment
- coordinator.py: Iteration control and feedback loops
"""
