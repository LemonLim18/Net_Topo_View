import type { ComplianceResultRecord } from '../src/services/api';
import type { ViewId } from '../types';

const MAX_LINES = 12;
const MAX_LINE_LEN = 120;

export const ANSIBLE_REMEDIATION_INTENT_KEY = 'ansible.remediationIntent';

export interface RemediationIntentPayload {
  intent_description: string;
  target_hosts: string[];
  vendor?: string;
  feature?: string;
}

function truncateLines(lines: string[], max = MAX_LINES): string[] {
  return lines.slice(0, max).map((l) => (l.length > MAX_LINE_LEN ? `${l.slice(0, MAX_LINE_LEN)}…` : l));
}

/**
 * Build a natural-language seed for Ansible AI from a compliance / drift result.
 */
export function buildRemediationIntentPayload(
  event: ComplianceResultRecord,
  meta?: { vendor?: string | null; role?: string | null; hostname?: string | null },
): RemediationIntentPayload {
  const host =
    meta?.hostname ||
    (event as { hostname?: string }).hostname ||
    (event.device_id != null ? `device_id_${event.device_id}` : 'unknown');
  const target_hosts =
    meta?.hostname || (event as { hostname?: string }).hostname
      ? [String(meta?.hostname || (event as { hostname?: string }).hostname)]
      : [];

  const parts: string[] = [];
  parts.push(
    `Remediate configuration drift on ${host} (compliance status: ${event.status || 'unknown'}).`,
  );
  if (meta?.role) parts.push(`Device role (inventory): ${meta.role}.`);
  if (meta?.vendor) parts.push(`Vendor/platform hint: ${meta.vendor}.`);

  const miss = event.missing_lines || [];
  const ext = event.extra_lines || [];
  const ch = event.changed_lines || [];
  if (miss.length) {
    parts.push(
      `Baseline expects these lines to be present (add or restore):\n${truncateLines(miss).map((l) => `+ ${l}`).join('\n')}`,
    );
  }
  if (ext.length) {
    parts.push(
      `These lines should be removed or aligned with policy/baseline:\n${truncateLines(ext).map((l) => `- ${l}`).join('\n')}`,
    );
  }
  if (ch.length) {
    const lines = ch.slice(0, MAX_LINES).map((c) => `Change from "${c.baseline}" to "${c.actual}"`);
    parts.push(`Changed stanzas:\n${lines.join('\n')}`);
  }
  if (event.violated_policies?.length) {
    parts.push(
      `Policy violations: ${event.violated_policies
        .map((v: { policy_id?: string; message?: string }) => `${v.policy_id || 'policy'}: ${v.message || ''}`)
        .join('; ')}`,
    );
  }
  if (event.reason) parts.push(`Analysis: ${event.reason.slice(0, 500)}${event.reason.length > 500 ? '…' : ''}`);

  parts.push(
    'Generate an Ansible playbook that applies the minimal safe changes to align this device with baseline and policy. Use the planned inventory hostnames.',
  );

  const intent_description = parts.join('\n\n');
  const vendorRaw = (meta?.vendor || '').toLowerCase();
  let vendor: string | undefined;
  if (vendorRaw.includes('huawei') || vendorRaw.includes('vrp')) vendor = 'huawei_vrp';
  else if (vendorRaw.includes('juniper') || vendorRaw.includes('junos')) vendor = 'juniper_junos';
  else if (vendorRaw.includes('cisco') || vendorRaw.includes('ios')) vendor = 'cisco_ios';

  const feature =
    event.violated_policies?.[0] && typeof (event.violated_policies[0] as { policy_id?: string }).policy_id === 'string'
      ? 'interface'
      : 'interface';

  return {
    intent_description,
    target_hosts,
    vendor,
    feature,
  };
}

export function storeRemediationIntentAndGoStudio(
  payload: RemediationIntentPayload,
  setView: (v: ViewId) => void,
): void {
  try {
    sessionStorage.setItem(ANSIBLE_REMEDIATION_INTENT_KEY, JSON.stringify(payload));
  } catch {
    /* quota */
  }
  setView('ansible_studio');
}
