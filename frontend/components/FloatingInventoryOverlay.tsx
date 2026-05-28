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
  Bot,
  Activity,
  Quote,
  Copy,
  Square
} from 'lucide-react';
import { llmApi } from '../src/services/api';

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
  groundingMeta?: {
    answer_scope?: string;
    freshness_class?: string;
    display_freshness_class?: string;
    confidence?: number;
    source_used?: string[];
    display_source_used?: string[];
    source_age?: Array<{ source?: string; scope?: string; source_category?: string; age_seconds?: number | null; freshness_class?: string }>;
    display_source_age?: Array<{ source?: string; scope?: string; source_category?: string; age_seconds?: number | null; freshness_class?: string }>;
    conflicts?: Array<{ code?: string; summary?: string; message?: string; severity?: string }>;
    display_conflicts?: Array<{ code?: string; summary?: string; message?: string; severity?: string }>;
  };
}

type OperatingMode = 'query' | 'thinking' | 'expert';

interface QuotedMessage {
  role: Message['role'];
  content: string;
  timestamp: string;
}

const PREFERRED_OVERLAY_MODEL = 'ollama::qwen2.5-coder:3b';
const OVERLAY_MIN_WIDTH = 340;
const OVERLAY_DEFAULT_WIDTH = 420;
const OVERLAY_MAX_WIDTH = 920;
const OVERLAY_MARGIN = 12;

const OPERATING_MODE_LABELS: Record<OperatingMode, string> = {
  query: 'Query',
  thinking: 'Thinking',
  expert: 'Expert',
};

const OPERATING_MODE_PROMPTS: Record<OperatingMode, string> = {
  query: 'Operating mode: Query. Answer directly and concisely. Use the best available grounded evidence, but do not add reasoning steps unless needed for clarity.',
  thinking: 'Operating mode: Thinking. Explain the reasoning path briefly before the answer. Keep it operational and avoid unrelated background.',
  expert: 'Operating mode: Expert. Correlate across relevant events, sources, timing, topology, telemetry, syslog, inventory, and configuration evidence. Identify possible relationships only when evidence supports them.',
};

function pickPreferredOverlayModel(models: string[]): string {
  if (models.includes(PREFERRED_OVERLAY_MODEL)) return PREFERRED_OVERLAY_MODEL;
  const localQwen = models.find((model) => model.startsWith('ollama::qwen2.5'));
  if (localQwen) return localQwen;
  const anyLocal = models.find((model) => model.startsWith('ollama::'));
  if (anyLocal) return anyLocal;
  return models[0] || '';
}

function clampOverlayWidth(width: number) {
  const viewportMax = Math.max(OVERLAY_MIN_WIDTH, window.innerWidth - OVERLAY_MARGIN * 2);
  return Math.min(Math.max(width, OVERLAY_MIN_WIDTH), Math.min(OVERLAY_MAX_WIDTH, viewportMax));
}

function clampOverlayPosition(x: number, y: number, width: number) {
  const maxX = Math.max(OVERLAY_MARGIN, window.innerWidth - width - OVERLAY_MARGIN);
  const maxY = Math.max(OVERLAY_MARGIN, window.innerHeight - 120);
  return {
    x: Math.min(Math.max(x, OVERLAY_MARGIN), maxX),
    y: Math.min(Math.max(y, OVERLAY_MARGIN), maxY),
  };
}

function quoteForPrompt(quoted: QuotedMessage | null): string {
  if (!quoted) return '';
  const trimmed = quoted.content.replace(/\s+/g, ' ').trim();
  return `Quoted previous message (${quoted.role}, ${quoted.timestamp}):\n${trimmed}`;
}

function buildModePrompt(prompt: string, mode: OperatingMode, quoted: QuotedMessage | null): string {
  const quote = quoteForPrompt(quoted);
  return [
    OPERATING_MODE_PROMPTS[mode],
    quote ? `Use this quoted context only when it is relevant:\n${quote}` : '',
    `User request:\n${prompt}`,
  ].filter(Boolean).join('\n\n');
}

