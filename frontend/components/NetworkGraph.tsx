import React, { useRef, useEffect, useCallback } from 'react';
import * as d3 from 'd3';
import type { D3Node, D3Link } from '../types';
import RouterIcon from './icons/RouterIcon';

interface NetworkGraphProps {
  nodes: D3Node[];
  links: D3Link[];
  onNodeClick: (nodeId: string) => void;
  selectedNodeId: string | null;
  isGridVisible: boolean;
  setIsGridVisible: (visible: boolean) => void;
  theme: string;
}

const NetworkGraph: React.FC<NetworkGraphProps> = ({ nodes, links, onNodeClick, selectedNodeId, isGridVisible, setIsGridVisible, theme }) => {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const simulationRef = useRef<d3.Simulation<D3Node, D3Link> | null>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  
  useEffect(() => {
    if (!containerRef.current || !svgRef.current) return;
    const container = containerRef.current;
    const svg = d3.select(svgRef.current);

    const observer = new ResizeObserver(([entry]) => {
      window.requestAnimationFrame(() => {
        if (!entry) return;
        const { width, height } = entry.contentRect;
        svg.attr('width', width).attr('height', height);
        
        if (simulationRef.current) {
          simulationRef.current.force("center", d3.forceCenter(width / 2, height / 2));
          simulationRef.current.alpha(0.3).restart();
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
        });
      
      svg.call(zoom).on("dblclick.zoom", null);
      zoomRef.current = zoom;
    }

    if (!simulationRef.current) {
      simulationRef.current = d3.forceSimulation<D3Node, D3Link>()
        .force("charge", d3.forceManyBody().strength(-900))
        .force("collide", d3.forceCollide().radius(50))
        .force("link", d3.forceLink<D3Node, D3Link>().id(d => d.id).distance(250).strength(0.6));
    }
    const simulation = simulationRef.current;

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
          g.append('line').attr('class', 'stroke-current text-base-content opacity-80').attr('stroke-width', 2);
          
          const sourceLabel = g.append('text').attr('class', 'source-label fill-current text-base-content text-[10px] font-mono select-none');
          sourceLabel.append('tspan').attr('class', 'source-interface font-semibold').attr('x', 0).attr('dy', 0);
          sourceLabel.append('tspan').attr('class', 'source-ip').attr('x', 0).attr('dy', '1.1em');
              
          const targetLabel = g.append('text').attr('class', 'target-label fill-current text-base-content text-[10px] font-mono select-none');
          targetLabel.append('tspan').attr('class', 'target-interface font-semibold').attr('x', 0).attr('dy', 0);
          targetLabel.append('tspan').attr('class', 'target-ip').attr('x', 0).attr('dy', '1.1em');
          return g;
      });

    linkG.select('.source-interface').text(d => d.sourceInterface);
    linkG.select('.source-ip').text(d => d.sourceIp);
    linkG.select('.target-interface').text(d => d.targetInterface);
    linkG.select('.target-ip').text(d => d.targetIp);

    const node = nodeGroup
      .selectAll<SVGGElement, D3Node>('g')
      .data(nodes, d => d.id)
      .join(
        enter => {
          const g = enter.append('g')
            .attr('class', 'cursor-pointer')
            .on('click', (_event, d) => onNodeClick(d.id))
            .call(d3.drag<SVGGElement, D3Node>()
              .on('start', (event, d) => {
                if (!event.active) simulation.alphaTarget(0.3).restart();
                d.fx = d.x;
                d.fy = d.y;
              })
              .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
              .on('end', (event, d) => {
                if (!event.active) simulation.alphaTarget(0);
                d.fx = null;
                d.fy = null;
              }));

          g.append('circle').attr('r', 30).attr('class', 'stroke-2 transition-all duration-300').style('fill', 'hsl(var(--b1))');

          const iconSize = 30;
          g.append('svg:foreignObject').attr('width', iconSize).attr('height', iconSize).attr('x', -iconSize / 2).attr('y', -iconSize / 2)
            .append('xhtml:div').attr('class', 'w-[30px] h-[30px]').html(RouterIcon);

          g.append('text').attr('dy', '3.1em').attr('text-anchor', 'middle').attr('class', 'fill-current text-base-content text-sm font-semibold select-none').text(d => d.id);
          
          g.append('text').attr('dy', '-2.5em').attr('text-anchor', 'middle').attr('class', 'fill-current text-base-content/70 text-xs font-mono select-none').text(d => d.mgmt_ip);

          g.append('title').text(d => `Mgmt IP: ${d.mgmt_ip}`);
          return g;
        }
      );

    simulation.on('tick', () => {
      linkG.each(function(d) {
        const source = d.source as D3Node;
        const target = d.target as D3Node;
        if (!source.x || !source.y || !target.x || !target.y) return;
        
        const el = d3.select(this);
        el.select('line').attr('x1', source.x).attr('y1', source.y).attr('x2', target.x).attr('y2', target.y);

        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const angle = Math.atan2(dy, dx);
        
        const nodeRadius = 45; 
        const offset = 15;
        const yThreshold = 30;

        if (source.y > target.y + yThreshold) { 
            el.select('text.source-label')
              .attr('transform', `translate(${source.x}, ${source.y - 65})`)
              .attr('text-anchor', 'middle');
        } else {
            const perpOffset = dx >= 0 ? offset : -offset;
            const sourceLabelX = source.x + nodeRadius * Math.cos(angle) + perpOffset * Math.sin(angle);
            const sourceLabelY = source.y + nodeRadius * Math.sin(angle) - perpOffset * Math.cos(angle);
            el.select('text.source-label')
              .attr('transform', `translate(${sourceLabelX}, ${sourceLabelY})`)
              .attr('text-anchor', dx >= 0 ? 'start' : 'end');
        }

        if (target.y > source.y + yThreshold) {
            el.select('text.target-label')
              .attr('transform', `translate(${target.x}, ${target.y - 65})`)
              .attr('text-anchor', 'middle');
        } else {
            const perpOffset = dx >= 0 ? offset : -offset;
            const targetLabelX = target.x - nodeRadius * Math.cos(angle) + perpOffset * Math.sin(angle);
            const targetLabelY = target.y - nodeRadius * Math.sin(angle) - perpOffset * Math.cos(angle);
            el.select('text.target-label')
              .attr('transform', `translate(${targetLabelX}, ${targetLabelY})`)
              .attr('text-anchor', dx >= 0 ? 'end' : 'start');
        }
      });

      node.attr('transform', d => `translate(${d.x},${d.y})`);
    });

    simulation.alpha(1).restart();

    return () => {
      try {
        gridResizeObserver.disconnect();
      } catch (e) { /* ignore */ }
    };
  }, [nodes, links, onNodeClick, theme]);

  useEffect(() => {
    if (!svgRef.current) return;
    const svg = d3.select(svgRef.current);
    svg.select<SVGGElement>('g.grid').style('display', isGridVisible ? '' : 'none');
  }, [isGridVisible]);

  useEffect(() => {
    if (!svgRef.current) return;
    const nodeSelection = d3.select(svgRef.current).selectAll<SVGGElement, D3Node>('g.nodes g');
    
    nodeSelection.select('circle').attr('stroke', d => d.id === selectedNodeId ? 'hsl(var(--p))' : 'hsl(var(--b3))');
    
    nodeSelection.select('foreignObject div').each(function(d) {
      const div = d3.select(this);
      const isSelected = d.id === selectedNodeId;
      div.classed('text-accent', !isSelected);
      div.classed('text-primary', isSelected);
    });

  }, [selectedNodeId, theme, nodes]);

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
      </div>
    </div>
  );
};

export default NetworkGraph;