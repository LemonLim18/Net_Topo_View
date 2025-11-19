import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Role } from '../typesLLM';
import type { Message } from '../typesLLM';
import { User, Bot, Copy, Check } from 'lucide-react';

interface MessageBubbleProps {
  message: Message;
}

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === Role.User;
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className={`chat ${isUser ? 'chat-end' : 'chat-start'} mb-4 group`}>
      <div className="chat-image avatar hidden sm:block">
        <div className={`w-10 rounded-full border ${isUser ? 'border-gray-600 bg-gray-700' : 'border-primary/30 bg-black'}`}>
          <div className="flex items-center justify-center h-full w-full text-gray-300">
            {isUser ? <User size={20} /> : <Bot size={20} />}
          </div>
        </div>
      </div>
      
      <div className="chat-header opacity-50 mb-1 text-xs ml-1">
        {isUser ? 'You' : 'Gemini'}
      </div>
      
      <div 
        className={`chat-bubble relative max-w-[85%] sm:max-w-[75%] ${
          isUser 
            ? 'chat-bubble-primary text-primary-content' 
            : 'chat-bubble-neutral text-neutral-content'
        } ${message.isError ? 'chat-bubble-error' : ''}`}
      >
        {/* Copy button for AI messages */}
        {!isUser && !message.isStreaming && (
          <button 
            onClick={handleCopy}
            className="absolute -bottom-8 left-0 p-1.5 text-gray-400 rounded-md  transition-opacity"
            title="Copy to clipboard"
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        )}

        <div className={`prose prose-sm max-w-none ${isUser ? 'prose-invert' : 'prose-invert'}`}>
           {/* Using ReactMarkdown for rendering. 
               Note: In a real production build, we'd configure code blocks with syntax highlighting. 
               For this simplified version, we rely on basic markdown parsing. */}
           <ReactMarkdown
            components={{
              code({className, children, ...props}: any) {
                // remove the incoming ref to avoid cross-version React.Ref incompatibility
                const { ref, ...rest } = props as any;
                const match = /language-(\w+)/.exec(className || '')
                const isInline = !match && !String(children).includes('\n');
                return isInline ? (
                  <code className="bg-black/30 text-base-content rounded-sm px-1 py-0.5 text-sm font-mono" {...rest}>
                    {children}
                  </code>
                ) : (
                  <div className="mockup-code bg-neutral-800 rounded-3xl text-sm my-4 min-w-0">
                     <pre className="px-4 py-2 overflow-x-auto">
                      <code className={className} {...rest}>
                        {children}
                      </code>
                    </pre>
                  </div>
                )
              }
            }}
           >
             {message.content}
           </ReactMarkdown>
        </div>
        
        {/* Cursor for streaming effect */}
        {message.isStreaming && (
        //   <span className="inline-block w-2 h-4 ml-1 bg-current animate-pulse align-middle"></span>
          <span className="loading loading-dots loading-sm"></span>
        )}
      </div>
    </div>
  );
};