async function copyText(value: string) {
  if (!value) return;
  await navigator.clipboard?.writeText(value);
}

const FloatingInventoryOverlay: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [activeTab, setActiveTab] = useState<'inventory' | 'ollama' | 'inventory_ai' | 'system_ai'>('inventory');
  const [overlayWidth, setOverlayWidth] = useState(() => clampOverlayWidth(OVERLAY_DEFAULT_WIDTH));
  const [position, setPosition] = useState(() => clampOverlayPosition(window.innerWidth - OVERLAY_DEFAULT_WIDTH - 30, 100, OVERLAY_DEFAULT_WIDTH));
  const [isDragging, setIsDragging] = useState(false);
  const [resizeEdge, setResizeEdge] = useState<'left' | 'right' | null>(null);
  const dragStartPos = useRef({ x: 0, y: 0 });
  const resizeStartRef = useRef({ x: 0, width: OVERLAY_DEFAULT_WIDTH, left: 0 });

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

  // System AI State
  const [systemAiPrompt, setSystemAiPrompt] = useState('');
  const [systemAiMessages, setSystemAiMessages] = useState<Message[]>([]);
  const [isSystemAiLoading, setIsSystemAiLoading] = useState(false);
  const [expandedSystemAiEvidenceIndex, setExpandedSystemAiEvidenceIndex] = useState<number | null>(null);
  const [operatingMode, setOperatingMode] = useState<OperatingMode>('query');
  const [quotedMessage, setQuotedMessage] = useState<QuotedMessage | null>(null);

  // Common LLM Params
  const [availableModels, setAvailableModels] = useState<string[]>(['ollama::qwen2.5-coder:3b']);
  const [selectedModel, setSelectedModel] = useState<string>('ollama::qwen2.5-coder:3b');
  const [modelFetchError, setModelFetchError] = useState<string | null>(null);
  const [hasFetchedModels, setHasFetchedModels] = useState(false);
  
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeAbortController = useRef<AbortController | null>(null);
  const previousActiveTab = useRef(activeTab);

  // --- Draggable Logic ---
  const handleMouseDown = (e: React.MouseEvent) => {
    if (resizeEdge) return;
    setIsDragging(true);
    dragStartPos.current = {
      x: e.clientX - position.x,
      y: e.clientY - position.y
    };
  };

  const handleResizeMouseDown = (edge: 'left' | 'right') => (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setResizeEdge(edge);
    resizeStartRef.current = {
      x: e.clientX,
      width: overlayWidth,
      left: position.x,
    };
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isDragging) return;
      setPosition(clampOverlayPosition(
        e.clientX - dragStartPos.current.x,
        e.clientY - dragStartPos.current.y,
        overlayWidth,
      ));
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
  }, [isDragging, overlayWidth]);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!resizeEdge) return;
      const delta = e.clientX - resizeStartRef.current.x;
      if (resizeEdge === 'right') {
        const nextWidth = clampOverlayWidth(resizeStartRef.current.width + delta);
        setOverlayWidth(nextWidth);
        setPosition((prev) => clampOverlayPosition(prev.x, prev.y, nextWidth));
        return;
      }

      const rawWidth = resizeStartRef.current.width - delta;
      const nextWidth = clampOverlayWidth(rawWidth);
      const widthDelta = nextWidth - resizeStartRef.current.width;
      const nextLeft = resizeStartRef.current.left - widthDelta;
      setOverlayWidth(nextWidth);
      setPosition((prev) => clampOverlayPosition(nextLeft, prev.y, nextWidth));
    };
    const handleMouseUp = () => setResizeEdge(null);

    if (resizeEdge) {
      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', handleMouseUp);
    }
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [resizeEdge]);

  useEffect(() => {
    const handleWindowResize = () => {
      setOverlayWidth((currentWidth) => {
        const nextWidth = clampOverlayWidth(currentWidth);
        setPosition((currentPosition) => clampOverlayPosition(currentPosition.x, currentPosition.y, nextWidth));
        return nextWidth;
      });
    };
    window.addEventListener('resize', handleWindowResize);
    return () => window.removeEventListener('resize', handleWindowResize);
  }, []);

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
    if ((activeTab === 'ollama' || activeTab === 'inventory_ai' || activeTab === 'system_ai') && !hasFetchedModels) {
      fetchModels();
      return;
    }
    const switchedTab = previousActiveTab.current !== activeTab;
    if (switchedTab && (activeTab === 'ollama' || activeTab === 'inventory_ai' || activeTab === 'system_ai')) {
      const preferredModel = pickPreferredOverlayModel(availableModels);
      if (preferredModel && selectedModel !== preferredModel) {
        setSelectedModel(preferredModel);
      }
    }
    previousActiveTab.current = activeTab;
  }, [activeTab, availableModels, devices.length, selectedModel, hasFetchedModels]);

  const fetchModels = async () => {
    setModelFetchError(null);
    try {
      // Fetch Ollama local models (if available)
      const resp = await fetch('/api/v1/llm/chat/ollama/models');
      let localModels: string[] = [];
      if (resp.ok) {
        const data = await resp.json();
        if (data.models && data.models.length > 0) {
          localModels = data.models.map((m: string) => `ollama::${m}`);
        }
      }

      // Fetch runtime providers (includes Gemini cloud models)
      const runtimeResp = await fetch('/api/v1/llm/chat/runtime');
      let combined: string[] = [];
      if (runtimeResp.ok) {
        const runtime = await runtimeResp.json();
        const gemini = runtime.providers?.find((p: any) => p.name === 'gemini');
        if (gemini && Array.isArray(gemini.models) && gemini.models.length > 0) {
          const g = gemini.models.map((m: string) => `gemini::${m}`);
          combined = [...g];
        }
        // Append local Ollama models after cloud options
        combined = [...combined, ...localModels];
      } else {
        // Fallback: show only local models (prefixed)
        combined = localModels;
      }

      if (!combined.includes('ollama::qwen2.5-coder:3b')) {
        combined.push('ollama::qwen2.5-coder:3b');
      }

      if (combined.length === 0) {
        setModelFetchError('No models found (cloud or local).');
      } else {
        setAvailableModels(combined);
        setSelectedModel(pickPreferredOverlayModel(combined));
      }
      setHasFetchedModels(true);
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

  const stopActiveResponse = () => {
    activeAbortController.current?.abort();
    activeAbortController.current = null;
  };

  const quoteMessage = (msg: Message) => {
    setQuotedMessage({
      role: msg.role,
      content: msg.content,
      timestamp: msg.timestamp,
    });
  };

  // --- Chat Handling ---
  const handleChatSend = async (mode: 'playground' | 'inventory' | 'system') => {
    const isPlayground = mode === 'playground';
    const isInventory = mode === 'inventory';
    const prompt = isPlayground ? ollamaPrompt : isInventory ? inventoryAiPrompt : systemAiPrompt;
    if (!prompt.trim()) return;
    const controller = new AbortController();
    activeAbortController.current = controller;
    const quotedForThisTurn = quotedMessage;
    const effectivePrompt = mode === 'system'
      ? buildModePrompt(prompt, operatingMode, quotedForThisTurn)
      : quotedForThisTurn
        ? `${quoteForPrompt(quotedForThisTurn)}\n\nUser request:\n${prompt}`
        : prompt;

    const userMsg: Message = {
      role: 'user',
      content: prompt,
      timestamp: new Date().toLocaleTimeString(),
    };

    if (isPlayground) {
      setOllamaMessages((prev) => [...prev, userMsg]);
      setOllamaPrompt('');
      setIsOllamaLoading(true);
    } else if (isInventory) {
      setInventoryAiMessages((prev) => [...prev, userMsg]);
      setInventoryAiPrompt('');
      setIsInventoryAiLoading(true);
    } else {
      setSystemAiMessages((prev) => [...prev, userMsg]);
      setSystemAiPrompt('');
      setIsSystemAiLoading(true);
    }
    setQuotedMessage(null);

    try {
      if (mode === 'system') {
        const runtimeModel = selectedModel.startsWith('gemini::') || selectedModel.startsWith('ollama::')
          ? selectedModel.split('::', 2)[1]
          : selectedModel;
        const runtimeProvider = selectedModel.startsWith('gemini::') ? 'gemini' : 'ollama';

        if (runtimeModel) {
          await llmApi.updateRuntime({
            provider: runtimeProvider,
            model: runtimeModel,
            clear_history: false,
          }, { signal: controller.signal });
        }
        const data = await llmApi.chat(effectivePrompt, { signal: controller.signal });
        const assistantMsg: Message = {
          role: 'assistant',
          content: data.content || 'No response from model.',
          timestamp: new Date().toLocaleTimeString(),
          groundingMeta: data.grounding_meta,
        };
        setSystemAiMessages((prev) => [...prev, assistantMsg]);
        return;
      } else if (selectedModel.startsWith('gemini::')) {
        const resp = await fetch('/api/v1/llm/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: effectivePrompt,
            model: selectedModel.split('::', 2)[1],
            grounding: mode !== 'playground',
          }),
          signal: controller.signal,
        });
        const data = await resp.json();
        const assistantMsg: Message = {
          role: 'assistant',
          content: data.content || 'No response from model.',
          timestamp: new Date().toLocaleTimeString(),
        };
        if (isPlayground) {
          setOllamaMessages((prev) => [...prev, assistantMsg]);
        } else if (isInventory) {
          setInventoryAiMessages((prev) => [...prev, assistantMsg]);
        }
        return;
      } else {
        const modelName = selectedModel.startsWith('ollama::') ? selectedModel.split('::', 2)[1] : selectedModel;
        const endpoint = isPlayground
          ? '/api/v1/llm/chat/ollama/playground'
          : '/api/v1/llm/chat/ollama/context_query';
        const resp = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: effectivePrompt, model: modelName }),
          signal: controller.signal,
        });
        const data = await resp.json();
        const assistantMsg: Message = {
          role: 'assistant',
          content: data.content || 'No response from model.',
          timestamp: new Date().toLocaleTimeString(),
        };
        if (isPlayground) {
          setOllamaMessages((prev) => [...prev, assistantMsg]);
        } else if (isInventory) {
          setInventoryAiMessages((prev) => [...prev, assistantMsg]);
        }
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        const haltedMsg: Message = {
          role: 'system',
          content: 'Response halted.',
          timestamp: new Date().toLocaleTimeString(),
        };
        if (isPlayground) {
          setOllamaMessages((prev) => [...prev, haltedMsg]);
        } else if (isInventory) {
          setInventoryAiMessages((prev) => [...prev, haltedMsg]);
        } else {
          setSystemAiMessages((prev) => [...prev, haltedMsg]);
        }
        return;
      }
      const errMsg: Message = {
        role: 'system',
        content: `Error: ${err}`,
        timestamp: new Date().toLocaleTimeString(),
      };
      if (isPlayground) {
        setOllamaMessages((prev) => [...prev, errMsg]);
      } else if (isInventory) {
        setInventoryAiMessages((prev) => [...prev, errMsg]);
      } else {
        setSystemAiMessages((prev) => [...prev, errMsg]);
      }
    } finally {
      if (isPlayground) setIsOllamaLoading(false);
      else if (isInventory) setIsInventoryAiLoading(false);
      else setIsSystemAiLoading(false);
      if (activeAbortController.current === controller) {
        activeAbortController.current = null;
      }
    }
  };

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [ollamaMessages, inventoryAiMessages, systemAiMessages]);

  return (
    <div 
      className="fixed z-9999 shadow-2xl border border-base-content/10 bg-base-100 rounded-xl overflow-hidden flex flex-col"
      style={{ 
        width: `${overlayWidth}px`,
        maxWidth: `calc(100vw - ${OVERLAY_MARGIN * 2}px)`,
        height: '550px',
        maxHeight: `calc(100vh - ${OVERLAY_MARGIN * 2}px)`,
        left: `${position.x}px`, 
        top: `${position.y}px`,
        transition: isDragging || resizeEdge ? 'none' : 'all 0.1s ease'
      }}
    >
      <div
        className="absolute left-0 top-10 bottom-0 z-20 w-1.5 cursor-ew-resize hover:bg-primary/30"
        onMouseDown={handleResizeMouseDown('left')}
        title="Resize overlay"
      />
      <div
        className="absolute right-0 top-10 bottom-0 z-20 w-1.5 cursor-ew-resize hover:bg-primary/30"
        onMouseDown={handleResizeMouseDown('right')}
        title="Resize overlay"
      />
      {/* Header / Drag Handle */}
      <div 
        className="bg-primary text-primary-content p-3 flex items-center justify-between cursor-grab active:cursor-grabbing select-none"
        onMouseDown={handleMouseDown}
      >
        <div className="flex items-center gap-2 font-bold text-base-200 text-sm">
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
      <div className="flex overflow-x-auto border-b border-base-content/10 bg-base-200/50">
        <button 
          onClick={() => setActiveTab('inventory')}
          className={`min-w-[72px] flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'inventory' ? 'bg-base-100 text-primary border-b-2 border-primary' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Database className="w-3.5 h-3.5" />
          INVENTORY
        </button>
        <button 
          onClick={() => setActiveTab('ollama')}
          className={`min-w-[72px] flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'ollama' ? 'bg-base-100 text-secondary border-b-2 border-secondary' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Cpu className="w-3.5 h-3.5" />
          GENERAL AI
        </button>
        <button 
          onClick={() => setActiveTab('inventory_ai')}
          className={`min-w-[72px] flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'inventory_ai' ? 'bg-base-100 text-accent border-b-2 border-accent' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Bot className="w-3.5 h-3.5" />
          NETWORK AI
        </button>
        <button 
          onClick={() => setActiveTab('system_ai')}
          className={`min-w-[72px] flex-1 p-2.5 flex items-center justify-center gap-1.5 text-[10px] font-bold transition-colors
            ${activeTab === 'system_ai' ? 'bg-base-100 text-success border-b-2 border-success' : 'hover:bg-base-200 opacity-60'}`}
        >
          <Activity className="w-3.5 h-3.5" />
          SYSTEM AI
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
            <div className={`px-3 md:px-5 py-2 border-b border-base-content/5 flex items-center justify-between gap-2
              ${modelFetchError ? 'bg-error/10' : 'bg-base-100'}`}>
              <div className="flex items-center gap-1.5 opacity-60 ml-1">
                 <Cpu className="w-3.5 h-3.5" />
                 <span className="text-[10px] font-bold uppercase tracking-wider">Model</span>
              </div>
              
              <div className="flex min-w-0 items-center gap-2">
                {modelFetchError && (
                  <div className="tooltip tooltip-left" data-tip={modelFetchError}>
                    <Info className="w-3.5 h-3.5 text-error ring-1 ring-error/20 rounded-full" />
                  </div>
                )}
                <select
                  className={`select select-bordered select-xs bg-base-200 border-none font-mono text-[10px] h-7 min-h-0 max-w-[210px] min-w-[140px] focus:outline-none
                    ${availableModels.length === 0 ? 'opacity-50' : ''}`}
                  value={selectedModel}
                  onChange={(e) => setSelectedModel(e.target.value)}
                  disabled={availableModels.length === 0}
                >
                  {availableModels.length > 0 ? (
                    availableModels.map(m => (
                      <option key={m} value={m}>
                        {m.startsWith('gemini::') ? `Gemini • ${m.split('::',2)[1]}` : (m.startsWith('ollama::') ? `${m.split('::',2)[1]} • Local` : m)}
                      </option>
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

            {activeTab === 'system_ai' && (
              <div className="px-3 sm:px-7 md:px-10 py-2 border-b border-base-content/5 bg-base-100/70 flex items-center justify-between gap-2">
                <span className="text-[10px] font-bold uppercase tracking-wider opacity-60">Mode</span>
                <div className="join">
                  {(['query', 'thinking', 'expert'] as OperatingMode[]).map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setOperatingMode(item)}
                      className={`join-item btn btn-xs min-h-0 h-6 px-2 text-[10px] ${operatingMode === item ? 'btn-success' : 'btn-ghost bg-base-200/70'}`}
                      title={OPERATING_MODE_PROMPTS[item]}
                    >
                      {OPERATING_MODE_LABELS[item]}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Messages */}
            <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
              {(activeTab === 'ollama' ? ollamaMessages : activeTab === 'inventory_ai' ? inventoryAiMessages : systemAiMessages).length === 0 && (
                <div className="flex flex-col items-center justify-center h-full opacity-80 text-center px-10 pt-10">
                  {activeTab === 'inventory_ai' ? <Bot className="w-12 h-12 mb-4" /> : activeTab === 'system_ai' ? <Activity className="w-12 h-12 mb-4" /> : <Cpu className="w-12 h-12 mb-4" />}
                  <p className="text-sm font-semibold">
                    {activeTab === 'inventory_ai' ? 'Project Inventory AI' : activeTab === 'system_ai' ? 'System AI' : 'Ollama Unrestricted Playground'}
                  </p>
                  <p className="text-[11px] mt-2 text-base-content/90">
                    {activeTab === 'inventory_ai' 
                      ? 'I can answer questions about your devices, vendors, and roles based on the current snapshot.'
                      : activeTab === 'system_ai'
                        ? 'I can answer live operational, inventory, telemetry, syslog, planned-state, and RAG-grounded questions across the network.'
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
              {(activeTab === 'ollama' ? ollamaMessages : activeTab === 'inventory_ai' ? inventoryAiMessages : systemAiMessages).map((msg, idx) => {
                const isSystemAnswer = activeTab === 'system_ai' && msg.role === 'assistant';
                const isEvidenceOpen = expandedSystemAiEvidenceIndex === idx;
                const grounding = msg.groundingMeta;
                const evidenceSources: Array<{ source?: string; scope?: string; source_category?: string; age_seconds?: number | null; freshness_class?: string }> = grounding?.display_source_age?.length
                  ? grounding.display_source_age
                  : grounding?.source_age?.length
                    ? grounding.source_age
                    : (grounding?.display_source_used?.map(source => ({ source })) ?? grounding?.source_used?.map(source => ({ source })) ?? []);
                const evidenceConflicts = grounding?.display_conflicts?.length
                  ? grounding.display_conflicts
                  : (grounding?.conflicts ?? []);
                const freshnessLabel = grounding?.display_freshness_class || grounding?.freshness_class;
                const freshnessBadgeClass = freshnessLabel === 'live'
                  ? 'badge-success'
                  : freshnessLabel === 'warm'
                    ? 'badge-warning'
                    : freshnessLabel === 'stale'
                      ? 'badge-error'
                      : 'badge-ghost';

                return (
                  <div key={idx} className={`flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                    <div className={`flex items-start gap-2 max-w-[92%] ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}>
                      <div className={`p-3 rounded-2xl text-[13px] leading-relaxed
                        ${msg.role === 'user'
                          ? 'bg-primary text-primary-content rounded-tr-none shadow-md'
                          : msg.role === 'system'
                            ? 'bg-error/10 text-error border border-error/20 italic'
                            : 'bg-base-100 text-base-content rounded-tl-none shadow-sm'}`}
                      >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                      </div>
                      {isSystemAnswer && grounding && (
                        <button
                          type="button"
                          onClick={() => setExpandedSystemAiEvidenceIndex(isEvidenceOpen ? null : idx)}
                          className={`mt-1 btn btn-ghost btn-xs btn-circle ${isEvidenceOpen ? 'btn-active text-success' : 'opacity-70 hover:opacity-100'}`}
                          aria-label={isEvidenceOpen ? 'Hide evidence' : 'Show evidence'}
                          title={isEvidenceOpen ? 'Hide evidence' : 'Show evidence'}
                        >
                          <Info className="w-4 h-4" />
                        </button>
                      )}
                      <div className="mt-1 flex flex-col gap-1 opacity-60 hover:opacity-100 transition-opacity">
                        <button
                          type="button"
                          onClick={() => quoteMessage(msg)}
                          className="btn btn-ghost btn-xs btn-circle"
                          aria-label="Quote message"
                          title="Quote message"
                        >
                          <Quote className="w-3.5 h-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => copyText(msg.content).catch(() => undefined)}
                          className="btn btn-ghost btn-xs btn-circle"
                          aria-label="Copy message"
                          title="Copy message"
                        >
                          <Copy className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {isSystemAnswer && grounding && isEvidenceOpen && (
                      <div className="mt-2 w-full max-w-[92%] rounded-xl border border-base-content/10 bg-base-200/50 p-3 text-[10px]">
                        <div className="mb-2 flex flex-wrap items-center gap-1.5">
                          <span className="badge badge-xs badge-success">Grounded</span>
                          {grounding.answer_scope && <span className="badge badge-xs badge-outline">{grounding.answer_scope}</span>}
                          {freshnessLabel && <span className={`badge badge-xs ${freshnessBadgeClass}`}>{freshnessLabel}</span>}
                          {typeof grounding.confidence === 'number' && <span className="badge badge-xs badge-outline">confidence {grounding.confidence.toFixed(2)}</span>}
                        </div>

                        <div className="overflow-x-auto">
                          <table className="table table-xs">
                            <thead>
                              <tr>
                                <th>Source</th>
                                <th>Category</th>
                                <th>Age</th>
                                <th>Freshness</th>
                              </tr>
                            </thead>
                            <tbody>
                              {evidenceSources.map((entry, sourceIdx) => {
                                const ageSeconds = entry.age_seconds;
                                const ageLabel = typeof ageSeconds === 'number'
                                  ? ageSeconds < 60
                                    ? `${ageSeconds}s`
                                    : ageSeconds < 3600
                                      ? `${Math.round(ageSeconds / 60)}m`
                                      : `${Math.round(ageSeconds / 3600)}h`
                                  : 'n/a';
                                return (
                                  <tr key={`${entry.source || 'source'}-${sourceIdx}`}>
                                    <td className="font-mono">{entry.source || 'unknown'}</td>
                                    <td>{entry.source_category || 'other'}</td>
                                    <td>{ageLabel}</td>
                                    <td>{entry.freshness_class || 'n/a'}</td>
                                  </tr>
                                );
                              })}
                              {evidenceSources.length === 0 && (
                                <tr>
                                  <td colSpan={4} className="text-center opacity-60">No evidence details available.</td>
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>

                        {evidenceConflicts.length > 0 && (
                          <div className="mt-2">
                            <div className="mb-1 font-semibold opacity-70">Conflicts</div>
                            <ul className="space-y-1">
                              {evidenceConflicts.map((conflict, conflictIdx) => (
                                <li key={`${conflict.code || 'conflict'}-${conflictIdx}`} className="rounded-md bg-error/10 px-2 py-1 text-error">
                                  [{(conflict.severity || conflict.code || 'info').toUpperCase()}] {conflict.summary || conflict.message || 'Conflict detected'}
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    <span className="text-[9px] opacity-55 mt-1 px-1">{msg.timestamp}</span>
                  </div>
                );
              })}
              {(activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading) && (
                <div className="flex items-center gap-2 px-1 text-primary animate-pulse">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span className="text-[10px] font-bold uppercase tracking-widest">
                    {activeTab === 'system_ai' ? 'System AI is thinking...' : 'Ollama is thinking...'}
                  </span>
                </div>
              )}
            </div>

            {/* Input */}
            <div className="p-3 bg-base-100 border-t border-base-content/10">
              {quotedMessage && (
                <div className="mb-2 rounded-lg border border-success/20 bg-success/5 px-3 py-2 text-[10px]">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-bold uppercase tracking-wider text-success">Quoted {quotedMessage.role}</div>
                      <div className="truncate opacity-70">{quotedMessage.content}</div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setQuotedMessage(null)}
                      className="btn btn-ghost btn-xs btn-circle shrink-0"
                      aria-label="Clear quoted message"
                      title="Clear quoted message"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              )}
              <div className="relative">
                <input 
                  type="text"
                  value={activeTab === 'ollama' ? ollamaPrompt : activeTab === 'inventory_ai' ? inventoryAiPrompt : systemAiPrompt}
                  onChange={(e) => {
                    if (activeTab === 'ollama') setOllamaPrompt(e.target.value);
                    else if (activeTab === 'inventory_ai') setInventoryAiPrompt(e.target.value);
                    else setSystemAiPrompt(e.target.value);
                  }}
                  onKeyDown={(e) => {
                    const isLoading = activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading;
                    if (e.key === 'Escape' && isLoading) stopActiveResponse();
                    if (e.key === 'Enter' && !isLoading) handleChatSend(activeTab === 'ollama' ? 'playground' : activeTab === 'inventory_ai' ? 'inventory' : 'system');
                  }}
                  placeholder={availableModels.length > 0 ? (activeTab === 'inventory_ai' ? "Ask about your network snapshot..." : activeTab === 'system_ai' ? "Ask about live ops, inventory, telemetry, or RAG..." : "Ask local model anything...") : "Ollama not available"}
                  className="input input-bordered w-full pr-12 text-sm bg-base-200 border-none h-11 focus:outline-none placeholder:text-base-content/50 placeholder:text-[13px]"
                  disabled={availableModels.length === 0}
                />
                <button 
                  onClick={() => {
                    const isLoading = activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading;
                    if (isLoading) stopActiveResponse();
                    else handleChatSend(activeTab === 'ollama' ? 'playground' : activeTab === 'inventory_ai' ? 'inventory' : 'system');
                  }}
                  disabled={!(activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading) && ((activeTab === 'ollama' ? !ollamaPrompt.trim() : activeTab === 'inventory_ai' ? !inventoryAiPrompt.trim() : !systemAiPrompt.trim()) || availableModels.length === 0)}
                  className={`absolute right-1 top-1/2 -translate-y-1/2 btn btn-sm btn-circle ${(activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading) ? 'btn-error' : activeTab === 'inventory_ai' ? 'btn-secondary' : activeTab === 'system_ai' ? 'btn-success' : 'btn-primary'}`}
                  title={(activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading) ? 'Stop response' : 'Send'}
                >
                  {(activeTab === 'ollama' ? isOllamaLoading : activeTab === 'inventory_ai' ? isInventoryAiLoading : isSystemAiLoading)
                    ? <Square className="w-3.5 h-3.5 fill-current" />
                    : <Send className="w-4 h-4" />}
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

