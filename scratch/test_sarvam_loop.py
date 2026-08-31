import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app.core.llm import ChatGroqWithFallback
from langchain_core.messages import HumanMessage, SystemMessage

llm = ChatGroqWithFallback(temperature=0.1)

print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] Starting Sarvam test...")
start_time = time.time()
try:
    # A generic prompt similar to what might be failing
    response = llm.invoke([
        SystemMessage(content="You are a JSON extractor. Output ONLY valid JSON, nothing else."),
        HumanMessage(content="Extract the name and age from this text: 'John Doe is 30 years old.'")
    ])
    elapsed = time.time() - start_time
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] Finished in {elapsed:.2f} seconds.")
    print("Response text:", repr(response.content[:500])) # print first 500 chars
    if hasattr(response, "response_metadata"):
        print("Metadata:", response.response_metadata)
except Exception as e:
    elapsed = time.time() - start_time
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] Failed in {elapsed:.2f} seconds.")
    print("Error:", repr(e))
