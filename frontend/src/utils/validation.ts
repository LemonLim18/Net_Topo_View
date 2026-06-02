/**
 * Network input validation and formatting utilities.
 */

export const formatIpInput = (value: string): string => {
  // Remove any character that is not a digit or a dot
  let cleaned = value.replace(/[^0-9.]/g, '');
  
  // Split by dot to validate each octet
  const parts = cleaned.split('.');
  const validatedParts: string[] = [];
  const maxParts = parts.slice(0, 4);
  
  maxParts.forEach((part) => {
    if (part !== '') {
      const num = parseInt(part, 10);
      if (num > 255) {
        validatedParts.push('255');
      } else {
        // Remove leading zeros, except when the part is just "0"
        if (part.length > 1 && part.startsWith('0')) {
          validatedParts.push(num.toString());
        } else {
          validatedParts.push(part);
        }
      }
    } else {
      validatedParts.push('');
    }
  });
  
  let result = validatedParts.join('.');
  
  // Auto-append dot if the last part is 3 digits long, and we have less than 4 parts, and the input didn't just delete a dot
  const lastPart = validatedParts[validatedParts.length - 1];
  if (
    validatedParts.length < 4 &&
    lastPart &&
    lastPart.length === 3 &&
    !value.endsWith('.')
  ) {
    result += '.';
  }
  
  return result;
};

export const isValidIp = (ip: string): boolean => {
  if (!ip) return false;
  const parts = ip.split('.');
  if (parts.length !== 4) return false;
  return parts.every((part) => {
    if (part === '') return false;
    const num = parseInt(part, 10);
    return !isNaN(num) && num >= 0 && num <= 255 && part === num.toString();
  });
};

export const formatSubnetMaskInput = (value: string): string => {
  // Allow only digits and dots
  let cleaned = value.replace(/[^0-9.]/g, '');
  
  // If there is no dot and it is a short numeric input, restrict to prefix length (0-32)
  if (!cleaned.includes('.')) {
    if (cleaned === '') return '';
    const num = parseInt(cleaned, 10);
    if (num > 32) {
      // If it exceeds 32, treat it as the first octet of a dot-decimal subnet mask
      if (num > 255) {
        return '255.';
      }
      return cleaned + '.';
    }
    return cleaned;
  }
  
  // Otherwise, format as a standard dot-decimal mask
  return formatIpInput(cleaned);
};

export const isValidSubnetMask = (mask: string): boolean => {
  if (!mask) return false;
  if (!mask.includes('.')) {
    const num = parseInt(mask, 10);
    return !isNaN(num) && num >= 0 && num <= 32 && num.toString() === mask;
  }
  return isValidIp(mask);
};

export const subnetMaskToPrefix = (mask: string): number | null => {
  if (!isValidSubnetMask(mask)) return null;
  if (!mask.includes('.')) return parseInt(mask, 10);
  const parts = mask.split('.').map((part) => parseInt(part, 10));
  const bits = parts.map((part) => part.toString(2).padStart(8, '0')).join('');
  if (!/^1*0*$/.test(bits)) return null;
  return bits.indexOf('0') === -1 ? 32 : bits.indexOf('0');
};

const ipv4ToNumber = (ip: string): number | null => {
  if (!isValidIp(ip)) return null;
  return ip.split('.').reduce((acc, part) => ((acc << 8) + parseInt(part, 10)) >>> 0, 0);
};

const numberToIpv4 = (value: number): string => (
  [24, 16, 8, 0].map((shift) => ((value >>> shift) & 255)).join('.')
);

export const validateUsableHostIp = (ip: string, mask: string, label = 'IP address'): string | null => {
  if (!ip.trim()) return `${label} is required`;
  if (!mask.trim()) return 'Subnet mask is required';
  if (!isValidIp(ip)) return `${label} is invalid`;
  const prefix = subnetMaskToPrefix(mask);
  if (prefix === null) return 'Subnet mask is invalid';
  const ipNum = ipv4ToNumber(ip);
  if (ipNum === null) return `${label} is invalid`;
  const maskNum = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const network = (ipNum & maskNum) >>> 0;
  const broadcast = (network | (~maskNum >>> 0)) >>> 0;
  if (ipNum === network) return `${label} cannot be the network address (${numberToIpv4(network)})`;
  if (ipNum === broadcast) return `${label} cannot be the broadcast address (${numberToIpv4(broadcast)})`;
  return null;
};

export const formatMacInput = (value: string): string => {
  // Remove all non-hex characters
  let hexOnly = value.replace(/[^0-9a-fA-F]/g, '');
  
  // Cap at 12 hex digits
  hexOnly = hexOnly.slice(0, 12).toLowerCase();
  
  // Group in pairs separated by colon
  const parts: string[] = [];
  for (let i = 0; i < hexOnly.length; i += 2) {
    parts.push(hexOnly.slice(i, i + 2));
  }
  
  return parts.join(':');
};

export const isValidMac = (mac: string): boolean => {
  if (!mac) return false;
  const cleaned = mac.replace(/[^0-9a-fA-F]/g, '');
  return cleaned.length === 12 && mac === formatMacInput(cleaned);
};
