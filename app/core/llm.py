import os

class ChatGroqWithFallback:
    def __new__(cls, *args, **kwargs):
        sarvam_api_key = os.getenv("SARVAM_API_KEY")
        if not sarvam_api_key:
            from langchain_groq import ChatGroq
            return ChatGroq(*args, **kwargs)

        # Imported lazily: langchain_openai is only needed for the optional
        # Sarvam path and is NOT a declared dependency in requirements.txt.
        # A module-level import crashes Groq-only deployments on startup.
        from langchain_openai import ChatOpenAI

        kwargs.pop("api_key", None)
        
        primary_model = "sarvam-105b"
            
        return ChatOpenAI(
            base_url="https://api.sarvam.ai/v1",
            api_key=sarvam_api_key,
            model=primary_model,
            temperature=kwargs.get("temperature", 0.1),
            max_tokens=kwargs.get("max_tokens", None),
            timeout=None,
            max_retries=0
        )
