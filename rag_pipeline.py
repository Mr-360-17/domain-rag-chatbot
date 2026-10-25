"""Modules 5-6: retrieve the most relevant chunks and generate a grounded answer.

The language model is Google Gemini when GEMINI_API_KEY is set, otherwise Groq when
GROQ_API_KEY is set. Without any key the app still retrieves and shows the best passage.
"""
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv

from prompt import FALLBACK, SYSTEM_PROMPT, build_user_prompt
from vector_store import VectorStore

load_dotenv()

TOP_K = 4                 # brief: retrieve the top 3-5 chunks
MIN_SCORE = 0.30          # below this cosine similarity, nothing relevant was found
DEFAULT_MODELS = {"gemini": "gemini-3.1-flash-lite", "groq": "llama-3.3-70b-versatile"}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}
# Tried in order after the configured model when Gemini is overloaded (HTTP 429/5xx) or
# a model is retired for the account (HTTP 404).
GEMINI_FALLBACKS = ["gemini-flash-latest", "gemini-3-flash-preview", "gemini-3.1-flash-lite"]
RETRYABLE_STATUS = {404, 429, 500, 503, 504}


@dataclass
class Answer:
    text: str
    sources: list[dict] = field(default_factory=list)   # chunks used as context
    found: bool = True                                  # False when the fallback was returned
    seconds: float = 0.0
    mode: str = "llm"                                   # "llm", "retrieval_only" or "no_match"
    model: str = ""                                     # model that produced the answer


def _setting(name: str) -> str | None:
    """A value from the environment / .env, or from Streamlit secrets when deployed."""
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None


def get_provider() -> str | None:
    """'gemini' or 'groq', whichever has a key (Gemini first); None without a key."""
    for provider, key_name in KEY_NAMES.items():
        if _setting(key_name):
            return provider
    return None


def get_api_key() -> str | None:
    provider = get_provider()
    return _setting(KEY_NAMES[provider]) if provider else None


def get_model_name(provider: str | None = None) -> str:
    provider = provider or get_provider() or "gemini"
    return _setting(f"{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]


class RAGPipeline:
    def __init__(self, store: VectorStore, provider: str | None = None):
        self.store = store
        self.provider = provider or get_provider()
        self.api_key = _setting(KEY_NAMES[self.provider]) if self.provider else None
        self.model = get_model_name(self.provider)
        self._client = None

    @property
    def client(self):
        if self._client is None and self.api_key:
            if self.provider == "gemini":
                from google import genai
                from google.genai import types
                # 30 s timeout and two quick retries per model; generate() then moves to a fallback model.
                self._client = genai.Client(api_key=self.api_key, http_options=types.HttpOptions(
                    timeout=30_000,
                    retry_options=types.HttpRetryOptions(attempts=3, initial_delay=1.5, max_delay=6,
                                                         http_status_codes=[429, 500, 503, 504])))
            else:
                from groq import Groq
                self._client = Groq(api_key=self.api_key)
        return self._client

    def generate(self, user_prompt: str) -> tuple[str, str]:
        """Send the guardrail system prompt and the context + question to the LLM.

        Returns (answer text, model used).
        """
        if self.provider == "gemini":
            from google.genai import errors, types
            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT, temperature=0, max_output_tokens=800)
            models = list(dict.fromkeys([self.model, *GEMINI_FALLBACKS]))
            for i, model in enumerate(models):
                try:
                    response = self.client.models.generate_content(
                        model=model, contents=user_prompt, config=config)
                    return (response.text or "").strip(), model
                except errors.APIError as err:
                    if err.code not in RETRYABLE_STATUS or i == len(models) - 1:
                        raise
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=600,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content.strip(), self.model

    def retrieve(self, question: str, k: int = TOP_K) -> list[dict]:
        return self.store.search(question, k)

    def answer(self, question: str) -> Answer:
        start = time.perf_counter()
        hits = self.retrieve(question)
        relevant = [h for h in hits if h["score"] >= MIN_SCORE]

        if not relevant:
            # Nothing in the documents is close to the question: refuse without calling the LLM.
            return Answer(FALLBACK, [], False, time.perf_counter() - start, "no_match")

        if self.client is None:
            best = relevant[0]
            text = (f"*No LLM API key is configured, so this is the most relevant passage instead of "
                    f"a written answer.*\n\n> {best['text']}\n\n({best['source']}, page {best['page']})")
            return Answer(text, relevant, True, time.perf_counter() - start, "retrieval_only")

        text, model = self.generate(build_user_prompt(question, relevant))
        text = text or FALLBACK
        found = FALLBACK.lower().rstrip(".") not in text.lower()
        return Answer(text, relevant if found else [], found, time.perf_counter() - start, "llm", model)
