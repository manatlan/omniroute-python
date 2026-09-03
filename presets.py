"""curated provider presets. free flags = no payment needed to start (tiers change; Test button verifies)."""
# ponytail: static list, fetched catalog when staleness proves painful
PRESETS = [
    {"provider": "Ollama (this laptop)", "kind": "ollama", "base_url": "http://localhost:11434/v1",
     "free": True, "note": "Local models. No key. Pull first: ollama pull llama3.1",
     "models": [{"id": "llama3.1", "free": True}, {"id": "qwen2.5", "free": True}, {"id": "mistral", "free": True}]},
    {"provider": "Pollinations", "kind": "openai", "base_url": "https://text.pollinations.ai/openai",
     "free": True, "note": "Free, no key. Use - as key. Shared, rate-limited.",
     "models": [{"id": "openai", "free": True}]},
    {"provider": "Google Gemini", "kind": "gemini", "base_url": "https://generativelanguage.googleapis.com",
     "free": True, "note": "Free tier with limits. Key from Google AI Studio.",
     "models": [{"id": "gemini-2.0-flash", "free": True}, {"id": "gemini-2.5-flash", "free": True}]},
    {"provider": "Groq", "kind": "openai", "base_url": "https://api.groq.com/openai/v1",
     "free": True, "note": "Free tier with daily limits. Very fast.",
     "models": [{"id": "llama-3.3-70b-versatile", "free": True}, {"id": "llama-3.1-8b-instant", "free": True}]},
    {"provider": "Cerebras", "kind": "openai", "base_url": "https://api.cerebras.ai/v1",
     "free": True, "note": "Free tier with limits. Very fast.",
     "models": [{"id": "llama3.1-70b", "free": True}, {"id": "llama-3.3-70b", "free": True}]},
    {"provider": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
     "free": False, "note": "Paid service, but :free models cost nothing.",
     "models": [{"id": "meta-llama/llama-3.3-70b-instruct:free", "free": True},
                {"id": "google/gemma-3-27b-it:free", "free": True}]},
]
