import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from sentinel_ai.agents.base import get_llm_client

async def main():
    print("Testing Gemini LLM Provider...")
    llm = get_llm_client()
    print("Agent LLM Available:", llm.is_available)
    print("Testing real-time completion...")
    
    prompt = "Hi, reply 'hello world' if you receive this message clearly."
    try:
        response = await llm.complete(prompt=prompt)
        print("Success! Gemini response:", response.strip())
    except Exception as e:
        print("Error during LLM execution:", e)

if __name__ == "__main__":
    asyncio.run(main())
