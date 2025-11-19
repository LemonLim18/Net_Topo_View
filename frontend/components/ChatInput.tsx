import React, { useState, useRef, useEffect } from 'react';
import { Send, Square } from 'lucide-react';

interface ChatInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  isGenerating?: boolean;
  onStop?: () => void;
}

export const ChatInput: React.FC<ChatInputProps> = ({ onSend, disabled, isGenerating, onStop }) => {
  const [text, setText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, [text]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (text.trim() && !disabled) {
      onSend(text.trim());
      setText('');
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="w-full max-w-4xl mx-auto relative">
      <div className="relative flex items-end gap-2 bg-base-200 p-2 rounded-xl border border-base-300 focus-within:border-primary focus-within:ring-1 focus-within:ring-primary transition-all">
        <textarea
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled || isGenerating}
          placeholder={isGenerating ? "Gemini is thinking..." : "Ask anything..."}
          className="w-full resize-none border-none focus:outline-none focus:ring-0 bg-transparent p-3 text-base-content placeholder:base-content/40  max-h-[150px] text-base leading-relaxed scrollbar-hide"
          rows={1}
        />
        
        <div className="pb-2 pr-2">
          {/* {isGenerating ? ( */}
          {false ? (
             <button
             onClick={onStop}
             className="btn btn-circle btn-sm btn-error w-9 h-9 rounded-full"
             title="Stop generation"
           >
             <Square size={14} fill="currentColor" />
           </button>
          ) : (
            <button
              onClick={() => handleSubmit()}
              disabled={disabled}
              className="btn btn-circle btn-sm btn-primary w-9 h-9 rounded-full"
            >
              <Send size={14} />
            </button>
          )}
        </div>
      </div>
      <div className="text-center text-xs text-base-content/40 mt-2">
        Gemini can make mistakes. Check important info.
      </div>
    </div>
  );
};