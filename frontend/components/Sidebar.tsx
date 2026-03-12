import React, { useState } from 'react';
import { 
  Home,
  Network,
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
  GitCompareArrows,
  PackagePlus,
  History,
  ListChecks,
  Columns,
  ChevronLeft,
  ChevronRight,
  FileCode2,
} from 'lucide-react';
import type { ViewId, User } from '../types';
import { Confirm } from '../src/utils/swal';

interface SidebarProps {
  currentView: ViewId;
  setView: (view: ViewId) => void;
  user: User | null;
  onLogout: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentView, setView, user, onLogout, collapsed, onToggleCollapse }) => {
  const [networkOpen, setNetworkOpen] = useState(true);
  const [securityOpen, setSecurityOpen] = useState(true);
  const [automationOpen, setAutomationOpen] = useState(true);

  if (!user) return null;

  // ── Menu Item ──
  const MenuItem = ({ id, label, icon: Icon }: { id: ViewId, label: string, icon: any }) => {
    const isActive = currentView === id;
    return (
      <li>
        <a
          className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors cursor-pointer
            ${isActive
              ? 'bg-primary/20 text-primary font-semibold border-l-2 border-primary'
              : 'hover:bg-base-200'
            }
            ${collapsed ? 'justify-center px-2' : ''}
          `}
          onClick={() => setView(id)}
          title={label}
        >
          <Icon className={`h-4 w-4 flex-shrink-0 ${isActive ? 'text-primary' : ''}`} />
          {!collapsed && <span className="whitespace-nowrap overflow-hidden">{label}</span>}
        </a>
      </li>
    );
  };

  // ── Section Header (collapsible group) ──
  const SectionHeader = ({
    label,
    icon: Icon,
    iconColor,
    open,
    onToggle,
    children,
  }: {
    label: string;
    icon: any;
    iconColor: string;
    open: boolean;
    onToggle: () => void;
    children: React.ReactNode;
  }) => {
    if (collapsed) {
      // In collapsed mode, just render items directly (no group header)
      return <>{children}</>;
    }
    return (
      <li>
        <details open={open}>
          <summary
            className="cursor-pointer"
            onClick={(e) => { e.preventDefault(); onToggle(); }}
          >
            <Icon className={`h-4 w-4 ${iconColor}`} />
            {label}
          </summary>
          <ul>{children}</ul>
        </details>
      </li>
    );
  };

  return (
    <aside
      className={`bg-base-100 flex-shrink-0 shadow-lg z-10 flex flex-col h-full transition-all duration-300 ease-in-out
        ${collapsed ? 'w-16' : 'w-64'}
      `}
    >
      {/* User Avatar Section */}
      <div className={`border-b border-base-200 ${collapsed ? 'p-2 flex justify-center' : 'p-4'}`}>
        {collapsed ? (
          <div className="avatar placeholder tooltip tooltip-right" data-tip={user.username}>
            <div className="bg-neutral text-neutral-content rounded-full w-10 flex items-center justify-center">
              <span className="text-lg">{user.username.charAt(0).toUpperCase()}</span>
            </div>
          </div>
        ) : (
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
        )}
      </div>

      {/* Navigation Menu */}
      <ul className={`menu ${collapsed ? 'p-1' : 'p-2'} w-full grow overflow-y-auto min-h-0 flex-col flex-nowrap`}>
        <MenuItem id="overview" label="Dashboard" icon={Home} />

        <SectionHeader
          label="Network"
          icon={Activity}
          iconColor="text-primary"
          open={networkOpen}
          onToggle={() => setNetworkOpen(!networkOpen)}
        >
          <MenuItem id="drift" label="Compliance / Drift" icon={GitCompareArrows} />
          <MenuItem id="netbox_devices" label="Device Inventory" icon={Server} />
          <MenuItem id="onboarding" label="Onboard Device" icon={PackagePlus} />
          <MenuItem id="net_perf" label="Performance" icon={Activity} />
          <MenuItem id="template_studio" label="Template Studio" icon={FileCode2} />
          <MenuItem id="topology" label="Topology Map" icon={Network} />
        </SectionHeader>

        <SectionHeader
          label="Automation & AI"
          icon={BotMessageSquare}
          iconColor="text-accent"
          open={automationOpen}
          onToggle={() => setAutomationOpen(!automationOpen)}
        >
          {/* <MenuItem id="net_chat" label="Network Copilot" icon={BotMessageSquare} /> */}
          <MenuItem id="ansible_studio" label="Ansible Studio" icon={Workflow} />
          <MenuItem id="intent_history" label="Intent History" icon={History} />
          <MenuItem id="playbook_comparison" label="Playbook Compare" icon={Columns} />
          <MenuItem id="approval_queue" label="Approval Queue" icon={ListChecks} />
          <MenuItem id="sdn_dashboard" label="SDN Controllers" icon={Network} />
          <MenuItem id="knowledge_base" label="Knowledge Base" icon={BookOpen} />
        </SectionHeader>
        <SectionHeader
          label="Security"
          icon={ShieldCheck}
          iconColor="text-secondary"
          open={securityOpen}
          onToggle={() => setSecurityOpen(!securityOpen)}
          >
          <MenuItem id="sec_dashboard" label="Security Dashboard" icon={Gauge} />
          <MenuItem id="sec_events" label="SIEM & Events" icon={ShieldAlert} />
          <MenuItem id="sec_analysis" label="Threat Analysis" icon={ScanSearch} />
          <MenuItem id="sec_chat" label="Security Copilot" icon={BotMessageSquare} />
          <MenuItem id="incidents" label="Incident Response" icon={Siren} />
        </SectionHeader>
      </ul>

      {/* Bottom Section: Collapse Toggle + Logout */}
      <div className="border-t border-base-200 p-2 flex flex-col gap-1">
        {/* Collapse toggle */}
        <button
          onClick={onToggleCollapse}
          className="btn btn-ghost btn-sm w-full gap-2"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <><ChevronLeft className="h-4 w-4" /><span className="text-xs">Collapse</span></>}
        </button>

        {/* Logout */}
        {collapsed ? (
          <button
            onClick={async () => { if (await Confirm.ask('Log Out?', 'You will need to sign in again.')) onLogout(); }}
            className="btn btn-ghost btn-sm w-full tooltip tooltip-right"
            data-tip="Logout"
          >
            <LogOut className="h-4 w-4 text-error" />
          </button>
        ) : (
          <button
            onClick={async () => { if (await Confirm.ask('Log Out?', 'You will need to sign in again.')) onLogout(); }}
            className="btn btn-outline btn-error btn-sm w-full gap-2"
          >
            <LogOut className="h-4 w-4" />
            Logout
          </button>
        )}
      </div>
    </aside>
  );
};

export default Sidebar;