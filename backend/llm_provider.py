from groq import Groq
import google.generativeai as genai
import anthropic
import os
import re
import time
from dotenv import load_dotenv

load_dotenv()

# Free tiers meter tokens PER MODEL, so when the big model hits its daily cap
# the smaller ones still have their own untouched budget. Walking this chain
# multiplies the usable free quota instead of stopping at the first wall.
# Order: best quality first, cheapest/highest-allowance last.
GROQ_MODELS = [m.strip() for m in os.getenv(
    "GROQ_MODELS",
    "llama-3.3-70b-versatile,openai/gpt-oss-120b,llama-3.1-8b-instant,openai/gpt-oss-20b"
).split(",") if m.strip()]

# gemini-1.5-flash was retired (404 on generateContent).
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-flash-latest,gemini-flash-lite-latest"
).split(",") if m.strip()]

GEMINI_MODEL = GEMINI_MODELS[0]          # back-compat for existing imports

ANTHROPIC_MODELS = ["claude-haiku-4-5-20251001"]


def _models_for(provider: str) -> list:
    return {"groq": GROQ_MODELS, "gemini": GEMINI_MODELS,
            "anthropic": ANTHROPIC_MODELS}.get(provider, [])


class RateLimited(Exception):
    """Every provider is currently rate limited — the caller should back off."""


# When the whole chain comes back limited, remember it. complete() has no memory
# between calls otherwise, so an exhausted quota costs a full walk of every
# model of every provider on EVERY call — roughly a dozen doomed round trips per
# document, on providers that already said no. The cooldown turns that into one
# local check, and lets callers ask "is it worth spending a call?" before they
# build a prompt.
_COOLDOWN_UNTIL = 0.0


def cooldown_remaining() -> float:
    """Seconds until the LLM chain is worth trying again. 0.0 when it's live."""
    return max(0.0, _COOLDOWN_UNTIL - time.time())


def is_rate_limited() -> bool:
    """True while every provider is known-exhausted. Cheap, no network."""
    return cooldown_remaining() > 0


def _open_cooldown(seconds: float):
    global _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = time.time() + seconds
    print(f"LLM: all providers exhausted — pausing LLM work for {seconds:.0f}s")


def _clear_cooldown():
    global _COOLDOWN_UNTIL
    if _COOLDOWN_UNTIL:
        print("LLM: quota recovered")
    _COOLDOWN_UNTIL = 0.0


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
    def __init__(self, provider="groq"):
        self.client = None
        self.model = None
        self._switch(provider)

    def _call(self, prompt: str, model: str) -> str:
        if self.provider == "groq":
            r = self.client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}])
            return r.choices[0].message.content.strip()
        if self.provider == "gemini":
            return genai.GenerativeModel(model).generate_content(prompt).text.strip()
        if self.provider == "anthropic":
            r = self.client.messages.create(
                model=model, max_tokens=2048,
                messages=[{"role": "user", "content": prompt}])
            return r.content[0].text.strip()
        raise Exception(f"Unknown provider '{self.provider}'")

    def complete(self, prompt: str, _tried: tuple = ()):
        """
        Try each model of the current provider, then each remaining provider.
        Free tiers meter per model, so exhausting one model doesn't mean the
        provider is out — only that this bucket is.
        """
        if not _tried and is_rate_limited():
            # Nothing has refilled yet; skip the walk rather than re-prove it.
            raise RateLimited(
                f"All providers rate limited. retry_after={cooldown_remaining():.0f}s"
            )

        last_err = None
        limited = False

        # Start from the currently selected model, then the rest of the chain.
        models = _models_for(self.provider)
        ordered = ([self.model] + [m for m in models if m != self.model]) if self.model else models

        for model in ordered:
            try:
                out = self._call(prompt, model)
                if model != self.model:
                    print(f"LLM: using {self.provider}/{model}")
                    self.model = model      # stick with what works
                _clear_cooldown()           # something answered — quota is back
                return out
            except Exception as e:
                last_err = e
                if _is_rate_limit(e):
                    limited = True
                    print(f"LLM {self.provider}/{model}: rate limited, trying next model")
                    continue
                print(f"LLM {self.provider}/{model}: {str(e)[:110]}")
                continue

        tried = _tried + (self.provider,)
        for nxt, key in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY"),
                         ("anthropic", "ANTHROPIC_API_KEY")):
            if nxt in tried or not os.getenv(key):
                continue
            print(f"Falling back to provider {nxt}...")
            self._switch(nxt)
            return self.complete(prompt, tried)

        if limited:
            # Distinct from a hard failure: the caller can pause and retry
            # later instead of treating the job as unscoreable.
            wait = _retry_after(last_err)
            _open_cooldown(wait)
            raise RateLimited(
                f"All providers/models rate limited. retry_after={wait:.0f}s"
            ) from last_err
        raise Exception(f"All providers failed. Check API keys and rate limits. {last_err}")

    def _switch(self, provider: str):
        self.provider = provider
        self.model = (_models_for(provider) or [None])[0]
        if provider == "groq":
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        elif provider == "gemini":
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            self.client = None            # model chosen per call
        elif provider == "anthropic":
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))