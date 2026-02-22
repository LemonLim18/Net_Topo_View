import os
from google import genai

# The client gets the API key from the environment variable `GEMINI_API_KEY`.
# client = genai.Client(api_key="AIzaSyA34gxoMmrQ2YBGGc6dQo7T-lLb2KIBA3U")
client = genai.Client(api_key="AIzaSyA34gxoMmrQ2YBGGc6dQo7T-lLb2KIBA3U")

def generate_text_with_gemini(prompt_text):
  """
  Generates text using the Gemini model with a simple prompt.
  """
  try:
    # Use gemini-2.5-flash which is available in the free tier
    model = 'gemini-2.5-flash'
    # model = 'gemini-2.5-flash-lite'
    # model = 'gemini-2.0-flash'

    response = client.models.generate_content(
      model=model,
      contents=prompt_text,
    )

    print(response.text)

  except Exception as e:
    print(f"An error occurred: {e}")
    print("Please check your API key and ensure you are within the free tier limits.")

# Example usage
prompt = " Good morning, how are you today?"
generate_text_with_gemini(prompt)
