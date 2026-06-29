"""Agent orchestration for natural-language planning questions.

Classifies whether a question is in scope, routes it to the right tool, calls
deterministic data/business functions, asks the LLM to explain retrieved facts,
and logs each action for auditability.
"""
