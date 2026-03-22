/**
 * Planned-inventory interface preselection for preview / chat / Playbook Studio.
 *
 * Backend may reject LLM interface tokens (e.g. GigabitEthernet…) and attach
 * `invalid_entities[].suggestions` (closest inventory names). Merge those with
 * names from `parsed_intent.parameters` so UIs can tick the right checkboxes
 * everywhere — not only one screen.
 */

export type PlannedInventoryInterfacePicklist = {
  devices: { hostname: string; interfaces: { name: string }[] }[];
} | null;

function pushInterfaceNamesFromList(arr: unknown, out: string[]): void {
  if (!Array.isArray(arr)) return;
  for (const x of arr) {
    if (typeof x === 'string' && x.trim()) out.push(x.trim());
    else if (x && typeof x === 'object' && 'name' in (x as object)) {
      const n = String((x as { name?: string }).name || '').trim();
      if (n) out.push(n);
    }
  }
}

/**
 * Names from primary `parameters` and every `secondary_intents` slice
 * (composite create_vlan + configure_interface keeps interfaces on the secondary).
 */
export function extractTargetInterfaceNamesFromParsedIntent(
  parsed: Record<string, unknown> | null | undefined,
): string[] {
  if (!parsed) return [];
  const out: string[] = [];
  const p = (parsed as { parameters?: Record<string, unknown> }).parameters ?? {};
  if (Array.isArray(p.interfaces_to_shutdown)) {
    pushInterfaceNamesFromList(p.interfaces_to_shutdown, out);
  }
  if (Array.isArray(p.interfaces)) {
    pushInterfaceNamesFromList(p.interfaces, out);
  }
  const secondaries = (parsed as { secondary_intents?: unknown }).secondary_intents;
  if (Array.isArray(secondaries)) {
    for (const sec of secondaries) {
      if (!sec || typeof sec !== 'object') continue;
      const sp = (sec as { parameters?: Record<string, unknown> }).parameters ?? {};
      if (Array.isArray(sp.interfaces_to_shutdown)) {
        pushInterfaceNamesFromList(sp.interfaces_to_shutdown, out);
      }
      if (Array.isArray(sp.interfaces)) {
        pushInterfaceNamesFromList(sp.interfaces, out);
      }
    }
  }
  const seen = new Set<string>();
  const dedup: string[] = [];
  for (const x of out) {
    if (!seen.has(x)) {
      seen.add(x);
      dedup.push(x);
    }
  }
  return dedup;
}

/** Loose match: inventory "Ethernet0/1" vs LLM "GigabitEthernet1/0/2". */
export function interfaceNameMatchesChipForPreselect(chip: string, plannedName: string): boolean {
  const a = chip.trim().toLowerCase();
  const b = plannedName.trim().toLowerCase();
  if (!a || !b) return false;
  if (a === b) return true;
  const strip = (s: string) =>
    s.replace(/^gigabitethernet|^gig|^ethernet|^eth|^fastethernet|^fa|^gi(?=\d)/i, '');
  const sa = strip(a);
  const sb = strip(b);
  if (sa && sb && (sa === sb || sa.endsWith(sb) || sb.endsWith(sa))) return true;
  const m1 = a.match(/(\d+\/\d+(?:\/\d+)?)/);
  const m2 = b.match(/(\d+\/\d+(?:\/\d+)?)/);
  if (m1 && m2 && m1[1] === m2[1]) return true;
  return false;
}

/**
 * Shared shape: `interfaces_inventory_validation.invalid_entities` and
 * `interfaces_disambiguation.invalid_entities` (preview).
 */
export function extractSuggestionChipsFromInvalidEntities(entities: unknown[] | undefined): string[] {
  if (!entities || !Array.isArray(entities)) return [];
  const out: string[] = [];
  for (const e of entities) {
    if (!e || typeof e !== 'object') continue;
    const ent = e as {
      suggestions?: unknown;
      value?: unknown;
      interface_for_match?: unknown;
    };
    if (Array.isArray(ent.suggestions)) {
      for (const s of ent.suggestions) {
        if (typeof s === 'string' && s.trim()) out.push(s.trim());
      }
    }
    if (typeof ent.value === 'string' && ent.value.trim()) out.push(ent.value.trim());
    if (typeof ent.interface_for_match === 'string' && ent.interface_for_match.trim()) {
      out.push(ent.interface_for_match.trim());
    }
  }
  const seen = new Set<string>();
  const dedup: string[] = [];
  for (const x of out) {
    if (!seen.has(x)) {
      seen.add(x);
      dedup.push(x);
    }
  }
  return dedup;
}

