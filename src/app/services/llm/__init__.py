"""LLM client implementations for the citizen-objections-rag pipeline.

OpenAIClient is the active implementation. AnthropicClient and
MistralClient exist as reference templates and must be imported
directly from their modules when activated, to avoid import-time
failures from optional SDK dependencies.
"""

from .openai_client import OpenAIClient

__all__ = ["OpenAIClient"]
