import React, { useState } from 'react';
import { 
  Home,
  Network,
  FileCog, 
  BotMessageSquare, 
  Activity, 
  ShieldAlert, 
  ShieldCheck, 
  Siren,
  LogOut,
  ScanSearch,
  Gauge,
  Server,
  BookOpen,
  Workflow,
  Rocket,
  GitCompareArrows,
  PackagePlus
} from 'lucide-react';
import type { ViewId, User } from '../types';
import { Confirm } from '../src/utils/swal';

interface SidebarProps {
  currentView: ViewId;
  setView: (view: ViewId) => void;
  user: User | null;
  onLogout: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentView, setView, user, onLogout }) => {
  const [networkOpen, setNetworkOpen] = useState(true);
  const [securityOpen, setSecurityOpen] = useState(true);

  if (!user) return null;

  const MenuItem = ({ id, label, icon: Icon }: { id: ViewId, label: string, icon: any }) => {
    const isActive = currentView === id;
    return (
      <li>
        <a
          className={
            isActive
              ? 'active bg-primary/20 text-primary font-semibold border-l-2 border-primary'
              : 'hover:bg-base-200'
          }
          onClick={() => setView(id)}
        >
          <Icon className={`h-4 w-4 ${isActive ? 'text-primary' : ''}`} />
          {label}
        </a>
      </li>
    );
  };

  return (
    <aside className="w-64 bg-base-100 flex-shrink-0 shadow-lg z-10 flex flex-col h-full">
       <div className="p-4 border-b border-base-200">
          <div className="flex items-center gap-3">
              <div className="avatar placeholder">
                  <div className="bg-neutral text-neutral-content rounded-full w-10 flex items-center justify-center">
                      <span className="text-lg">{user.username.charAt(0).toUpperCase()}</span>
                  </div>
              </div>
              <div>
                  <div className="font-bold text-sm">{user.username}</div>
                  <div className="text-xs opacity-60 capitalize">{user.role}</div>
              </div>
          </div>
       </div>

      <ul className="menu p-2 w-full flex-grow overflow-y-auto min-h-0 flex-col flex-nowrap">

        {/* Overview — top-level */}
        <MenuItem id="overview" label="Dashboard" icon={Home} />
        
        {/* Operations */}
        <li>
          <details open={networkOpen}>
            <summary onClick={(e) => { e.preventDefault(); setNetworkOpen(!networkOpen); }}>
              <Activity className="h-4 w-4 text-primary" />
              Operations
            </summary>
            <ul>
                <MenuItem id="onboarding" label="Onboard Device" icon={PackagePlus} />
                <MenuItem id="topology" label="Topology Map" icon={Network} />
                <MenuItem id="netbox_devices" label="Device Inventory" icon={Server} />
                <MenuItem id="drift" label="Compliance / Drift" icon={GitCompareArrows} />
                <MenuItem id="net_perf" label="Performance" icon={Activity} />
                <MenuItem id="deployments" label="Deployments" icon={Rocket} />
                <MenuItem id="configuration" label="Configuration" icon={FileCog} />
            </ul>
          </details>
        </li>

        {/* Security */}
        <li>
          <details open={securityOpen} className="mt-2">
            <summary onClick={(e) => { e.preventDefault(); setSecurityOpen(!securityOpen); }}>
              <ShieldCheck className="h-4 w-4 text-secondary" />
              Security
            </summary>
            <ul>
                <MenuItem id="sec_dashboard" label="Security Dashboard" icon={Gauge} />
                <MenuItem id="sec_events" label="SIEM & Events" icon={ShieldAlert} />
                <MenuItem id="sec_analysis" label="Threat Analysis" icon={ScanSearch} />
                <MenuItem id="sec_chat" label="Security Copilot" icon={BotMessageSquare} />
                <MenuItem id="incidents" label="Incident Response" icon={Siren} />
            </ul>
          </details>
        </li>

        {/* Automation & AI */}
        <li>
          <details open className="mt-2">
            <summary>
              <BotMessageSquare className="h-4 w-4 text-accent" />
              Automation & AI
            </summary>
            <ul>
                <MenuItem id="net_chat" label="Network Copilot" icon={BotMessageSquare} />
                <MenuItem id="ansible_studio" label="Ansible Studio" icon={Workflow} />
                <MenuItem id="knowledge_base" label="Knowledge Base" icon={BookOpen} />
            </ul>
          </details>
        </li>
      </ul>

      <div className="p-4 border-t border-base-200">
        <button onClick={async () => { if (await Confirm.ask('Log Out?', 'You will need to sign in again.')) onLogout(); }} className="btn btn-outline btn-error btn-sm w-full gap-2">
            <LogOut className="h-4 w-4" />
            Logout
        </button>
      </div>
    </aside>
  );
};

export default Sidebar;