export function extractInventoryValidationChipsForPreselect(
  parsed: Record<string, unknown> | null | undefined,
): string[] {
  if (!parsed) return [];
  const iv = (
    parsed as {
      interfaces_inventory_validation?: { invalid_entities?: unknown[] };
    }
  ).interfaces_inventory_validation;
  return extractSuggestionChipsFromInvalidEntities(iv?.invalid_entities);
}

export type BuildPreselectChipsOptions = {
  /** e.g. `interfaces_disambiguation.invalid_entities` when `parsed_intent` is not yet populated. */
  extraInvalidEntities?: unknown[] | undefined;
};

/**
 * Merge intent interface names + validation suggestions (dedup).
 * Use `extraInvalidEntities` for chat preview when suggestions live only on disambiguation.
 */
export function buildPlannedInventoryPreselectChips(
  parsed: Record<string, unknown> | null | undefined,
  options?: BuildPreselectChipsOptions,
): string[] {
  const fromIntent = extractTargetInterfaceNamesFromParsedIntent(parsed);
  const fromParsedInv = extractInventoryValidationChipsForPreselect(parsed);
  const fromExtra = extractSuggestionChipsFromInvalidEntities(options?.extraInvalidEntities);
  const seen = new Set<string>();
  const out: string[] = [];
  for (const x of [...fromIntent, ...fromParsedInv, ...fromExtra]) {
    if (!seen.has(x)) {
      seen.add(x);
      out.push(x);
    }
  }
  return out;
}

/** @deprecated Use {@link buildPlannedInventoryPreselectChips} */
export const buildVlanPreselectChips = buildPlannedInventoryPreselectChips;

/**
 * Pre-check planned-inventory names that match chips (exact, then fuzzy).
 * Works for `interfaces_for_selection` and `interfaces_disambiguation` (same `devices` shape).
 */
/**
 * Pre-check inventory names per device so selecting Ethernet0/3 on SW-A does not
 * auto-select Ethernet0/3 on SW-B (same interface name, different host).
 */
export function preselectPlannedInventoryInterfacesByDevice(
  chips: string[],
  picklist: PlannedInventoryInterfacePicklist,
): Record<string, Set<string>> {
  const out: Record<string, Set<string>> = {};
  if (!picklist?.devices?.length || !chips.length) return out;
  for (const d of picklist.devices) {
    const hn = String(d.hostname || '').trim();
    if (!hn) continue;
    const names = (d.interfaces || []).map((i) => String(i.name || '').trim()).filter(Boolean);
    const set = new Set<string>();
    for (const chip of chips) {
      const c = chip.trim();
      if (!c) continue;
      const exact = names.find((n) => n.toLowerCase() === c.toLowerCase());
      if (exact) {
        set.add(exact);
        continue;
      }
      for (const name of names) {
        if (interfaceNameMatchesChipForPreselect(chip, name)) {
          set.add(name);
          break;
        }
      }
    }
    out[hn] = set;
  }
  return out;
}

export function preselectPlannedInventoryInterfaces(
  chips: string[],
  picklist: PlannedInventoryInterfacePicklist,
): Set<string> {
  const next = new Set<string>();
  if (!picklist?.devices?.length || !chips.length) return next;
  const flat = picklist.devices.flatMap((d) =>
    (d.interfaces || []).map((i) => String(i.name || '').trim()).filter(Boolean),
  );
  const flatLower = new Map(flat.map((n) => [n.toLowerCase(), n]));
  for (const chip of chips) {
    const c = chip.trim();
    if (!c) continue;
    const exact = flatLower.get(c.toLowerCase());
    if (exact) {
      next.add(exact);
      continue;
    }
    for (const name of flat) {
      if (interfaceNameMatchesChipForPreselect(chip, name)) {
        next.add(name);
        break;
      }
    }
  }
  return next;
}

/** @deprecated Use {@link preselectPlannedInventoryInterfaces} */
export const preselectVlanCheckboxesFromChips = preselectPlannedInventoryInterfaces;
