import requests
import json

url = "http://localhost:8000/api/v1/llm/chat/ollama/context_query"
# Use a specific query about interfaces
payload = {
    "prompt": "What interfaces does Dist_Cisco_1 have?",
    "model": "qwen2.5-coder:7b"
}
headers = {"Content-Type": "application/json"}

try:
    response = requests.post(url, json=payload, timeout=60)
    print(f"Status: {response.status_code}")
    result = response.json()
    print(f"Content: {result.get('content')}")
    print(f"Context Size: {result.get('context_size')}")
except Exception as e:
    print(f"Error: {e}")
