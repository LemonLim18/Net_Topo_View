import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { 
  X, 
  Search, 
  Database, 
  GripHorizontal, 
  RefreshCw, 
  ChevronRight,
  Send,
  Loader2,
  Terminal,
  Cpu,
  ArrowLeft,
  Info,
  Layers,
  Bot
} from 'lucide-react';

interface DeviceInterface {
  name: string;
  ip_address?: string;
  status?: string;
  mac_address?: string;
}

interface DeviceLink {
  peer_hostname: string;
  local_interface: string;
  peer_interface: string;
  status?: string;
}

interface Device {
  hostname: string;
  management_ip?: string;
  vendor?: string;
  model?: string;
  role?: string;
  status?: string;
  source?: string;
  interfaces?: DeviceInterface[];
  links?: DeviceLink[];
}

interface Message {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
}

const FloatingInventoryOverlay: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [activeTab, setActiveTab] = useState<'inventory' | 'ollama' | 'inventory_ai'>('inventory');
  const [position, setPosition] = useState({ x: window.innerWidth - 450, y: 100 });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartPos = useRef({ x: 0, y: 0 });
  
  // Inventory State
  const [devices, setDevices] = useState<Device[]>([]);
  const [expandedDevice, setExpandedDevice] = useState<Device | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [isLoadingInventory, setIsLoadingInventory] = useState(false);
  const [isEnriching, setIsEnriching] = useState(false);
  
  // Ollama State
  const [ollamaPrompt, setOllamaPrompt] = useState('');
  const [ollamaMessages, setOllamaMessages] = useState<Message[]>([]);
  const [isOllamaLoading, setIsOllamaLoading] = useState(false);
  
  // Inventory AI State
  const [inventoryAiPrompt, setInventoryAiPrompt] = useState('');
  const [inventoryAiMessages, setInventoryAiMessages] = useState<Message[]>([]);
  const [isInventoryAiLoading, setIsInventoryAiLoading] = useState(false);

  // Common LLM Params
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [modelFetchError, setModelFetchError] = useState<string | null>(null);
  
  const scrollRef = useRef<HTMLDivElement>(null);

  // --- Draggable Logic ---
  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    dragStartPos.current = {
      x: e.clientX - position.x,
      y: e.clientY - position.y
    };
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      setPosition({
        x: e.clientX - dragStartPos.current.x,
        y: e.clientY - dragStartPos.current.y
      });
    };
    const handleMouseUp = () => setIsDragging(false);

    if (isDragging) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isDragging]);

  // --- Inventory Logic ---
  const fetchInventory = async () => {
    setIsLoadingInventory(true);
    setExpandedDevice(null);
    try {
      const resp = await fetch('/api/v1/llm/chat/inventory/snapshot');
      const data = await resp.json();
      if (data.devices) {
        setDevices(data.devices);
      }
    } catch (err) {
      console.error('Failed to fetch inventory:', err);
    } finally {
      setIsLoadingInventory(false);
    }
  };

  const fetchDeviceDetail = async (hostname: string) => {
    setIsEnriching(true);
    try {
      const resp = await fetch(`/api/v1/llm/chat/inventory/device/${encodeURIComponent(hostname)}`);
      if (resp.ok) {
        const data = await resp.json();
        setExpandedDevice(data);
      } else {
        console.error('Failed to fetch device enrichment');
      }
    } catch (err) {
      console.error('Error fetching device detail:', err);
    } finally {
      setIsEnriching(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'inventory' && devices.length === 0) {
      fetchInventory();
    }
    if ((activeTab === 'ollama' || activeTab === 'inventory_ai') && availableModels.length === 0) {
      fetchModels();
    }
  }, [activeTab]);

  const fetchModels = async () => {
    setModelFetchError(null);
    try {
      const resp = await fetch('/api/v1/llm/chat/ollama/models');
      if (resp.ok) {
        const data = await resp.json();
        if (data.models && data.models.length > 0) {
          setAvailableModels(data.models);
          setSelectedModel(data.models[0]);
        } else {
          setModelFetchError('No models found on Ollama server.');
        }
      } else {
        const err = await resp.json();
        setModelFetchError(err.detail || 'Ollama server unreachable.');
      }
    } catch (err) {
      console.error('Failed to fetch Ollama models:', err);
      setModelFetchError('Connection failed.');
    }
  };

  const filteredDevices = devices.filter(d => 
    d.hostname.toLowerCase().includes(searchQuery.toLowerCase()) ||
    d.management_ip?.includes(searchQuery) ||
    d.role?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  // --- Chat Handling ---
  const handleChatSend = async (mode: 'playground' | 'inventory') => {
    const prompt = mode === 'playground' ? ollamaPrompt : inventoryAiPrompt;
    if (!prompt.trim()) return;
    
    const userMsg: Message = { 
      role: 'user', 
      content: prompt, 
      timestamp: new Date().toLocaleTimeString() 
    };
    
    if (mode === 'playground') {
      setOllamaMessages(prev => [...prev, userMsg]);
      setOllamaPrompt('');
      setIsOllamaLoading(true);
    } else {
      setInventoryAiMessages(prev => [...prev, userMsg]);
      setInventoryAiPrompt('');
      setIsInventoryAiLoading(true);
    }

    try {
      const endpoint = mode === 'playground' 
        ? '/api/v1/llm/chat/ollama/playground' 
        : '/api/v1/llm/chat/ollama/context_query';

      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          prompt: userMsg.content,
          model: selectedModel || undefined
        })
      });
      const data = await resp.json();
      
      const assistantMsg: Message = {
        role: 'assistant',
        content: data.content || 'No response from model.',
        timestamp: new Date().toLocaleTimeString()
      };

      if (mode === 'playground') {
        setOllamaMessages(prev => [...prev, assistantMsg]);
      } else {
        setInventoryAiMessages(prev => [...prev, assistantMsg]);
      }
    } catch (err) {
      const errMsg: Message = {
        role: 'system',
        content: `Error: ${err}`,
        timestamp: new Date().toLocaleTimeString()
      };
      if (mode === 'playground') {
        setOllamaMessages(prev => [...prev, errMsg]);
      } else {
        setInventoryAiMessages(prev => [...prev, errMsg]);
      }
    } finally {
      if (mode === 'playground') setIsOllamaLoading(false);
      else setIsInventoryAiLoading(false);
    }
  };

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [ollamaMessages, inventoryAiMessages]);

  return (
    <div 
      className="fixed z-9999 shadow-2xl border border-base-content/10 bg-base-100 rounded-xl overflow-hidden flex flex-col"
      style={{ 
        width: '420px', 
        height: '550px', 
        left: `${position.x}px`, 
        top: `${position.y}px`,
        transition: isDragging ? 'none' : 'all 0.1s ease'
      }}
    >
      {/* Header / Drag Handle */}
      <div 
        className="bg-primary text-primary-content p-3 flex items-center justify-between cursor-grab active:cursor-grabbing select-none"
        onMouseDown={handleMouseDown}
      >
        <div className="flex items-center gap-2 font-bold text-sm">
          <Terminal className="w-4 h-4" />
          <span>Network Ops Center Overlay</span>
        </div>
        <div className="flex items-center gap-1">
          <GripHorizontal className="w-4 h-4 opacity-50" />
          <button onClick={onClose} className="btn btn-ghost btn-xs btn-circle hover:bg-white/20">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tabs Selector */}
      <div className="flex border-b border-base-content/10 bg-base-200/50">
        <button 
          onClick={() => setActiveTab('inventory')}
          className={`flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'inventory' ? 'bg-base-100 text-primary border-b-2 border-primary' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Database className="w-3.5 h-3.5" />
          INVENTORY
        </button>
        <button 
          onClick={() => setActiveTab('inventory_ai')}
          className={`flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'inventory_ai' ? 'bg-base-100 text-secondary border-b-2 border-secondary' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Bot className="w-3.5 h-3.5" />
          INVENTORY AI
        </button>
        <button 
          onClick={() => setActiveTab('ollama')}
          className={`flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'ollama' ? 'bg-base-100 text-info border-b-2 border-info' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Cpu className="w-3.5 h-3.5" />
          OLLAMA (FREE)
        </button>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-hidden relative">
        {activeTab === 'inventory' ? (
          <div className="h-full flex flex-col p-4 bg-base-100">
            {expandedDevice ? (
              <div className="flex-1 flex flex-col overflow-hidden">
                <button 
                  onClick={() => setExpandedDevice(null)}
                  className="btn btn-ghost btn-xs w-fit mb-3 gap-1 pl-0 hover:bg-transparent text-primary"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to List
                </button>
                
                <div className="flex-1 overflow-y-auto space-y-4 pr-1">
                  {/* Summary Card */}
                  <div className="bg-base-200/50 rounded-xl p-3 border border-base-content/5">
                    <div className="flex justify-between items-start mb-2">
                       <h3 className="font-bold text-base">{expandedDevice.hostname}</h3>
                       <span className={`badge badge-sm ${expandedDevice.source === 'netbox' ? 'badge-primary' : 'badge-neutral'}`}>
                         {expandedDevice.source}
                       </span>
                    </div>
                    <div className="grid grid-cols-2 gap-y-2 text-[11px]">
                      <div className="flex flex-col">
                        <span className="opacity-40 uppercase font-bold tracking-tighter">Management IP</span>
                        <span className="font-mono">{expandedDevice.management_ip || '-'}</span>
                      </div>
                      <div className="flex flex-col">
                        <span className="opacity-40 uppercase font-bold tracking-tighter">Vendor</span>
                        <span>{expandedDevice.vendor || '-'}</span>
                      </div>
                      <div className="flex flex-col">
                        <span className="opacity-40 uppercase font-bold tracking-tighter">Model</span>
                        <span>{expandedDevice.model || '-'}</span>
                      </div>
                      <div className="flex flex-col">
                        <span className="opacity-40 uppercase font-bold tracking-tighter">Role</span>
                        <span>{expandedDevice.role || '-'}</span>
                      </div>
                    </div>
                  </div>

                  {/* Integrated Unified Table */}
                  <div>
                    <div className="flex items-center gap-2 mb-2 px-1">
                      <Layers className="w-3.5 h-3.5 text-primary" />
                      <span className="text-xs font-bold uppercase">Integrated Inventory Table</span>
                    </div>
                    <div className="bg-base-200/30 rounded-lg overflow-hidden border border-base-content/5">
                      <table className="table table-compact w-full text-[10px]">
                        <thead className="bg-base-200">
                          <tr>
                            <th className="py-1">Interface</th>
                            <th className="py-1">IP Address</th>
                            <th className="py-1">Connection (Peer)</th>
                            <th className="py-1 text-right">Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {expandedDevice.interfaces && expandedDevice.interfaces.length > 0 ? (
                            expandedDevice.interfaces.map((iface, i) => {
                              const link = expandedDevice.links?.find(l => l.local_interface === iface.name);
                              return (
                                <tr key={i} className="hover:bg-base-200/50">
                                  <td className="font-bold py-1.5">{iface.name}</td>
                                  <td className="font-mono py-1.5">{iface.ip_address || '-'}</td>
                                  <td className="py-1.5">
                                    {link ? (
                                      <div className="flex flex-col">
                                        <span className="font-bold text-primary">{link.peer_hostname}</span>
                                        <span className="opacity-50 text-[9px]">{link.peer_interface}</span>
                                      </div>
                                    ) : (
                                      <span className="opacity-20">-</span>
                                    )}
                                  </td>
                                  <td className="text-right py-1.5">
                                    <span className={`badge badge-xs ${iface.status === 'active' || iface.status === 'up' ? 'badge-success' : 'badge-ghost opacity-50'}`}>
                                      {iface.status || 'unknown'}
                                    </span>
                                  </td>
                                </tr>
                              );
                            })
                          ) : (
                            <tr><td colSpan={4} className="text-center italic opacity-40">No interfaces found</td></tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="relative mb-4">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 opacity-40" />
                  <input 
                    type="text" 
                    placeholder="Search hostname, IP, role..." 
                    className="input input-sm input-bordered w-full pl-10 bg-base-200 focus:bg-base-100 border-none rounded-lg"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                  <button 
                    onClick={fetchInventory}
                    disabled={isLoadingInventory}
                    className="absolute right-2 top-1/2 -translate-y-1/2 btn btn-ghost btn-xs btn-square"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${isLoadingInventory ? 'animate-spin' : ''}`} />
                  </button>
                </div>

                <div className="flex-1 overflow-y-auto space-y-2 pr-1">
                  {isLoadingInventory ? (
                    <div className="flex flex-col items-center justify-center h-32 opacity-50 italic text-sm">
                      <Loader2 className="w-6 h-6 animate-spin mb-2" />
                      Fetching unified records...
                    </div>
                  ) : filteredDevices.length > 0 ? (
                    filteredDevices.map((dev, idx) => (
                      <div 
                        key={idx} 
                        onClick={() => fetchDeviceDetail(dev.hostname)}
                        className="p-3 bg-base-200/50 hover:bg-base-200 rounded-xl border border-transparent hover:border-primary/20 transition-all cursor-pointer group"
                      >
                        <div className="flex justify-between items-start">
                          <div>
                            <div className="font-bold text-sm flex items-center gap-1.5">
                              {dev.hostname}
                              <span className={`badge badge-xs text-[10px] ${dev.source === 'netbox' ? 'badge-primary' : 'badge-neutral'}`}>
                                {dev.source}
                              </span>
                            </div>
                            <div className="text-[11px] opacity-60 flex items-center gap-2 mt-0.5">
                              <span className="font-mono">{dev.management_ip || 'No IP'}</span>
                              <span>•</span>
                              <span>{dev.vendor}</span>
                              <span>•</span>
                              <span>{dev.role}</span>
                            </div>
                          </div>
                          {isEnriching && searchQuery === '' ? (
                            <Loader2 className="w-4 h-4 animate-spin opacity-40" />
                          ) : (
                            <ChevronRight className="w-4 h-4 opacity-0 group-hover:opacity-40 transition-opacity" />
                          )}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="text-center py-10 opacity-40 text-sm italic">
                      No devices found in snapshot
                    </div>
                  )}
                </div>
                
                <div className="mt-4 pt-4 border-t border-base-content/5 flex justify-between items-center">
                   <span className="text-[10px] opacity-40 font-mono">Found {filteredDevices.length} matches</span>
                   <div className="flex gap-2">
                      <span className="badge badge-outline badge-xs opacity-50 uppercase text-[10px]">{devices.filter(d=>d.source==='netbox').length} NB</span>
                      <span className="badge badge-outline badge-xs opacity-50 uppercase text-[10px]">{devices.filter(d=>d.source==='local_db').length} DB</span>
                   </div>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="h-full flex flex-col bg-base-200/30">
            {/* Model Selector Bar */}
            <div className={`p-2 border-b border-base-content/5 flex items-center justify-between gap-2
              ${modelFetchError ? 'bg-error/10' : 'bg-base-100'}`}>
              <div className="flex items-center gap-1.5 opacity-60 ml-1">
                 <Cpu className="w-3.5 h-3.5" />
                 <span className="text-[10px] font-bold uppercase tracking-wider">Model</span>
              </div>
              
              <div className="flex items-center gap-2">
                {modelFetchError && (
                  <div className="tooltip tooltip-left" data-tip={modelFetchError}>
                    <Info className="w-3.5 h-3.5 text-error ring-1 ring-error/20 rounded-full" />
                  </div>
                )}
                <select 
                  className={`select select-bordered select-xs bg-base-200 border-none font-mono text-[10px] h-7 min-h-0 focus:outline-none 
                    ${availableModels.length === 0 ? 'opacity-50' : ''}`}
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={availableModels.length === 0}
                >
                  {availableModels.length > 0 ? (
                    availableModels.map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))
                  ) : (
                    <option disabled>No local models found</option>
                  )}
                </select>
                <button 
                  onClick={fetchModels}
                  className="btn btn-ghost btn-xs btn-square"
                >
                  <RefreshCw className="w-3 h-3" />
                </button>
              </div>
            </div>

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
              {(activeTab === 'ollama' ? ollamaMessages : inventoryAiMessages).length === 0 && (
                <div className="flex flex-col items-center justify-center h-full opacity-30 text-center px-10 pt-10">
                  {activeTab === 'inventory_ai' ? <Bot className="w-12 h-12 mb-4" /> : <Cpu className="w-12 h-12 mb-4" />}
                  <p className="text-sm font-semibold">
                    {activeTab === 'inventory_ai' ? 'Project Inventory AI' : 'Ollama Unrestricted Playground'}
                  </p>
                  <p className="text-[11px] mt-2">
                    {activeTab === 'inventory_ai' 
                      ? 'I can answer questions about your devices, vendors, and roles based on the current snapshot.'
                      : 'Speak directly to the local model. No grounding, no system prompts, just raw intelligence.'}
                  </p>
                  {availableModels.length === 0 && !modelFetchError && (
                    <div className="mt-4 flex items-center gap-2 text-primary animate-pulse">
                       <Loader2 className="w-4 h-4 animate-spin" />
                       <span className="text-[10px] font-bold uppercase">Scanning local server...</span>
                    </div>
                  )}
                </div>
              )}
              {(activeTab === 'ollama' ? ollamaMessages : inventoryAiMessages).map((msg, idx) => (
                <div key={idx} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                  <div className={`max-w-[85%] p-3 rounded-2xl text-[13px] leading-relaxed
                    ${msg.role === 'user' 
                      ? 'bg-primary text-primary-content rounded-tr-none shadow-md' 
                      : msg.role === 'system'
                        ? 'bg-error/10 text-error border border-error/20 italic'
                        : 'bg-base-100 text-base-content rounded-tl-none shadow-sm'}`}
                  >
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                  </div>
                  <span className="text-[9px] opacity-30 mt-1 px-1">{msg.timestamp}</span>
                </div>
              ))}
              {(activeTab === 'ollama' ? isOllamaLoading : isInventoryAiLoading) && (
                <div className="flex items-center gap-2 px-1 text-primary animate-pulse">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span className="text-[10px] font-bold uppercase tracking-widest">Ollama is thinking...</span>
                </div>
              )}
            </div>

            {/* Input */}
            <div className="p-3 bg-base-100 border-t border-base-content/10">
              <div className="relative">
                <input 
                  type="text"
                  value={activeTab === 'ollama' ? ollamaPrompt : inventoryAiPrompt}
                  onChange={(e) => activeTab === 'ollama' ? setOllamaPrompt(e.target.value) : setInventoryAiPrompt(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleChatSend(activeTab === 'ollama' ? 'playground' : 'inventory')}
                  placeholder={availableModels.length > 0 ? (activeTab === 'inventory_ai' ? "Ask about your network..." : "Ask local model anything...") : "Ollama not available"}
                  className="input input-bordered w-full pr-12 text-sm bg-base-200 border-none h-11 focus:outline-none"
                  disabled={availableModels.length === 0}
                />
                <button 
                  onClick={() => handleChatSend(activeTab === 'ollama' ? 'playground' : 'inventory')}
                  disabled={(activeTab === 'ollama' ? isOllamaLoading : isInventoryAiLoading) || (activeTab === 'ollama' ? !ollamaPrompt.trim() : !inventoryAiPrompt.trim()) || availableModels.length === 0}
                  className={`absolute right-1 top-1/2 -translate-y-1/2 btn btn-sm btn-circle ${activeTab === 'inventory_ai' ? 'btn-secondary' : 'btn-primary'}`}
                >
                  <Send className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default FloatingInventoryOverlay;
