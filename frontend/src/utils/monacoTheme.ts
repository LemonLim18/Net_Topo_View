import { useEffect, useState } from 'react';
import { loader } from '@monaco-editor/react';

export function colorToHex(color: string, fallback: string): string {
  if (!color) return fallback;
  color = color.trim();
  if (color.startsWith('#')) return color;
  
  // 1. Handle OKLCH (Tailwind v4 / DaisyUI color format)
  if (color.startsWith('oklch')) {
    try {
      const match = color.match(/oklch\(([^)]+)\)/);
      if (!match) return fallback;
      const parts = match[1].trim().split(/\s+/);
      if (parts.length < 3) return fallback;
      
      let L = parseFloat(parts[0]);
      if (parts[0].endsWith('%')) L = L / 100;
      const C = parseFloat(parts[1]);
      const H = parseFloat(parts[2]);
      
      // H is in degrees, convert to radians
      const hRad = (H * Math.PI) / 180;
      const a = C * Math.cos(hRad);
      const b = C * Math.sin(hRad);
      
      // OKLab to LMS
      const l_lms = L + 0.3963377774 * a + 0.2158017574 * b;
      const m_lms = L - 0.1055613458 * a - 0.0638541728 * b;
      const s_lms = L - 0.0894841775 * a - 1.2914855480 * b;
      
      // Non-linear LMS to linear sRGB
      const l = Math.pow(l_lms, 3);
      const m = Math.pow(m_lms, 3);
      const s = Math.pow(s_lms, 3);
      
      // Linear sRGB to standard sRGB
      const r_linear = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
      const g_linear = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
      const b_linear = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
      
      // Gamma correction
      const gamma = (val: number) => {
        return val <= 0.0031308
          ? 12.92 * val
          : 1.055 * Math.pow(val, 1 / 2.4) - 0.055;
      };
      
      const r = Math.round(Math.max(0, Math.min(1, gamma(r_linear))) * 255);
      const g = Math.round(Math.max(0, Math.min(1, gamma(g_linear))) * 255);
      const blue = Math.round(Math.max(0, Math.min(1, gamma(b_linear))) * 255);
      
      return "#" + ((1 << 24) + (r << 16) + (g << 8) + blue).toString(16).slice(1);
    } catch {
      return fallback;
    }
  }
  
  // 2. Handle RGB/RGBA
  if (color.startsWith('rgb')) {
    const match = color.match(/\d+/g);
    if (match && match.length >= 3) {
      const r = parseInt(match[0], 10);
      const g = parseInt(match[1], 10);
      const b = parseInt(match[2], 10);
      return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
    }
  }
  
  // 3. Fallback to HTML Canvas rasterizer
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const ctx = canvas.getContext('2d');
    if (!ctx) return fallback;
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, 1, 1);
    const data = ctx.getImageData(0, 0, 1, 1).data;
    const r = data[0];
    const g = data[1];
    const b = data[2];
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  } catch {
    return fallback;
  }
}

export function updateMonacoTheme() {
  loader.init().then((monaco) => {
    // Get computed styles by creating a temporary element styled with DaisyUI base classes
    const dummy = document.createElement('div');
    dummy.className = 'bg-base-100 text-base-content';
    dummy.style.position = 'absolute';
    dummy.style.visibility = 'hidden';
    dummy.style.width = '0px';
    dummy.style.height = '0px';
    document.body.appendChild(dummy);
    
    const dummyStyles = getComputedStyle(dummy);
    const bgRgb = dummyStyles.backgroundColor; // e.g., "oklch(0.20768 0.039 265.754)"
    const fgRgb = dummyStyles.color;
    document.body.removeChild(dummy);
    
    const bgHex = colorToHex(bgRgb, '#0f172a');
    const fgHex = colorToHex(fgRgb, '#f8fafc');
    
    // Check dark vs light based on luminance of background color
    const hexToRgb = (hex: string) => {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      return { r, g, b };
    };
    
    const bgRgbObj = hexToRgb(bgHex);
    const luminance = 0.2126 * bgRgbObj.r + 0.7152 * bgRgbObj.g + 0.0722 * bgRgbObj.b;
    const isDark = luminance < 140;
    
    const themeName = 'cognishield-dynamic';
    const baseTheme = isDark ? 'vs-dark' : 'vs';
    
    monaco.editor.defineTheme(themeName, {
      base: baseTheme,
      inherit: true,
      rules: isDark ? [
        { token: 'comment', foreground: '6B7280' },
        { token: 'delimiter', foreground: 'F59E0B' },
        { token: 'delimiter.bracket', foreground: 'FBBF24' },
        { token: 'keyword', foreground: 'C084FC' },
        { token: 'string', foreground: '86EFAC' },
        { token: 'number', foreground: 'FCA5A5' },
        { token: 'variable.name', foreground: '60A5FA' },
        { token: 'variable.member', foreground: '22D3EE' },
        { token: 'predefined', foreground: '7DD3FC' },
        { token: 'operator', foreground: 'F9A8D4' },
      ] : [
        { token: 'comment', foreground: '8E9297' },
        { token: 'delimiter', foreground: 'D97706' },
        { token: 'delimiter.bracket', foreground: 'B45309' },
        { token: 'keyword', foreground: '7C3AED' },
        { token: 'string', foreground: '15803D' },
        { token: 'number', foreground: 'B91C1C' },
        { token: 'variable.name', foreground: '1D4ED8' },
        { token: 'variable.member', foreground: '0891B2' },
        { token: 'predefined', foreground: '0369A1' },
        { token: 'operator', foreground: 'BE185D' },
      ],
      colors: {
        'editor.background': bgHex,
        'editor.foreground': fgHex,
        'editorLineNumber.foreground': isDark ? '#475569' : '#94a3b8',
        'editorLineNumber.activeForeground': isDark ? '#cbd5e1' : '#334155',
        'editorGutter.background': bgHex,
        'editor.selectionBackground': isDark ? '#264f78' : '#add6ff',
        'editor.inactiveSelectionBackground': isDark ? '#3a3d41' : '#e4e6f1',
        'editorCursor.foreground': fgHex,
      },
    });
  });
}

export function useMonacoTheme() {
  const [themeUpdated, setThemeUpdated] = useState(0);

  useEffect(() => {
    updateMonacoTheme();
    setThemeUpdated((n) => n + 1);

    const observer = new MutationObserver(() => {
      updateMonacoTheme();
      setThemeUpdated((n) => n + 1);
    });

    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    });

    return () => observer.disconnect();
  }, []);

  return { themeName: 'cognishield-dynamic', themeUpdated };
}
