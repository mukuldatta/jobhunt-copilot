from groq import Groq
import google.generativeai as genai
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()

class LLMProvider:
    def __init__(self, provider = "groq"):
        self.provider = provider

        if provider == "groq":
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            self.model = "llama-3.3-70b-versatile"
        elif provider == "gemini":
            genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
            self.model = "gemini-1.5-flash"
            self.client = genai.GenerativeModel(self.model)
        elif provider == "anthropic":
            self.model = "claude-haiku-4-5-20251001"
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        
    def complete(self, prompt: str):
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
            print(f"LLM error ({self.provider}): {e}")
            if self.provider == "groq":
                print("Falling back to Gemini...")
                self.provider = "gemini"
                self.model = "gemini-1.5-flash"
                genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
                self.client = genai.GenerativeModel("gemini-1.5-flash")
                return self.complete(prompt)
            raise Exception(f"All providers failed, Please check you API keys and Rate Limits. {e}")