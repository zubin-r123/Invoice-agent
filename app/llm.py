import os

_client = None


def get_client():
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add it to your .env file (see .env.example) "
            "or export it before starting the app."
        )

    from groq import Groq

    _client = Groq(api_key=api_key)
    return _client
