import React, { useState, useRef, useEffect } from 'react';
import { Network, Activity, Hash, Layers, ZoomIn, ZoomOut, Locate, Maximize, Minimize } from 'lucide-react';
import type { IntentSchema } from '../../types/intentSchema';

interface Props {
  schema: IntentSchema;
  onNavigateCategory?: (cat: string) => void;
  onNavigateOperation?: (cat: string, op: string) => void;
}

const IntentGraphCanvas: React.FC<Props> = ({ schema, onNavigateCategory, onNavigateOperation }) => {
  const [scale, setScale] = useState(0.45);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number, y: number, type: 'category' | 'operation', cat: string, op?: string } | null>(null);
  
  const containerRef = useRef<HTMLDivElement>(null);

  // Close context menu on click
  useEffect(() => {
    const closeMenu = () => setContextMenu(null);
    document.addEventListener('click', closeMenu);
    return () => document.removeEventListener('click', closeMenu);
  }, []);

  // Handle zooming via scroll wheel
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault(); // Prevent page scroll
      const zoomSensitivity = 0.05;
      const delta = e.deltaY > 0 ? -zoomSensitivity : zoomSensitivity;
      setScale((s) => Math.min(Math.max(0.2, s + delta), 3));
    };

    container.addEventListener('wheel', handleWheel, { passive: false });
    return () => container.removeEventListener('wheel', handleWheel);
  }, []);

  // Listen to fullscreen changes to update icon state
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    // Don't drag if opening context menu
    if (e.button === 2) return;
    setIsDragging(true);
    setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDragging) {
      setPosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y
      });
    }
  };

  const handleMouseUp = () => setIsDragging(false);
  const handleMouseLeave = () => setIsDragging(false);

  const zoomIn = () => setScale(s => Math.min(s + 0.2, 3));
  const zoomOut = () => setScale(s => Math.max(s - 0.2, 0.2));
  const resetZoom = () => {
    setScale(0.45);
    // Explicitly moving back to root/center top view
    setPosition({ x: 0, y: 0 });
  };

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen().catch(err => {
         console.error(`Error attempting to enable fullscreen: ${err.message}`);
      });
    } else {
      document.exitFullscreen();
    }
  };

  const handleContextMenu = (e: React.MouseEvent, type: 'category' | 'operation', cat: string, op?: string) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, type, cat, op });
  };

  return (
    <div 
      ref={containerRef}
      className="w-full h-full relative overflow-hidden bg-base-300"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseLeave}
      // Prevent default context menu on canvas bg so you can right click nodes properly without double menus
      onContextMenu={(e) => e.preventDefault()} 
      style={{ cursor: isDragging ? 'grabbing' : 'grab' }}
    >
      {/* Controls Overlay */}
      <div className="absolute top-4 right-4 z-10 flex gap-2 bg-base-100 p-2 rounded-lg shadow-lg border border-base-200">
        <button className="btn btn-sm btn-square btn-ghost tooltip tooltip-bottom" data-tip="Zoom Out" onClick={zoomOut}>
          <ZoomOut className="w-4 h-4" />
        </button>
        <div className="flex items-center justify-center w-12 text-xs font-mono font-bold opacity-70 cursor-default">
          {Math.round(scale * 100)}%
        </div>
        <button className="btn btn-sm btn-square btn-ghost tooltip tooltip-bottom" data-tip="Zoom In" onClick={zoomIn}>
          <ZoomIn className="w-4 h-4" />
        </button>
        <div className="w-px h-6 bg-base-300 mx-1 self-center" />
        <button className="btn btn-sm btn-square btn-ghost tooltip tooltip-bottom" data-tip="Locate / Reset View" onClick={resetZoom}>
          <Locate className="w-4 h-4" />
        </button>
        <button className="btn btn-sm btn-square btn-ghost tooltip tooltip-bottom" data-tip={isFullscreen ? "Exit Fullscreen" : "Fullscreen"} onClick={toggleFullscreen}>
          {isFullscreen ? <Minimize className="w-4 h-4" /> : <Maximize className="w-4 h-4" />}
        </button>
      </div>

      {/* Grid Background that scales and translates */}
      <div 
         className="absolute inset-0 origin-center pointer-events-none opacity-50"
         style={{
           backgroundImage: 'radial-gradient(circle, currentColor 1px, transparent 1px)',
           backgroundSize: `${40 * scale}px ${40 * scale}px`,
           backgroundPosition: `${position.x}px ${position.y}px`,
           color: 'var(--fallback-bc,oklch(var(--bc)/0.2))'
         }}
      />

      {/* Transformable Canvas */}
      <div 
        className="absolute top-[10%] left-1/2 origin-top transition-transform duration-75 ease-out flex flex-col items-center select-none"
        style={{ 
          transform: `translate(calc(-50% + ${position.x}px), ${position.y}px) scale(${scale})`
        }}
      >
        <div className="text-center mb-16 relative z-10">
          <div className="badge badge-error badge-lg gap-2 shadow-lg mb-4 transform hover:scale-105 transition-transform cursor-default">
             <Layers className="w-5 h-5" /> Root
          </div>
          <div className="text-3xl font-bold font-mono bg-base-100 px-8 py-4 rounded-2xl shadow-xl border-2 border-base-content/10">
             Intent Schema Parse Tree
          </div>
        </div>
        
        {/* Main Trunk Line */}
        <div className="w-1 h-16 bg-linear-to-b from-base-content/20 to-base-content/40 -mt-16 relative z-0" />

        <div className="flex gap-16 w-max justify-center items-start pt-4">
          {schema.categories.map((cat, i) => (
            <div key={cat.name} className="flex flex-col items-center relative min-w-[280px]">
              
              {/* Category Stem */}
              <div className={`w-1 h-8 bg-base-content/40`} />
              
              {/* Horizontal Connecting Branch */}
              <div className="absolute top-0 h-1 bg-base-content/40 rounded-full" 
                   style={{ 
                     left: i === 0 ? '50%' : 0, 
                     right: i === schema.categories.length - 1 ? '50%' : 0,
                     width: schema.categories.length === 1 ? '0' : '100%'
                   }} />
              
              {/* Category Node */}
              <div 
                className="card w-72 bg-base-100 shadow-xl border-2 border-accent/20 hover:border-accent hover:shadow-accent/20 transition-all cursor-context-menu relative z-10 group"
                onContextMenu={(e) => handleContextMenu(e, 'category', cat.name)}
              >
                <div className="card-body p-6 items-center">
                  <div className="p-3 bg-accent/10 rounded-full mb-2 group-hover:scale-110 transition-transform duration-300">
                    <Layers className="w-10 h-10 text-accent" />
                  </div>
                  <h3 className="card-title text-2xl tracking-wide">{cat.name}</h3>
                  <p className="text-sm opacity-60 text-center mt-2 leading-relaxed">{cat.description || 'Category'}</p>
                </div>
              </div>

              {/* Operations Stem */}
              <div className="w-1 h-12 bg-base-content/20" />
              
              {/* Operations List */}
              <div className="flex flex-col gap-8 w-full relative items-center">
                {cat.operations.map((op) => (
                  <div key={op.name} className="flex flex-col items-center relative w-[90%]">
                    
                    <div className="absolute left-1/2 -top-8 w-1 h-8 bg-base-content/20" />
                    
                    <div 
                      className="card w-full bg-base-100 shadow-md border border-secondary/30 hover:border-secondary hover:shadow-secondary/20 transition-all group z-10 cursor-context-menu"
                      onContextMenu={(e) => handleContextMenu(e, 'operation', cat.name, op.name)}
                    >
                      <div className="card-body p-4">
                        <div className="flex items-center gap-3 font-bold text-lg border-b border-base-200 pb-2">
                          <Activity className="w-5 h-5 text-secondary group-hover:text-primary transition-colors" />
                          <span className="truncate">{op.name}</span>
                        </div>
                        
                        <div className="flex flex-col gap-3 mt-3">
                            {op.required_fields.length > 0 && (
                                <div className="flex flex-col gap-1.5">
                                  <span className="text-[11px] uppercase tracking-wider font-extrabold text-error flex items-center gap-1">
                                      <span className="w-1.5 h-1.5 rounded-full bg-error animate-pulse"/> Required
                                  </span>
                                  <div className="flex flex-wrap gap-1.5">
                                    {op.required_fields.map(f => (
                                      <span key={f} className="badge badge-outline badge-error font-mono text-xs py-2 shadow-sm">
                                        <Hash className="w-3 h-3 mr-1 opacity-70" />{f}
                                      </span>
                                    ))}
                                  </div>
                                </div>
                            )}
                            
                            {op.dependencies && op.dependencies.length > 0 && (
                                <div className="mt-2 p-3 bg-info/5 text-info rounded-lg text-sm border border-info/20 shadow-inner">
                                  <div className="font-extrabold flex gap-2 items-center mb-1.5 text-xs uppercase tracking-wider opacity-80">
                                    <Network className="w-3.5 h-3.5" /> Logical Dependency
                                  </div>
                                  <div className="font-mono tracking-tight leading-snug">
                                      {op.dependencies[0].condition}
                                  </div>
                                </div>
                            )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Context Menu Overlay rendering */}
      {contextMenu && (
        <ul 
          className="menu bg-base-100 w-56 rounded-box shadow-xl border border-secondary/50 fixed z-100"
          style={{ top: contextMenu.y, left: contextMenu.x }}
        >
          <li className="menu-title text-[10px] opacity-70 px-4 py-2 uppercase tracking-widest border-b border-base-200 mb-1">
             {contextMenu.type === 'category' ? `Category: ${contextMenu.cat}` : `Operation: ${contextMenu.op}`}
          </li>
          <li>
            <a onClick={() => {
              if (contextMenu.type === 'category' && onNavigateCategory) {
                onNavigateCategory(contextMenu.cat);
              } else if (contextMenu.type === 'operation' && onNavigateOperation && contextMenu.op) {
                onNavigateOperation(contextMenu.cat, contextMenu.op);
              }
            }}>
              Go To Object Schema
            </a>
          </li>
        </ul>
      )}
    </div>
  );
};

export default IntentGraphCanvas;
