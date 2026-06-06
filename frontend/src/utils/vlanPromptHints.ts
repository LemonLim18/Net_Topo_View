/**
 * Shared prompt heuristics for VLAN + L2 (Playbook Studio + Ansible chat).
 */

export function userPromptSuggestsNativeVlanIntent(text: string): boolean {
  const t = (text || '').trim().toLowerCase();
  if (!t) return false;
  if (/\bnative\s+vlan\b/.test(t)) return true;
  if (/\bset\s+native\s+vlan\b/.test(t)) return true;
  if (/\bnative-vlan\b/.test(t)) return true;
  if (/\bpvid\b/.test(t) && /\bvlan\b/.test(t)) return true;
  return false;
}

export function parseNativeVlanNumberFromPrompt(text: string): number | null {
  const m =
    text.match(/\bnative\s+vlan\s+(\d{1,4})\b/i) ||
    text.match(/\bnative\s+vlan\s*[:\s]+(\d{1,4})/i) ||
    text.match(/\bset\s+native\s+vlan\s+(\d{1,4})\b/i);
  if (!m) return null;
  const n = parseInt(m[1], 10);
  if (Number.isNaN(n) || n < 1 || n > 4094) return null;
  return n;
}
