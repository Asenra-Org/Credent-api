import os
from langchain_groq import ChatGroq as _ChatGroq

class ChatGroqWithFallback:
    def __new__(cls, *args, **kwargs):
        # Always use the environment variables to dynamically route models
        primary_model = os.getenv("PRIMARY_LLM_MODEL", "openai/gpt-oss-20b")
        fallback_model_1 = os.getenv("FALLBACK_LLM_MODEL_1", "qwen/qwen3.6-27b")
        fallback_model_2 = os.getenv("FALLBACK_LLM_MODEL_2", "openai/gpt-oss-120b")

        # Override the hardcoded model with the dynamic one
        if "model" in kwargs:
            kwargs["model"] = primary_model

        primary_llm = _ChatGroq(*args, **kwargs)

        kwargs["model"] = fallback_model_1
        fallback_llm_1 = _ChatGroq(*args, **kwargs)

        kwargs["model"] = fallback_model_2
        fallback_llm_2 = _ChatGroq(*args, **kwargs)

        return primary_llm.with_fallbacks([fallback_llm_1, fallback_llm_2])
