import React from 'react';
import ReactMarkdown from 'react-markdown';
import { Role } from '../typesLLM';
import type { Message } from '../typesLLM';
import { User, Bot, Copy, Check, Search, Database, Brain, Sparkles } from 'lucide-react';

interface MessageBubbleProps {
  message: Message;
}

// Phase icon mapping
const phaseIcons: Record<string, React.ReactNode> = {
  classifying: <Search size={14} className="text-blue-400" />,
  fetching:    <Database size={14} className="text-emerald-400" />,
  reasoning:   <Brain size={14} className="text-purple-400" />,
  composing:   <Sparkles size={14} className="text-amber-400" />,
};

function formatElapsed(ms: number): string {
  if (ms < 1000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 10000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 1000)}s`;
}

/** Animated thinking indicator with phase labels and elapsed time. */
const ThinkingIndicator: React.FC<{ message: Message }> = ({ message }) => {
  const phase = message.thinkingPhase;
  const [elapsed, setElapsed] = React.useState(0);

  React.useEffect(() => {
    if (!message.thinkingStartTime) return;
    const tick = setInterval(() => {
      setElapsed(Date.now() - (message.thinkingStartTime || Date.now()));
    }, 100);
    return () => clearInterval(tick);
  }, [message.thinkingStartTime]);

  const icon = phase ? phaseIcons[phase.phase] || <Brain size={14} className="text-purple-400" /> : null;
  const label = phase?.label || 'Processing…';

  return (
    <div className="flex items-center gap-2 text-sm text-gray-400 py-1.5">
      <div className="flex items-center gap-1.5 min-w-0">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <span className="text-xs tabular-nums text-gray-500 shrink-0">
        {formatElapsed(elapsed)}
      </span>
      <span className="loading loading-dots loading-xs ml-1"></span>
    </div>
  );
};

export const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === Role.User;
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Show thinking indicator while streaming with no content yet
  const showThinking = message.isStreaming && !message.content && message.thinkingPhase;

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

        {/* Thinking indicator with phase labels */}
        {showThinking ? (
          <ThinkingIndicator message={message} />
        ) : (
          <div className={`prose prose-sm max-w-none ${isUser ? 'prose-invert' : 'prose-invert'}`}>
             <ReactMarkdown
              components={{
                code({className, children, ...props}: any) {
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
        )}
        
        {/* Cursor for streaming effect (only when content is present) */}
        {message.isStreaming && message.content && (
          <span className="loading loading-dots loading-sm"></span>
        )}

        {!isUser && !message.isStreaming && typeof message.elapsedMs === 'number' && (
          <div className="mt-3 text-[11px] text-base-content/50">
            {message.stopped ? 'Stopped at' : 'Completed in'} {formatElapsed(message.elapsedMs)}
          </div>
        )}
      </div>
    </div>
  );
};