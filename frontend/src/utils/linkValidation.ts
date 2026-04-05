import type { InterfaceRecord } from '../services/api';

type LayerType = 'l2' | 'l3';

type ParsedInterface = {
  layer: LayerType;
  networkCidr: string | null;
  error: string | null;
};

const ipv4ToInt = (ip: string): number | null => {
  const parts = ip.split('.');
  if (parts.length !== 4) return null;
  const octets: number[] = [];
  for (const p of parts) {
    if (!/^\d+$/.test(p)) return null;
    const n = Number(p);
    if (!Number.isInteger(n) || n < 0 || n > 255) return null;
    octets.push(n);
  }
  return (((octets[0] << 24) >>> 0) | (octets[1] << 16) | (octets[2] << 8) | octets[3]) >>> 0;
};

const prefixFromMask = (maskRaw: string): number | null => {
  let txt = maskRaw.trim();
  if (!txt) return null;
  if (txt.startsWith('/')) txt = txt.slice(1);

  if (/^\d{1,2}$/.test(txt)) {
    const p = Number(txt);
    return p >= 0 && p <= 32 ? p : null;
  }

  const maskInt = ipv4ToInt(txt);
  if (maskInt == null) return null;

  let zeroSeen = false;
  let prefix = 0;
  for (let i = 31; i >= 0; i -= 1) {
    const bit = (maskInt >>> i) & 1;
    if (bit === 1) {
      if (zeroSeen) return null;
      prefix += 1;
    } else {
      zeroSeen = true;
    }
  }
  return prefix;
};

const parseInterfaceLayerAndSubnet = (iface: InterfaceRecord): ParsedInterface => {
  const ipRaw = String(iface.ip_address || '').trim();
  const maskRaw = String(iface.subnet_mask || '').trim();

  if (!ipRaw) {
    return { layer: 'l2', networkCidr: null, error: null };
  }

  let ipOnly = ipRaw;
  let prefix: number | null = null;

  if (ipRaw.includes('/')) {
    const [base, pfx] = ipRaw.split('/');
    ipOnly = String(base || '').trim();
    prefix = prefixFromMask(String(pfx || '').trim());
  }

  if (prefix == null) {
    prefix = prefixFromMask(maskRaw);
  }

  const ipInt = ipv4ToInt(ipOnly);
  if (ipInt == null) {
    return {
      layer: 'l3',
      networkCidr: null,
      error: `Interface ${iface.name} has invalid IP address '${ipRaw}'.`,
    };
  }

  if (prefix == null) {
    return {
      layer: 'l3',
      networkCidr: null,
      error: `Interface ${iface.name} has IP '${ipRaw}' but missing/invalid subnet mask '${maskRaw || 'empty'}'.`,
    };
  }

  const maskInt = prefix === 0 ? 0 : ((0xffffffff << (32 - prefix)) >>> 0);
  const netInt = (ipInt & maskInt) >>> 0;
  return {
    layer: 'l3',
    networkCidr: `${netInt}/${prefix}`,
    error: null,
  };
};

export const validateLinkInterfaceCompatibility = (
  srcIface?: InterfaceRecord,
  dstIface?: InterfaceRecord,
): string | null => {
  if (!srcIface || !dstIface) {
    return 'Both source and destination interfaces are required.';
  }

  const src = parseInterfaceLayerAndSubnet(srcIface);
  if (src.error) return src.error;

  const dst = parseInterfaceLayerAndSubnet(dstIface);
  if (dst.error) return dst.error;

  if (src.layer !== dst.layer) {
    return `Cannot connect different layer types: ${srcIface.name} is ${src.layer.toUpperCase()} and ${dstIface.name} is ${dst.layer.toUpperCase()}.`;
  }

  if (src.layer === 'l3' && src.networkCidr !== dst.networkCidr) {
    return `Cannot connect different subnets: ${srcIface.name} and ${dstIface.name} are not in the same subnet.`;
  }

  return null;
};
