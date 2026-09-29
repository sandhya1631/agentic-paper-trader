"""Provider-neutral LLM interface (LLM Client Factory).

Orchestration code (the future agent loop) depends only on `LLMClient`
(base.py) and `create_llm_client`/`get_llm_client` (factory.py) — never on a
specific provider. Switching between OpenAI and a local Ollama model is a
single `.env` change (`LLM_PROVIDER`), not a code change.
"""
