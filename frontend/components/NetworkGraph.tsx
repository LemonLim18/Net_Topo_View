import React, { useRef, useEffect, useCallback, useState } from 'react';
import * as d3 from 'd3';
import type { D3Node, D3Link } from '../types';
import RouterIcon from './icons/RouterIcon';
import ServerIcon from './icons/ServerIcon';
import { CONTROLLER_NODE_ID } from '../constants';

const POSITIONS_STORAGE_KEY = 'nettopo_node_positions';
const TRANSFORM_STORAGE_KEY = 'nettopo_zoom_transform';

type SavedPosition = { x: number; y: number; fx: number | null; fy: number | null };
type SavedPositions = Record<string, SavedPosition>;

function loadSavedPositions(): SavedPositions {
  try {
    const raw = localStorage.getItem(POSITIONS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}

function savePosToStorage(positions: Map<string, { x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null }>) {
  const obj: SavedPositions = loadSavedPositions();
  positions.forEach((v, k) => {
    if (typeof v.x === 'number' && typeof v.y === 'number') {
      obj[k] = { x: v.x, y: v.y, fx: v.fx ?? null, fy: v.fy ?? null };
    }
  });
  try { localStorage.setItem(POSITIONS_STORAGE_KEY, JSON.stringify(obj)); } catch { /* quota */ }
}

function loadSavedTransform(): d3.ZoomTransform | null {
  try {
    const raw = localStorage.getItem(TRANSFORM_STORAGE_KEY);
    if (!raw) return null;
    const { k, x, y } = JSON.parse(raw);
    return d3.zoomIdentity.translate(x, y).scale(k);
  } catch { return null; }
}

function saveTransformToStorage(t: d3.ZoomTransform) {
  try { localStorage.setItem(TRANSFORM_STORAGE_KEY, JSON.stringify({ k: t.k, x: t.x, y: t.y })); } catch { /* quota */ }
}

interface NetworkGraphProps {
  nodes: D3Node[];
  links: D3Link[];
  onNodeClick: (nodeId: string) => void;
  onNodeContextMenu?: (nodeId: string, x: number, y: number) => void;
  selectedNodeId: string | null;
  isGridVisible: boolean;
  setIsGridVisible: (visible: boolean) => void;
  theme: string;
  topologyView?: 'actual' | 'planned' | 'diff';
}

const NetworkGraph: React.FC<NetworkGraphProps> = ({ nodes, links, onNodeClick, onNodeContextMenu, selectedNodeId, isGridVisible, setIsGridVisible, theme, topologyView = 'actual' }) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const simulationRef = useRef<d3.Simulation<D3Node, D3Link> | null>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const positionsRef = useRef<Map<string, { x?: number; y?: number; vx?: number; vy?: number; fx?: number | null; fy?: number | null }>>(new Map());
  const graphSignatureRef = useRef<string>('');
  const hasInitializedLayoutRef = useRef(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [, forceRender] = useState(0);
  
  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;
    const container = containerRef.current;
    const svg = d3.select(svgRef.current);

    // Seed positionsRef from localStorage on first mount
    if (positionsRef.current.size === 0) {
      const saved = loadSavedPositions();
      for (const [id, pos] of Object.entries(saved)) {
        positionsRef.current.set(id, { x: pos.x, y: pos.y, vx: 0, vy: 0, fx: pos.fx, fy: pos.fy });
      }
    }

    const observer = new ResizeObserver(([entry]) => {
      window.requestAnimationFrame(() => {
        if (!entry) return;
        const { width, height } = entry.contentRect;
        svg.attr('width', width).attr('height', height);
        if (simulationRef.current) {
          simulationRef.current.force("center", d3.forceCenter(width / 2, height / 2));
          simulationRef.current.stop();
        }
      });
    });

    observer.observe(container);
    return () => observer.unobserve(container);
  }, []);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;
    const svg = d3.select(svgRef.current);
    const container = containerRef.current;
    const { width, height } = container.getBoundingClientRect();

    const g = svg.selectAll<SVGGElement, unknown>('g.zoom-container').data([null]).join('g').attr('class', 'zoom-container');

    if (!zoomRef.current) {
      const zoom = d3.zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.5, 3])
        .wheelDelta(event => -event.deltaY * (event.deltaMode === 1 ? 0.05 : event.deltaMode ? 1 : 0.002) * 0.4)
        .on('zoom', (event) => {
          g.attr('transform', event.transform);
          saveTransformToStorage(event.transform);
        });
      
      svg.call(zoom).on("dblclick.zoom", null);
      zoomRef.current = zoom;

      // Restore saved zoom/pan transform
      const savedTransform = loadSavedTransform();
      if (savedTransform) {
        svg.call(zoom.transform, savedTransform);
      }
    }

    if (!simulationRef.current) {
      simulationRef.current = d3.forceSimulation<D3Node, D3Link>()
        .force("charge", d3.forceManyBody().strength(-1200)) // Stronger repulsion to avoid clumping
        .force("collide", d3.forceCollide().radius(70)) // Increase breathing room between nodes
        .force("link", d3.forceLink<D3Node, D3Link>().id(d => d.id).distance(250).strength(0.3)) // Looser links
        .force("x", d3.forceX(width / 2).strength(0.05)) // Gentle centering force
        .force("y", d3.forceY(height / 2).strength(0.05))
        .velocityDecay(0.7); // Higher friction keeps the graph stable during interaction
    }
    const simulation = simulationRef.current;

    const linkKey = (link: D3Link) => {
      const source = link.source as D3Node | string;
      const target = link.target as D3Node | string;
      const sourceId = typeof source === 'string' ? source : source.id;
      const targetId = typeof target === 'string' ? target : target.id;
      return `${sourceId}->${targetId}`;
    };

    const nodeIds = nodes.map(n => n.id).sort();
    const linkIds = links.map(linkKey).sort();
    const graphSignature = JSON.stringify({ nodeIds, linkIds });
    const topologyShapeChanged = graphSignatureRef.current !== graphSignature;
    graphSignatureRef.current = graphSignature;

    const unsavedNodes = nodes.filter(n => !positionsRef.current.has(n.id));
    const unsavedIndex = new Map(unsavedNodes.map((n, i) => [n.id, i]));
    const unsavedCount = Math.max(unsavedNodes.length, 1);

    // Reapply saved positions first. For brand-new nodes, place them in a
    // deterministic ring so they do not trigger a force-layout reshuffle.
    nodes.forEach(n => {
      const prev = positionsRef.current.get(n.id);
      if (prev) {
        if (typeof prev.x === 'number') n.x = prev.x;
        if (typeof prev.y === 'number') n.y = prev.y;
        if (typeof prev.vx === 'number') n.vx = prev.vx;
        if (typeof prev.vy === 'number') n.vy = prev.vy;
        if (prev.fx !== undefined) n.fx = prev.fx as any;
        if (prev.fy !== undefined) n.fy = prev.fy as any;
      } else if (n.x === undefined || n.y === undefined) {
        const index = unsavedIndex.get(n.id) ?? 0;
        const radius = Math.max(140, Math.min(width, height) * 0.34);
        const angle = (-Math.PI / 2) + ((2 * Math.PI * index) / unsavedCount);
        n.x = (width / 2) + radius * Math.cos(angle);
        n.y = (height / 2) + radius * Math.sin(angle);
        n.fx = n.x;
        n.fy = n.y;
      }
    });

    const nodeById = new Map(nodes.map(n => [n.id, n] as const));

    simulation
        .nodes(nodes)
        .force("center", d3.forceCenter(width / 2, height / 2));
    
    (simulation.force('link') as d3.ForceLink<D3Node, D3Link>).links(links);

    // ---------------------- GRID (visual lines that pan/zoom with graph) -----------------------
    const gridSpacing = 30; // distance between grid lines in px
    const gridPaddingFactor = 4; // how much bigger than viewport to draw the grid

    const gridGroup = g.selectAll<SVGGElement, unknown>('g.grid').data([null]).join('g').attr('class', 'grid');
    gridGroup.lower();

    function renderGrid(w: number, h: number, spacing = gridSpacing) {
      gridGroup.selectAll('*').remove();
      
      const baseColor = getComputedStyle(container).getPropertyValue('color');
      const d3Color = d3.color(baseColor);
      let gridLineColor = 'rgba(128, 128, 128, 0.1)';
      let centerAxisColor = 'rgba(128, 128, 128, 0.2)';
      if (d3Color) {
        d3Color.opacity = 0.1;
        gridLineColor = d3Color.toString();
        d3Color.opacity = 0.2;
        centerAxisColor = d3Color.toString();
      }

      const extent = Math.max(w, h) * gridPaddingFactor;
      const half = extent / 2;
      const startX = -half;
      const startY = -half;
      const cols = Math.ceil(extent / spacing);
      const rows = Math.ceil(extent / spacing);

      for (let i = 0; i <= cols; i++) {
        const x = startX + i * spacing;
        gridGroup.append('line')
          .attr('x1', x).attr('y1', startY).attr('x2', x).attr('y2', startY + extent)
          .attr('stroke', gridLineColor).attr('stroke-width', 1)
          .attr('shape-rendering', 'crispEdges').attr('pointer-events', 'none');
      }

      for (let j = 0; j <= rows; j++) {
        const y = startY + j * spacing;
        gridGroup.append('line')
          .attr('x1', startX).attr('y1', y).attr('x2', startX + extent).attr('y2', y)
          .attr('stroke', gridLineColor).attr('stroke-width', 1)
          .attr('shape-rendering', 'crispEdges').attr('pointer-events', 'none');
      }

      gridGroup.append('line')
        .attr('x1', -half).attr('y1', 0).attr('x2', half).attr('y2', 0)
        .attr('stroke', centerAxisColor).attr('stroke-width', 1.2)
        .attr('pointer-events', 'none');

      gridGroup.append('line')
        .attr('x1', 0).attr('y1', -half).attr('x2', 0).attr('y2', half)
        .attr('stroke', centerAxisColor).attr('stroke-width', 1.2)
        .attr('pointer-events', 'none');

      gridGroup.lower();
    }

    renderGrid(width, height);

    const gridResizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const { width: newW, height: newH } = entry.contentRect;
      renderGrid(newW, newH);
    });
    gridResizeObserver.observe(container);
    // -------------------------------------------------------------------------------------------

    // Debounced save of node positions to localStorage
    const scheduleSave = () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      saveTimerRef.current = setTimeout(() => savePosToStorage(positionsRef.current), 300);
    };

    const linkGroup = g.selectAll<SVGGElement, unknown>('g.links').data([null]).join('g').attr('class', 'links');
    const nodeGroup = g.selectAll<SVGGElement, unknown>('g.nodes').data([null]).join('g').attr('class', 'nodes');

    const linkG = linkGroup
      .selectAll<SVGGElement, D3Link>('g.link-group')
      .data(links, d => {
        const source = d.source as D3Node | string;
        const target = d.target as D3Node | string;
        const sourceId = typeof source === 'string' ? source : source.id;
        const targetId = typeof target === 'string' ? target : target.id;
        return `${sourceId}-${targetId}`;
      })
      .join(enter => {
          const g = enter.append('g').attr('class', 'link-group');
          // line color indicates diff status only when in Diff view
          g.append('line')
            .attr('stroke-width', 2)
            .attr('class', (d: any) => {
              if (topologyView === 'diff') {
                const status = d.diffStatus || 'unchanged';
                if (status === 'added') return 'stroke-warning opacity-90';
                if (status === 'removed') return 'stroke-error opacity-90';
              }
              return 'stroke-current text-base-content opacity-80';
            });
          
          const sourceLabel = g.append('text').attr('class', 'source-label fill-current text-base-content text-[10px] font-mono select-none');
          sourceLabel.append('tspan').attr('class', 'source-interface font-semibold').attr('x', 0).attr('dy', 0);
          sourceLabel.append('tspan').attr('class', 'source-ip text-[9px] opacity-80').attr('x', 0).attr('dy', '0.85em');
              
          const targetLabel = g.append('text').attr('class', 'target-label fill-current text-base-content text-[10px] font-mono select-none');
          targetLabel.append('tspan').attr('class', 'target-interface font-semibold').attr('x', 0).attr('dy', 0);
          targetLabel.append('tspan').attr('class', 'target-ip text-[9px] opacity-80').attr('x', 0).attr('dy', '0.85em');
          return g;
      });

    const hasIpLabel = (ip: unknown): boolean => String(ip ?? '').trim().length > 0;

    // Update labels and ensure link stroke classes reflect diffStatus for existing links
    linkG.select('.source-interface').text(d => d.sourceInterface);
    linkG
      .select('.source-ip')
      .text(d => (hasIpLabel(d.sourceIp) ? String(d.sourceIp) : ''))
      .attr('dy', d => (hasIpLabel(d.sourceIp) ? '0.85em' : '0'))
      .style('display', d => (hasIpLabel(d.sourceIp) ? null : 'none'))
      .attr('class', 'source-ip text-[9px] opacity-80');
    linkG.select('.target-interface').text(d => d.targetInterface);
    linkG
      .select('.target-ip')
      .text(d => (hasIpLabel(d.targetIp) ? String(d.targetIp) : ''))
      .attr('dy', d => (hasIpLabel(d.targetIp) ? '0.85em' : '0'))
      .style('display', d => (hasIpLabel(d.targetIp) ? null : 'none'))
      .attr('class', 'target-ip text-[9px] opacity-80');
    linkG.select('line').attr('class', (d: any) => {
      if (topologyView === 'diff') {
        const status = d.diffStatus || 'unchanged';
        if (status === 'added') return 'stroke-warning opacity-90';
        if (status === 'removed') return 'stroke-error opacity-90';
      }
      return 'stroke-current text-base-content opacity-80';
    });

    const node = nodeGroup
      .selectAll<SVGGElement, D3Node>('g')
      .data(nodes, d => d.id)
      .join(
        enter => {
          const g = enter.append('g')
            .attr('class', 'cursor-pointer')
            .on('click', (_event, d) => onNodeClick(d.id))
            .on('contextmenu', (event, d) => {
              event.preventDefault();
              event.stopPropagation();
              if (onNodeContextMenu) {
                onNodeContextMenu(d.id, event.clientX, event.clientY);
              }
            });

          // node circle: change stroke color based on diffStatus, but only in Diff view
          g.append('circle')
            .attr('r', 30)
            .attr('class', (d: any) => {
              if (topologyView === 'diff') {
                const status = d.diffStatus || 'unchanged';
                if (status === 'added') return 'stroke-2 stroke-warning transition-all duration-300';
                if (status === 'removed') return 'stroke-2 stroke-error transition-all duration-300';
              }
              return 'stroke-2 stroke-current transition-all duration-300';
            })
            .style('fill', 'hsl(var(--b1))');

          const iconSize = 30;
          g.append('svg:foreignObject').attr('width', iconSize).attr('height', iconSize).attr('x', -iconSize / 2).attr('y', -iconSize / 2)
            .append('xhtml:div').attr('class', 'w-[30px] h-[30px]').html(d => d.id === CONTROLLER_NODE_ID ? ServerIcon : RouterIcon);

          g.append('text').attr('dy', '3.1em').attr('text-anchor', 'middle').attr('class', 'fill-current text-base-content text-sm font-semibold select-none').text(d => d.id);
          
          g.append('text').attr('dy', '-2.5em').attr('text-anchor', 'middle').attr('class', 'fill-current text-base-content/70 text-xs font-mono select-none').text(d => d.mgmt_ip);

          g.append('title').text(d => `Mgmt IP: ${d.mgmt_ip}`);
          return g;
        }
      );

    const dragBehavior = d3.drag<SVGGElement, D3Node>()
      .on('start', (_event, d) => {
        d.fx = d.x;
        d.fy = d.y;
      })
      .on('drag', (event, d) => {
        d.x = event.x;
        d.y = event.y;
        d.fx = event.x;
        d.fy = event.y;
        positionsRef.current.set(d.id, { x: d.x, y: d.y, vx: 0, vy: 0, fx: d.fx, fy: d.fy });
        renderPositions();
      })
      .on('end', (event, d) => {
        d.x = event.x;
        d.y = event.y;
        d.fx = event.x;
        d.fy = event.y;
        positionsRef.current.set(d.id, { x: d.x!, y: d.y!, vx: 0, vy: 0, fx: d.fx, fy: d.fy });
        renderPositions();
        scheduleSave();
      });

    node.call(dragBehavior);

    // Ensure existing node circles update their stroke classes when topologyView
    // or diffStatus changes (not only on enter). This makes the ring around
    // nodes reflect added/removed colors in Diff view.
    node.select('circle').attr('class', (d: any) => {
      if (topologyView === 'diff') {
        const status = d.diffStatus || 'unchanged';
        if (status === 'added') return 'stroke-2 stroke-warning transition-all duration-300';
        if (status === 'removed') return 'stroke-2 stroke-error transition-all duration-300';
      }
      return 'stroke-2 stroke-current transition-all duration-300';
    });

    const renderPositions = () => {
      // Pre-calculate incidence counts and angles to detect overlaps
      const nodeIncidence = new Map<string, { angle: number; linkIndex: number; isSource: boolean }[]>();
      links.forEach((l, idx) => {
        const sId = typeof l.source === 'string' ? l.source : (l.source as D3Node).id;
        const tId = typeof l.target === 'string' ? l.target : (l.target as D3Node).id;
        const s = nodeById.get(sId);
        const t = nodeById.get(tId);
        if (s && t && typeof s.x === 'number' && typeof t.x === 'number') {
          const angle = Math.atan2(t.y! - s.y!, t.x! - s.x!);
          if (!nodeIncidence.has(sId)) nodeIncidence.set(sId, []);
          nodeIncidence.get(sId)!.push({ angle, linkIndex: idx, isSource: true });
          if (!nodeIncidence.has(tId)) nodeIncidence.set(tId, []);
          nodeIncidence.get(tId)!.push({ angle: angle + Math.PI, linkIndex: idx, isSource: false });
        }
      });

      // Sort and group by angle for each node to apply staggers
      const staggers = new Map<string, number>(); // key: "linkIndex-isSource", value: radial stagger index

      nodeIncidence.forEach((list) => {
        list.sort((a, b) => a.angle - b.angle);
        let currentStagger = 0;
        for (let j = 0; j < list.length; j++) {
          if (j > 0) {
            let diff = Math.abs(list[j].angle - list[j - 1].angle);
            if (diff > Math.PI) diff = 2 * Math.PI - diff;
            if (diff < 0.4) { // ~23°: nearby links get staggered to avoid overlap
              currentStagger++;
            } else {
              currentStagger = 0;
            }
          }
          staggers.set(`${list[j].linkIndex}-${list[j].isSource}`, currentStagger);
        }
      });

      linkG.each(function(d, i) {
        const sourceRef = d.source as D3Node | string;
        const targetRef = d.target as D3Node | string;
        const source = typeof sourceRef === 'string' ? nodeById.get(sourceRef) : (sourceRef as D3Node);
        const target = typeof targetRef === 'string' ? nodeById.get(targetRef) : (targetRef as D3Node);
        if (!source || !target) return;
        if (typeof source.x !== 'number' || typeof source.y !== 'number' || typeof target.x !== 'number' || typeof target.y !== 'number') return;

        const el = d3.select(this);
        el.select('line').attr('x1', source.x).attr('y1', source.y).attr('x2', target.x).attr('y2', target.y);

        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const len = Math.hypot(dx, dy) || 1;
        const angle = Math.atan2(dy, dx);
        const perpX = -dy / len;
        const perpY = dx / len;

        // Place labels along the link (not at the node) to avoid pile-up: 28% from each endpoint.
        const alongFrac = 0.28;
        const basePerp = 18;
        const minAlong = 40; // minimum distance from node so label stays off the circle
        const alongSource = Math.max(minAlong, len * alongFrac);
        const alongTarget = Math.max(minAlong, len * alongFrac);

        const processLabel = (isSource: boolean) => {
          const stagger = staggers.get(`${i}-${isSource}`) || 0;
          const side = isSource ? 1 : -1;
          const perp = (basePerp + stagger * 14) * side;

          const lx = isSource
            ? source.x! + (dx / len) * alongSource + perpX * perp
            : target.x! - (dx / len) * alongTarget + perpX * perp;
          const ly = isSource
            ? source.y! + (dy / len) * alongSource + perpY * perp
            : target.y! - (dy / len) * alongTarget + perpY * perp;

          const lAngle = isSource ? angle : angle + Math.PI;
          const cos = Math.cos(lAngle);
          let anchor: string = 'middle';
          if (cos > 0.35) anchor = 'start';
          else if (cos < -0.35) anchor = 'end';

          el.select(isSource ? '.source-label' : '.target-label')
            .attr('transform', `translate(${lx}, ${ly})`)
            .attr('text-anchor', anchor);
        };

        processLabel(true);
        processLabel(false);
      });

      node.attr('transform', d => `translate(${d.x},${d.y})`);
    };

    simulation.on('tick', () => {
      renderPositions();

      const simNodes = simulation.nodes();
      simNodes.forEach(n => {
        positionsRef.current.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy, fx: (n as any).fx ?? null, fy: (n as any).fy ?? null });
      });
    });

    renderPositions();

    if (!hasInitializedLayoutRef.current) {
      hasInitializedLayoutRef.current = true;
      nodes.forEach(n => {
        if (typeof n.x === 'number' && typeof n.y === 'number') {
          if (n.fx == null) n.fx = n.x;
          if (n.fy == null) n.fy = n.y;
          positionsRef.current.set(n.id, { x: n.x, y: n.y, vx: 0, vy: 0, fx: n.fx, fy: n.fy });
        }
      });
      simulation.alpha(0).stop();
      savePosToStorage(positionsRef.current);
    } else if (topologyShapeChanged) {
      nodes.forEach(n => {
        if (typeof n.x === 'number' && typeof n.y === 'number') {
          if (n.fx == null) n.fx = n.x;
          if (n.fy == null) n.fy = n.y;
          positionsRef.current.set(n.id, { x: n.x, y: n.y, vx: 0, vy: 0, fx: n.fx, fy: n.fy });
        }
      });
      simulation.alpha(0).stop();
      savePosToStorage(positionsRef.current);
    } else {
      simulation.alpha(0);
      simulation.stop();
    }

    return () => {
      try {
        gridResizeObserver.disconnect();
      } catch (e) { /* ignore */ }
    };
  }, [nodes, links, onNodeClick, theme, topologyView]);

  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.select<SVGGElement>('g.grid').style('display', isGridVisible ? '' : 'none');
  }, [isGridVisible]);

  useEffect(() => {
    if (!svgRef.current) return;
    const nodeSelection = d3.select(svgRef.current).selectAll<SVGGElement, D3Node>('g.nodes g');
    
    // Keep selected highlighting while preserving diff-based stroke classes
    nodeSelection.select('circle').each(function(d: any) {
      const circle = d3.select(this);
      const isSelected = d.id === selectedNodeId;
      if (isSelected) {
        circle.attr('stroke', 'hsl(var(--p))');
      } else {
        // If not selected, let the class-driven stroke color remain; remove inline stroke
        circle.attr('stroke', null);
      }
    });
    
    nodeSelection.select('foreignObject div').each(function(d: any) {
      const div = d3.select(this);
      const isSelected = d.id === selectedNodeId;

      // Priority: selected > diffStatus coloring (only in Diff view) > default accent
      if (isSelected) {
        div.classed('text-primary', true).classed('text-accent text-warning text-error', false);
      } else if (topologyView === 'diff') {
        const status = d.diffStatus || 'unchanged';
        if (status === 'added') {
          div.classed('text-warning', true).classed('text-accent text-primary text-error', false);
        } else if (status === 'removed') {
          div.classed('text-error', true).classed('text-accent text-primary text-warning', false);
        } else {
          div.classed('text-accent', true).classed('text-primary text-warning text-error', false);
        }
      } else {
        div.classed('text-accent', true).classed('text-primary text-warning text-error', false);
      }
    });

  }, [selectedNodeId, theme, nodes, topologyView]);

  const handleZoomIn = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current)
      .transition()
      .duration(250)
      .call(zoomRef.current.scaleBy, 1.2);
  }, []);

  const handleZoomOut = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current)
      .transition()
      .duration(250)
      .call(zoomRef.current.scaleBy, 0.8);
  }, []);

  const handleResetLayout = useCallback(() => {
    localStorage.removeItem(POSITIONS_STORAGE_KEY);
    localStorage.removeItem(TRANSFORM_STORAGE_KEY);
    positionsRef.current.clear();
    hasInitializedLayoutRef.current = false;
    graphSignatureRef.current = '';

    if (simulationRef.current) {
      simulationRef.current.nodes().forEach(n => { n.fx = null; n.fy = null; (n as any).x = undefined; (n as any).y = undefined; });
    }

    // Reset zoom to identity
    if (svgRef.current && zoomRef.current) {
      d3.select(svgRef.current).call(zoomRef.current.transform, d3.zoomIdentity);
    }

    forceRender(c => c + 1);
  }, []);

  return (
    <div 
      ref={containerRef} 
      className="w-full h-full bg-base-200 shadow-inner overflow-hidden cursor-move relative"
    >
      <svg ref={svgRef}></svg>
      <div className="absolute top-4 left-4 z-10 btn-group btn-group-vertical shadow-md">
        <button onClick={handleZoomIn} className="btn btn-sm" aria-label="Zoom in">
           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" /></svg>
        </button>
        <button onClick={handleZoomOut} className="btn btn-sm" aria-label="Zoom out">
           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M18 12H6" /></svg>
        </button>
        <button 
          onClick={() => setIsGridVisible(!isGridVisible)} 
          className={`btn btn-sm ${isGridVisible ? 'btn-active' : ''}`} 
          aria-label="Toggle grid"
          title="Toggle Grid"
        >
           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 6a2 2 0 012-2h12a2 2 0 012 2v12a2 2 0 01-2 2H6a2 2 0 01-2-2V6z" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 12h16" />
           </svg>
        </button>
        {/* Reset Layout Button */}
        {/* <button
          onClick={handleResetLayout}
          className="btn btn-sm"
          aria-label="Reset layout"
          title="Reset Layout (re-run auto-layout)"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button> */}
      </div>
    </div>
  );
};

export default NetworkGraph;
