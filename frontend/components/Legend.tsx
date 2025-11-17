
import React from 'react';
import RouterIcon from './icons/RouterIcon';
import ServerIcon from './icons/ServerIcon';

const Legend: React.FC = () => {
  return (
    <div className="absolute top-4 right-4 card bg-base-100/80 backdrop-blur-sm shadow-lg p-3 text-xs text-base-content w-48 z-1">
      <div className="card-body p-2">
        <h3 className="font-bold mb-2">Legend</h3>
        <ul className="space-y-2">
          <li className="flex items-center">
            <div className="w-8 h-8 mr-2 flex-shrink-0 flex items-center justify-center bg-base-300 rounded-full">
                <div className="w-5 h-5" dangerouslySetInnerHTML={{ __html: ServerIcon }} />
            </div>
            <span>Controller</span>
          </li>
          <li className="flex items-center">
            <div className="w-8 h-8 mr-2 flex-shrink-0 flex items-center justify-center bg-base-300 rounded-full">
                <div className="w-4 h-4" dangerouslySetInnerHTML={{ __html: RouterIcon }} />
            </div>
            <span>Router Node</span>
          </li>
          <li className="flex items-center">
             <div className="w-8 h-8 mr-2 flex-shrink-0 flex items-center justify-center">
                <svg width="24" height="2" viewBox="0 0 24 2" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <line x1="1" y1="1" x2="23" y2="1" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                </svg>
            </div>
            <span>Physical Link</span>
          </li>
        </ul>
      </div>
    </div>
  );
};

export default Legend;