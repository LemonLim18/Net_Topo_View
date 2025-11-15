import React, { useState, useMemo } from 'react';
import type { D3Link, D3Node } from '../types';

interface TopologyTableProps {
  nodes: D3Node[];
  links: D3Link[];
  onNodeClick: (nodeId: string) => void;
}

type SortKey = 'source' | 'target';

const TopologyTable: React.FC<TopologyTableProps> = ({ links, onNodeClick }) => {
  const [sortKey, setSortKey] = useState<SortKey>('source');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const sortedLinks = useMemo(() => {
    return [...links].sort((a, b) => {
      const aNode = a[sortKey];
      const bNode = b[sortKey];
      
      const aVal = typeof aNode === 'string' ? aNode : (aNode as D3Node).id;
      const bVal = typeof bNode === 'string' ? bNode : (bNode as D3Node).id;

      if (aVal < bVal) return sortOrder === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortOrder === 'asc' ? 1 : -1;
      return 0;
    });
  }, [links, sortKey, sortOrder]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortOrder('asc');
    }
  };

  const renderSortArrow = (key: SortKey) => {
    if (sortKey !== key) return null;
    return sortOrder === 'asc' ? '▲' : '▼';
  };

  return (
    <div className="h-full bg-base-100 p-4 overflow-auto">
        <h3 className="text-lg font-bold mb-2 px-2">Connection List</h3>
        <table className="table table-compact table-zebra w-full">
            <thead>
            <tr>
                <th onClick={() => handleSort('source')} className="cursor-pointer">Source Node {renderSortArrow('source')}</th>
                <th>Source Interface</th>
                <th onClick={() => handleSort('target')} className="cursor-pointer">Target Node {renderSortArrow('target')}</th>
                <th>Target Interface</th>
            </tr>
            </thead>
            <tbody>
            {sortedLinks.map((link, index) => {
                const sourceId = typeof link.source === 'string' ? link.source : (link.source as D3Node).id;
                const targetId = typeof link.target === 'string' ? link.target : (link.target as D3Node).id;
                return (
                <tr key={`${sourceId}-${targetId}-${index}`} className="hover">
                    <td><a onClick={() => onNodeClick(sourceId)} className="link link-hover text-primary">{sourceId}</a></td>
                    <td>{link.sourceInterface}</td>
                    <td><a onClick={() => onNodeClick(targetId)} className="link link-hover text-secondary">{targetId}</a></td>
                    <td>{link.targetInterface}</td>
                </tr>
                );
            })}
            </tbody>
      </table>
    </div>
  );
};

export default TopologyTable;