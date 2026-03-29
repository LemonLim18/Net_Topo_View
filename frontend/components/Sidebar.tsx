import React, { useState } from 'react';
import { 
  Home,
  Network,
  BotMessageSquare, 
  Activity,
  ShieldCheck,
  LogOut,
  Server,
  BookOpen,
  Workflow,
  PackagePlus,
  History,
  ListChecks,
  ChevronLeft,
  ChevronRight,
  FileCode2,
  HardDrive,
} from 'lucide-react';
import type { ViewId, User } from '../types';
import { normalizeUserRole } from '../types';
import { Confirm } from '../src/utils/swal';
import { SHOW_SDN_CONTROLLER } from '../src/config';

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
  const [automationOpen, setAutomationOpen] = useState(true);

  if (!user) return null;
  const normalizedRole = normalizeUserRole(user.role);

  // ── Menu Item ──
  const MenuItem = ({
    id,
    label,
    icon: Icon,
    iconColor,
    textColor,
    matchViews,
  }: {
    id: ViewId;
    label: string;
    icon: any;
    iconColor?: string;
    textColor?: string;
    /** Treat these views as active for this row (e.g. legacy drift / policy routes → hub). */
    matchViews?: ViewId[];
  }) => {
    const isActive = matchViews?.length
      ? matchViews.includes(currentView)
      : currentView === id;
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
          <Icon className={`h-4 w-4 shrink-0 ${isActive ? 'text-primary' : (iconColor || '')}`} />
          {!collapsed && <span className={`whitespace-nowrap overflow-hidden ${isActive ? '' : (textColor || '')}`}>{label}</span>}
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
            <div className={`${iconColor}`}>{label}</div>
          </summary>
          <ul>{children}</ul>
        </details>
      </li>
    );
  };

  return (
    <aside
      className={`bg-base-100 shrink-0 shadow-lg z-10 flex flex-col h-full transition-all duration-300 ease-in-out
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
              <div className="text-xs opacity-60 capitalize">{normalizedRole}</div>
            </div>
          </div>
        )}
      </div>

      {/* Navigation Menu */}
      <ul className={`menu ${collapsed ? 'p-1' : 'p-2'} w-full grow overflow-y-auto min-h-0 flex-col flex-nowrap`}>
        <MenuItem id="overview" label="Dashboard" icon={Home} iconColor="text-warning" textColor="text-warning" />

        <SectionHeader
          label="Network Onboarding"
          icon={Activity}
          iconColor="text-primary"
          open={networkOpen}
          onToggle={() => setNetworkOpen(!networkOpen)}
        >
          
          <MenuItem id="netbox_devices" label="Device Inventory" icon={Server} iconColor="text-primary"/>
          <MenuItem id="template_studio" label="Template Studio" icon={FileCode2} iconColor="text-primary"/>
          <MenuItem id="onboarding" label="Onboard Device" icon={PackagePlus} iconColor="text-primary"/>
        </SectionHeader>

        <SectionHeader
          label="Safety/Monitoring/Policy"
          icon={ShieldCheck}
          iconColor="text-secondary"
          open={automationOpen}
          onToggle={() => setAutomationOpen(!automationOpen)}
        >
          <MenuItem id="topology" label="Topology Map" icon={Network} iconColor="text-secondary"/>
          <MenuItem id="net_perf" label="Performance" icon={Activity} iconColor="text-secondary"/>
          <MenuItem
            id="compliance_hub"
            label="Compliance & governance"
            icon={ShieldCheck}
            iconColor="text-secondary"
            matchViews={['compliance_hub', 'drift', 'policy_builder']}
          />
          <MenuItem id="config_backup" label="Config Backup" icon={HardDrive} iconColor="text-secondary"/>          
        </SectionHeader>

        <SectionHeader
          label="Automation & AI"
          icon={BotMessageSquare}
          iconColor="text-accent"
          open={automationOpen}
          onToggle={() => setAutomationOpen(!automationOpen)}
        >
          {/* <MenuItem id="net_chat" label="Network Copilot" icon={BotMessageSquare} /> */}
          <MenuItem id="ansible_studio" label="Ansible Studio" icon={Workflow} iconColor="text-accent"/>
          {/* <MenuItem id="intent_parser" label="Intent Parser" icon={BotMessageSquare} iconColor="text-accent"/> */}
          {/* <MenuItem id="intent_history" label="Intent History" icon={History} iconColor="text-accent"/> */}
          {/* <MenuItem id="playbook_comparison" label="Playbook Compare" icon={Columns} /> */}
          <MenuItem id="approval_queue" label="Approval Queue" icon={ListChecks} iconColor="text-accent"/>
          {SHOW_SDN_CONTROLLER && (
            <MenuItem id="sdn_dashboard" label="SDN Controllers" icon={Network} iconColor="text-accent"/>
          )}
          <MenuItem id="knowledge_base" label="Knowledge Base" icon={BookOpen} iconColor="text-accent"/>
        </SectionHeader>
        {/* <SectionHeader
          label="Security"
          icon={ShieldCheck}
          iconColor="text-secondary"
          open={securityOpen}
          onToggle={() => setSecurityOpen(!securityOpen)}
          >
          <MenuItem id="sec_dashboard" label="Security Dashboard" icon={Gauge} iconColor="text-secondary"/>
          <MenuItem id="sec_events" label="SIEM & Events" icon={ShieldAlert} iconColor="text-secondary"/>
          <MenuItem id="sec_analysis" label="Threat Analysis" icon={ScanSearch} iconColor="text-secondary"/>
          <MenuItem id="sec_chat" label="Security Copilot" icon={BotMessageSquare} iconColor="text-secondary"/>
          <MenuItem id="incidents" label="Incident Response" icon={Siren} iconColor="text-secondary"/>
        </SectionHeader> */}
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
