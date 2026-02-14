# API_KEY='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'
# CURRENT: test-key='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'
from openai import OpenAI

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key="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",
)

completion = client.chat.completions.create(
  extra_headers={
    # "HTTP-Referer": "<YOUR_SITE_URL>", # Optional. Site URL for rankings on openrouter.ai.
    # "X-Title": "<YOUR_SITE_NAME>", # Optional. Site title for rankings on openrouter.ai.
  },
  extra_body={},
  model="deepseek/deepseek-r1-distill-llama-70b:free", #ok
  # model="deepseek/deepseek-r1-0528-qwen3-8b:free",
  # model= "deepseek/deepseek-r1:free",
  # model="deepseek/deepseek-chat-v3.1:free",
  messages=[
              {
                "role": "user",
                "content": "How are you today?"
              }
            ]
)
print(completion.choices[0].message.content)