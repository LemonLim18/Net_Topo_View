import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
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
        <div className={`w-10 rounded-full border shadow-lg ${isUser ? 'border-gray-600 bg-gray-700' : 'border-primary/40 bg-black/60 backdrop-blur-md'}`}>
          <div className="flex items-center justify-center h-full w-full text-gray-300">
            {isUser ? <User size={20} /> : <Bot size={20} className="text-primary" />}
          </div>
        </div>
      </div>
      
      <div className="chat-header opacity-50 mb-1.5 text-xs font-medium ml-1 flex items-center gap-2">
        {isUser ? 'You' : (
          <>
            <span className="text-primary/80 font-bold uppercase tracking-wider">NetAuto AI</span>
            <span className="badge badge-outline badge-xs opacity-50">Local Intelligence</span>
          </>
        )}
      </div>
      
      <div 
        className={`chat-bubble relative overflow-hidden transition-all duration-300 max-w-[85%] sm:max-w-[75%] shadow-xl ${
          isUser 
            ? 'bg-primary text-primary-content font-medium rounded-2xl p-4' 
            : 'bg-neutral-800/90 text-neutral-content rounded-2xl border border-white/5 p-4 backdrop-blur-sm shadow-black/40'
        } ${message.isError ? 'chat-bubble-error scale-95' : ''}`}
      >
        {/* Copy button for AI messages */}
        {!isUser && !message.isStreaming && (
          <button 
            onClick={handleCopy}
            className="absolute top-2 right-2 p-1.5 text-gray-400 hover:text-white rounded-md bg-black/30 opacity-0 group-hover:opacity-100 transition-opacity z-10"
            title="Copy to clipboard"
          >
            {copied ? <Check size={14} className="text-success" /> : <Copy size={14} />}
          </button>
        )}

        {/* Thinking indicator with phase labels */}
        {showThinking ? (
          <ThinkingIndicator message={message} />
        ) : (
          <div className={`prose prose-sm max-w-none prose-invert ${isUser ? '' : 'prose-emerald'}`}>
             <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                strong({children}) {
                  return <strong className="font-extrabold text-white decoration-primary/50 underline-offset-2">{children}</strong>
                },
                code({className, children, ...props}: any) {
                  const { ref, ...rest } = props as any;
                  const match = /language-(\w+)/.exec(className || '')
                  const isInline = !match && !String(children).includes('\n');
                  return isInline ? (
                    <code className="bg-black/50 text-emerald-400 rounded-lg px-2 py-0.5 text-sm font-mono border border-white/5" {...rest}>
                      {children}
                    </code>
                  ) : (
                    <div className="mockup-code bg-black/40 border border-white/5 rounded-2xl text-sm my-5 shadow-inner">
                       <pre data-prefix=">" className="text-success bg-transparent">
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
          <div className="flex justify-start mt-2">
            <span className="loading loading-dots loading-sm opacity-50"></span>
          </div>
        )}

        {!isUser && !message.isStreaming && typeof message.elapsedMs === 'number' && (
          <div className="mt-4 pt-3 border-t border-white/5 text-[10px] uppercase tracking-widest text-base-content/30 font-bold flex justify-between items-center italic">
             <span>{message.stopped ? 'Interrupted' : 'Analysis Complete'}</span>
             <span>{formatElapsed(message.elapsedMs)}</span>
          </div>
        )}
      </div>
    </div>
  );
};