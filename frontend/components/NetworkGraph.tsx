import React, { useRef, useEffect, useCallback } from 'react';
import * as d3 from 'd3';
import type { D3Node, D3Link } from '../types';
import RouterIcon from './icons/RouterIcon';

interface NetworkGraphProps {
  nodes: D3Node[];
  links: D3Link[];
  onNodeClick: (nodeId: string) => void;
  selectedNodeId: string | null;
}

const NetworkGraph: React.FC<NetworkGraphProps> = ({ nodes, links, onNodeClick, selectedNodeId }) => {
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
    const { width, height } = containerRef.current.getBoundingClientRect();

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

    const linkGroup = g.selectAll<SVGGElement, unknown>('g.links').data([null]).join('g').attr('class', 'links');
    const nodeGroup = g.selectAll<SVGGElement, unknown>('g.nodes').data([null]).join('g').attr('class', 'nodes');

    // Link groups (line + labels)
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
          g.append('line').attr('class', 'stroke-current text-base-content opacity-30').attr('stroke-width', 2);
          
          const sourceLabel = g.append('text').attr('class', 'source-label fill-current text-base-content/80 text-[10px] font-mono select-none');
          sourceLabel.append('tspan').attr('class', 'source-interface font-semibold').attr('x', 0).attr('dy', 0);
          sourceLabel.append('tspan').attr('class', 'source-ip').attr('x', 0).attr('dy', '1.1em');
              
          const targetLabel = g.append('text').attr('class', 'target-label fill-current text-base-content/80 text-[10px] font-mono select-none');
          targetLabel.append('tspan').attr('class', 'target-interface font-semibold').attr('x', 0).attr('dy', 0);
          targetLabel.append('tspan').attr('class', 'target-ip').attr('x', 0).attr('dy', '1.1em');
          return g;
      });

    linkG.select('.source-interface').text(d => d.sourceInterface);
    linkG.select('.source-ip').text(d => d.sourceIp);
    linkG.select('.target-interface').text(d => d.targetInterface);
    linkG.select('.target-ip').text(d => d.targetIp);

    // Node elements
    const node = nodeGroup
      .selectAll<SVGGElement, D3Node>('g')
      .data(nodes, d => d.id)
      .join(
        enter => {
          const g = enter.append('g')
            .attr('class', 'cursor-pointer')
            .on('click', (d) => onNodeClick(d.id))
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
          
          g.append('text').attr('dy', '-2.5em').attr('text-anchor', 'middle').attr('class', 'fill-current text-accent text-xs font-mono select-none').text(d => d.mgmt_ip);

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
        
        const nodeRadius = 45; // Increased from 40 for more space
        const offset = 15;     // Increased from 12 for more space
        const yThreshold = 30;

        // Position source label
        if (source.y > target.y + yThreshold) { // source is clearly lower, place label above node
            el.select('text.source-label')
              .attr('transform', `translate(${source.x}, ${source.y - 65})`)
              .attr('text-anchor', 'middle');
        } else { // source is higher or level, place label along link
            // This logic ensures the label is always placed "outward" from the link, preventing overlaps
            const perpOffset = dx >= 0 ? offset : -offset;
            const sourceLabelX = source.x + nodeRadius * Math.cos(angle) + perpOffset * Math.sin(angle);
            const sourceLabelY = source.y + nodeRadius * Math.sin(angle) - perpOffset * Math.cos(angle);
            el.select('text.source-label')
              .attr('transform', `translate(${sourceLabelX}, ${sourceLabelY})`)
              .attr('text-anchor', dx >= 0 ? 'start' : 'end');
        }

        // Position target label
        if (target.y > source.y + yThreshold) { // target is clearly lower, place label above node
            el.select('text.target-label')
              .attr('transform', `translate(${target.x}, ${target.y - 65})`)
              .attr('text-anchor', 'middle');
        } else { // target is higher or level, place label along link
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

  }, [nodes, links, onNodeClick]);

  useEffect(() => {
    if (!svgRef.current) return;
    const nodeSelection = d3.select(svgRef.current).selectAll<SVGGElement, D3Node>('g.nodes g');
    
    nodeSelection.select('circle').attr('stroke', d => d.id === selectedNodeId ? 'hsl(var(--p))' : 'hsl(var(--n))');
    
    nodeSelection.select('foreignObject div').each(function(d) {
      const div = d3.select(this);
      const isSelected = d.id === selectedNodeId;
      div.classed('text-primary', isSelected);
      div.classed('text-neutral-content', !isSelected);
    });

  }, [selectedNodeId]);

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
      style={{
        backgroundImage: `linear-gradient(hsla(var(--bc)/.1) 1px, transparent 1px), linear-gradient(to right, hsla(var(--bc)/.1) 1px, transparent 1px)`,
        backgroundSize: '30px 30px',
      }}
    >
      <svg ref={svgRef}></svg>
      <div className="absolute top-4 left-4 z-10 btn-group btn-group-vertical shadow-md">
        <button onClick={handleZoomIn} className="btn btn-sm" aria-label="Zoom in">
           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 6v6m0 0v6m0-6h6m-6 0H6" /></svg>
        </button>
        <button onClick={handleZoomOut} className="btn btn-sm" aria-label="Zoom out">
           <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M18 12H6" /></svg>
        </button>
      </div>
    </div>
  );
};

export default NetworkGraph;
