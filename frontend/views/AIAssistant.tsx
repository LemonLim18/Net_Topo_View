import React, { useState, useRef, useEffect } from 'react';
import * as GoogleGenAI from '@google/generative-ai';
// Use the application's topology data as the realtime topology source.
// The constants file exports `topologyData`; alias it to the name the
// assistant expects so the prompt generation below works as-is.
// Look aftert this when integrating with a backend proxy.
import { mockTopologyData as REALTIME_TOPOLOGY_DATA } from '../constants';
import { Send, Bot, User, Copy, Check } from 'lucide-react';

interface Message {
  sender: 'user' | 'ai';
  text: string;
  isPlaybook?: boolean;
}

const AIAssistant: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [copiedStates, setCopiedStates] = useState<boolean[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  // The `@google/generative-ai` package exposes functions on the
  // imported module rather than a constructable class in the browser
  // bundle. Also, Vite exposes env vars on `import.meta.env` (not
  // `process.env`). Use a safe `any` cast here and read the API key
  // from `VITE_API_KEY` if you plan to call the API directly from
  // the browser (not recommended for production—use a server proxy).
  //
  // If you integrate a backend proxy, replace this with a small
  // wrapper that forwards calls to your server instead of embedding
  // the key in the client.
  const ai = (GoogleGenAI as any);
  const VITE_API_KEY = import.meta.env.VITE_API_KEY as string | undefined;
  if (!VITE_API_KEY) {
    // eslint-disable-next-line no-console
    console.warn('VITE_API_KEY is not set. Calling the AI API from the browser may fail.');
  }

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(scrollToBottom, [messages]);

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    const newCopiedStates = [...copiedStates];
    newCopiedStates[index] = true;
    setCopiedStates(newCopiedStates);
    setTimeout(() => {
        const resetCopiedStates = [...newCopiedStates];
        resetCopiedStates[index] = false;
        setCopiedStates(resetCopiedStates);
    }, 2000);
  };
  
  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage: Message = { sender: 'user', text: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsLoading(true);

    try {
      if (typeof ai?.models?.generateContent !== 'function') {
        const errorMessage: Message = { sender: 'ai', text: 'AI client is not configured in the browser. Configure `VITE_API_KEY` or use a backend proxy.' };
        setMessages(prev => [...prev, errorMessage]);
        setIsLoading(false);
        return;
      }
      const systemInstruction = `You are a senior network automation engineer. Given a network topology and a user's request, you must generate a complete and runnable Ansible playbook to accomplish the task. The topology is provided in JSON format. The playbook should be well-structured, commented, and enclosed in a single markdown code block.`;
      
      const fullPrompt = `Current Network Topology (JSON):
${JSON.stringify(REALTIME_TOPOLOGY_DATA.nodes, null, 2)}

User Request: "${input}"`;

      const response = await ai.models.generateContent({
        model: 'gemini-2.5-flash',
        contents: fullPrompt,
        config: { systemInstruction },
      });
      
      const playbookText = response.text;
      const aiMessage: Message = { sender: 'ai', text: playbookText, isPlaybook: true };
      setMessages(prev => [...prev, aiMessage]);

    } catch (error) {
      console.error("Error calling Gemini API:", error);
      const errorMessage: Message = { sender: 'ai', text: 'Sorry, I encountered an error. Please try again.' };
      setMessages(prev => [...prev, errorMessage]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full p-4 bg-base-300">
      <div className="grow overflow-y-auto pr-4 space-y-6">
        {messages.map((msg, index) => (
          <div key={index} className={`chat ${msg.sender === 'user' ? 'chat-end' : 'chat-start'}`}>
            <div className="chat-image avatar">
              <div className="w-10 rounded-full bg-base-100 flex items-center justify-center">
                {msg.sender === 'user' ? <User className="h-6 w-6" /> : <Bot className="h-6 w-6" />}
              </div>
            </div>
            <div className={`chat-bubble ${msg.sender === 'user' ? 'chat-bubble-primary' : ''}`}>
              {msg.isPlaybook ? (
                <div className="mockup-code text-left text-sm relative">
                   <div className="absolute top-2 right-2">
                    <button className="btn btn-ghost btn-sm" onClick={() => handleCopy(msg.text, index)}>
                      {copiedStates[index] ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
                    </button>
                  </div>
                  <pre><code>{msg.text}</code></pre>
                </div>
              ) : (
                <p>{msg.text}</p>
              )}
            </div>
          </div>
        ))}
        {isLoading && (
            <div className="chat chat-start">
                 <div className="chat-image avatar">
                    <div className="w-10 rounded-full bg-base-100 flex items-center justify-center"><Bot className="h-6 w-6"/></div>
                 </div>
                 <div className="chat-bubble"><span className="loading loading-dots loading-md"></span></div>
            </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="shrink-0 mt-4">
        <div className="form-control">
          <div className="input-group">
            <input
              type="text"
              placeholder="e.g., Generate a playbook to configure OSPF on all routers..."
              className="input input-bordered w-full"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              disabled={isLoading}
            />
            <button className="btn btn-square btn-primary" onClick={handleSend} disabled={isLoading}>
              <Send className="h-5 w-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AIAssistant;
