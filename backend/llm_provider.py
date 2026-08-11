from groq import Groq
import google.generativeai as genai
import anthropic
import os
import re
import time
from dotenv import load_dotenv

load_dotenv()

# gemini-1.5-flash was retired (404 on generateContent). Overridable via env.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")


class RateLimited(Exception):
    """Every provider is currently rate limited — the caller should back off."""


def _is_rate_limit(err: Exception) -> bool:
    s = str(err).lower()
    return ("429" in s or "rate limit" in s or "rate_limit" in s
            or "quota" in s or "resource_exhausted" in s or "too many requests" in s)


def _retry_after(err: Exception, default: float = 30.0) -> float:
    """Honour the provider's own retry hint when it gives one."""
    s = str(err)
    m = re.search(r"retry_delay\s*{\s*seconds:\s*(\d+)", s) or re.search(r"try again in (\d+(?:\.\d+)?)s", s)
    if m:
        return min(float(m.group(1)) + 2, 120.0)
    m = re.search(r"try again in (\d+)m(\d+(?:\.\d+)?)s", s)
    if m:
        return min(float(m.group(1)) * 60 + float(m.group(2)) + 2, 120.0)
    return default


class LLMProvider:
    def __init__(self, provider = "groq"):
        self.provider = provider

        if provider == "groq":
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            self.model = "llama-3.3-70b-versatile"
        elif provider == "gemini":
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            self.model = GEMINI_MODEL
            self.client = genai.GenerativeModel(self.model)
        elif provider == "anthropic":
            self.model = "claude-haiku-4-5-20251001"
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        
    def complete(self, prompt: str, _tried: tuple = ()):
        try:
            if self.provider == "groq":
                response = self.client.chat.completions.create(
                    model = self.model,
                    messages = [{"role": "user", "content": prompt}]
                )
                return response.choices[0].message.content.strip()
            elif self.provider == "gemini":
                response = self.client.generate_content(prompt)
                return response.text.strip()
            elif self.provider == "anthropic":
                response = self.client.messages.create(
                    model = self.model,
                    max_tokens = 2048,
                    messages = [{"role": "user", "content": prompt}]
                )
                return response.content[0].text.strip()
            
        except Exception as e:
            limited = _is_rate_limit(e)
            print(f"LLM error ({self.provider}): {str(e)[:160]}")
            tried = _tried + (self.provider,)

            # Try the next configured provider before giving up.
            for nxt, key in (("gemini", "GEMINI_API_KEY"), ("groq", "GROQ_API_KEY"),
                             ("anthropic", "ANTHROPIC_API_KEY")):
                if nxt in tried or not os.getenv(key):
                    continue
                print(f"Falling back to {nxt}...")
                self._switch(nxt)
                return self.complete(prompt, tried)

            if limited:
                # Distinct from a hard failure: the caller can pause and retry
                # later instead of treating the job as unscoreable.
                raise RateLimited(f"All providers rate limited. retry_after={_retry_after(e):.0f}s") from e
            raise Exception(f"All providers failed. Check API keys and rate limits. {e}")

    def _switch(self, provider: str):
        self.provider = provider
        if provider == "groq":
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            self.model = "llama-3.3-70b-versatile"
        elif provider == "gemini":
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            self.model = GEMINI_MODEL
            self.client = genai.GenerativeModel(GEMINI_MODEL)
        elif provider == "anthropic":
            self.model = "claude-haiku-4-5-20251001"
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))