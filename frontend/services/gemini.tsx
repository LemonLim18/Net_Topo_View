import { GoogleGenAI, Chat, GenerateContentResponse } from "@google/genai";

// Initialize the client
// The API key is injected by the environment
const ai = new GoogleGenAI({ apiKey: (import.meta as any).env?.VITE_API_KEY });

export const createChatSession = (systemInstruction?: string): Chat => {
  return ai.chats.create({
    model: 'gemini-2.5-flash',
    config: {
      systemInstruction: systemInstruction || "You are a helpful, knowledgeable, and friendly AI assistant. formatted in Markdown.",
      temperature: 0.7,
    },
  });
};

export const sendMessageStream = async (
  chat: Chat,
  message: string
): Promise<AsyncGenerator<string, void, unknown>> => {
  
  // Convert the async iterable response to a simple generator of text chunks
  async function* streamGenerator() {
    try {
      const responseStream = await chat.sendMessageStream({ message });
      
      for await (const chunk of responseStream) {
        const c = chunk as GenerateContentResponse;
        if (c.text) {
          yield c.text;
        }
      }
    } catch (error) {
      console.error("Error in stream:", error);
      throw error;
    }
  }

  return streamGenerator();
};

export const generateTitle = async (firstMessage: string): Promise<string> => {
  try {
    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: `Generate a very short, 3-5 word title for a chat that starts with this message: "${firstMessage}". Return only the title text.`,
    });
    return response.text?.trim() || "New Chat";
  } catch (e) {
    return "New Chat";
  }
};