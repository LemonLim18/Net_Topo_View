import requests
import json

url = "https://api.deepseek.com/chat/completions"

payload = json.dumps({
   "messages": [
      {
         "content": "You are a helpful assistant",
         "role": "system"
      },
      {
         "content": "Hi",
         "role": "user"
      }
   ],
   "model": "deepseek-chat",
   "frequency_penalty": 0,
   "max_tokens": 2048,
   "presence_penalty": 0,
   "response_format": {
      "type": "text"
   },
   "stop": None,
   "stream": False,
   "stream_options": None,
   "temperature": 1,
   "top_p": 1,
   "tools": None,
   "tool_choice": "none",
   "logprobs": False,
   "top_logprobs": None
})
headers = {
   'Authorization': 'Bearer sk-2b52373080c444c196fe90b0fd614525',
   'Content-Type': 'application/json'
}

response = requests.request("POST", url, headers=headers, data=payload)

print(response.text)
