import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Editor, { type Monaco } from '@monaco-editor/react';
import {
  FileCode2,
  FolderOpen,
  Save,
  Plus,
  Trash2,
  Upload,
  Eye,
  RefreshCw,
  Loader2,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
  Play,
  X,
  LogIn,
} from 'lucide-react';
import { configApi, templateGitApi } from '../src/services/api';
import { Toast, Confirm } from '../src/utils/swal';

const TEMPLATE_TABS_KEY = 'templateStudio.tabs';
const TEMPLATE_ACTIVE_TAB_KEY = 'templateStudio.activeTabId';
const TEMPLATE_EXPANDED_VENDORS_KEY = 'templateStudio.expandedVendors';
const TEMPLATE_VARIABLES_KEY = 'templateStudio.variables';
const TEMPLATE_GIT_HEIGHT_KEY = 'templateStudio.gitTerminalHeight';
const TEMPLATE_GIT_MIN_KEY = 'templateStudio.gitTerminalMinimized';
const TEMPLATE_EDITOR_LANGUAGE = 'nettopo-jinja';

const TEMPLATE_SCOPE_OPTIONS = [
  { value: 'shared', label: 'Shared (all roles)' },
  { value: 'core', label: 'Core' },
  { value: 'distribution', label: 'Distribution' },
  { value: 'access', label: 'Access' },
];

const TEMPLATE_EXPR_KEYWORDS = new Set([
  'if', 'else', 'elif', 'endif', 'for', 'endfor', 'in', 'is', 'defined', 'and', 'or', 'not',
  'true', 'false', 'none', 'set', 'with', 'without', 'loop', 'block', 'endblock', 'macro',
  'endmacro', 'filter', 'endfilter', 'include', 'extends', 'import', 'from', 'as', 'recursive',
]);

const TEMPLATE_EXPR_LITERALS = new Set([
  'full', 'half', 'auto', 'input', 'output', 'any', 'permit', 'deny', 'tcp', 'udp', 'icmp', 'ip',
  'low', 'default', 'remainder', 'strict', 'high', 'transmit', 'drop', 'accept', 'reject', 'discard',
]);

interface SchemaProperty {
  type?: string;
  description?: string;
  items?: SchemaProperty;
  properties?: Record<string, SchemaProperty>;
  required?: string[];
}

function ensureTemplateEditorLanguage(monaco: Monaco) {
  if (!monaco.languages.getLanguages().some((lang: { id: string }) => lang.id === TEMPLATE_EDITOR_LANGUAGE)) {
    monaco.languages.register({
      id: TEMPLATE_EDITOR_LANGUAGE,
      extensions: ['.j2', '.jinja2'],
      aliases: ['Jinja2', 'jinja2'],
    });

    monaco.languages.setLanguageConfiguration(TEMPLATE_EDITOR_LANGUAGE, {
      comments: { blockComment: ['{#', '#}'] },
      brackets: [['{', '}'], ['[', ']'], ['(', ')']],
      autoClosingPairs: [
        { open: '{', close: '}' },
        { open: '[', close: ']' },
        { open: '(', close: ')' },
        { open: '"', close: '"' },
        { open: "'", close: "'" },
      ],
      surroundingPairs: [
        { open: '{', close: '}' },
        { open: '[', close: ']' },
        { open: '(', close: ')' },
        { open: '"', close: '"' },
        { open: "'", close: "'" },
      ],
    });

    monaco.languages.setMonarchTokensProvider(TEMPLATE_EDITOR_LANGUAGE, {
      tokenizer: {
        root: [
          [/\{#-?[\s\S]*?-?#\}/, 'comment'],
          [/\{\{-?/, { token: 'delimiter', next: '@expression' }],
          [/\{%-?/, { token: 'delimiter', next: '@statement' }],
          [/[^\{]+/, ''],
          [/\{/, ''],
        ],
        expression: [
          [/-?\}\}/, { token: 'delimiter', next: '@pop' }],
          { include: '@tagBody' },
        ],
        statement: [
          [/-?%\}/, { token: 'delimiter', next: '@pop' }],
          { include: '@tagBody' },
        ],
        tagBody: [
          [/\s+/, ''],
          [/"([^"\\]|\\.)*"/, 'string'],
          [/'([^'\\]|\\.)*'/, 'string'],
          [/\|[a-zA-Z_][a-zA-Z0-9_]*/, 'predefined'],
          [/\.[a-zA-Z_][a-zA-Z0-9_]*/, 'variable.member'],
          [/\b(if|else|elif|endif|for|endfor|in|is|defined|and|or|not|true|false|none|set|with|without|block|endblock|macro|endmacro|filter|endfilter|include|extends|import|from|as)\b/, 'keyword'],
          [/\b\d+(?:\.\d+)?\b/, 'number'],
          [/[a-zA-Z_][a-zA-Z0-9_]*/, 'variable.name'],
          [/[-+*=<>!]+/, 'operator'],
          [/[()\[\],:]/, 'delimiter.bracket'],
        ],
      },
    });
  }

  monaco.editor.defineTheme('nettopo-jinja-dark', {
    base: 'vs-dark',
    inherit: true,
    rules: [
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
    ],
    colors: {
      'editor.background': '#0b1220',
      'editorLineNumber.foreground': '#475569',
      'editorLineNumber.activeForeground': '#cbd5e1',
      'editorGutter.background': '#0b1220',
      'editor.selectionBackground': '#1d4ed833',
      'editor.inactiveSelectionBackground': '#33415555',
      'editorCursor.foreground': '#f8fafc',
      'editorIndentGuide.background1': '#1e293b',
      'editorIndentGuide.activeBackground1': '#334155',
    },
  });
}

function extractTemplateVariables(content: string): string[] {
  const roots = new Set<string>();
  const loopAliases = new Set<string>();

  for (const match of content.matchAll(/\{%\s*for\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in\s+([a-zA-Z_][a-zA-Z0-9_]*)/g)) {
    const alias = match[1];
    const collection = match[2];
    loopAliases.add(alias);
    roots.add(collection);
  }

  for (const match of content.matchAll(/\{[{%][\s\S]*?[}%]\}/g)) {
    const block = match[0];
    for (const token of block.matchAll(/\b([a-zA-Z_][a-zA-Z0-9_]*)\b/g)) {
      const name = token[1];
      const prevChar = block[token.index! - 1];
      if (prevChar === '.') continue;
      if (loopAliases.has(name)) continue;
      if (TEMPLATE_EXPR_KEYWORDS.has(name)) continue;
      if (TEMPLATE_EXPR_LITERALS.has(name)) continue;
      roots.add(name);
    }
  }

  return Array.from(roots).sort((a, b) => a.localeCompare(b));
}

function schemaPlaceholder(schema?: SchemaProperty, fieldName?: string): string {
  if (!schema?.type) return fieldName || '';
  if (schema.type === 'array') {
    if (schema.items?.type === 'object' && schema.items.properties) {
      const sample = Object.fromEntries(Object.keys(schema.items.properties).map((key) => [key, '']));
      return JSON.stringify([sample], null, 2);
    }
    return JSON.stringify([], null, 2);
  }
  if (schema.type === 'object') {
    return JSON.stringify({}, null, 2);
  }
  if (schema.type === 'boolean') return 'true or false';
  return schema.description || fieldName || '';
}

function coerceSchemaValue(raw: string, schema?: SchemaProperty): any {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  switch (schema?.type) {
    case 'integer': {
      const value = Number.parseInt(trimmed, 10);
      if (Number.isNaN(value)) throw new Error('Expected an integer');
      return value;
    }
    case 'number': {
      const value = Number.parseFloat(trimmed);
      if (Number.isNaN(value)) throw new Error('Expected a number');
      return value;
    }
    case 'boolean': {
      if (trimmed === 'true') return true;
      if (trimmed === 'false') return false;
      throw new Error('Expected true or false');
    }
    case 'array':
    case 'object':
      return JSON.parse(trimmed);
    default:
      return trimmed;
  }
}

// ---------------------------------------------------------------------------
//  Types
// ---------------------------------------------------------------------------

interface TemplateInfo {
  vendor: string;
  feature: string;
  has_undo: boolean;
  schema_exists: boolean;
}

interface OpenTab {
  id: string; // "vendor/feature"
  vendor: string;
  feature: string;
  content: string;
  originalContent: string;
  isDirty: boolean;
}

interface TemplateGitStatus {
  repo_root: string;
  branch: string;
  is_dirty: boolean;
  origin: string;
  github_remote: boolean;
  github_cli_available: boolean;
  github_authenticated: boolean;
  github_username?: string | null;
}

import { useMonacoTheme } from '../src/utils/monacoTheme';

// ---------------------------------------------------------------------------
//  Template Studio Component
// ---------------------------------------------------------------------------

const TemplateStudioView: React.FC = () => {
  const { themeName } = useMonacoTheme();
  const getStoredTabs = (): OpenTab[] => {
    try {
      const raw = localStorage.getItem(TEMPLATE_TABS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed.filter((t: any) =>
        typeof t?.id === 'string' &&
        typeof t?.vendor === 'string' &&
        typeof t?.feature === 'string' &&
        typeof t?.content === 'string' &&
        typeof t?.originalContent === 'string'
      ).map((t: any) => ({
        id: t.id,
        vendor: t.vendor,
        feature: t.feature,
        content: t.content,
        originalContent: t.originalContent,
        isDirty: Boolean(t.isDirty),
      }));
    } catch {
      return [];
    }
  };

  const getStoredExpandedVendors = (): Set<string> => {
    try {
      const raw = localStorage.getItem(TEMPLATE_EXPANDED_VENDORS_KEY);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(parsed.filter((v: any) => typeof v === 'string'));
    } catch {
      return new Set();
    }
  };

  const getStoredVariables = (): Record<string, string> => {
    try {
      const raw = localStorage.getItem(TEMPLATE_VARIABLES_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return {};
      const next: Record<string, string> = {};
      Object.entries(parsed).forEach(([key, value]) => {
        if (typeof value === 'string') next[key] = value;
      });
      return next;
    } catch {
      return {};
    }
  };

  const hasStoredExpandedRef = useRef(Boolean(localStorage.getItem(TEMPLATE_EXPANDED_VENDORS_KEY)));
  const hasSyncedStoredTabsRef = useRef(false);

  // File tree
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loadingTree, setLoadingTree] = useState(false);
  const [expandedVendors, setExpandedVendors] = useState<Set<string>>(() => getStoredExpandedVendors());

  // Editor tabs
  const [tabs, setTabs] = useState<OpenTab[]>(() => getStoredTabs());
  const [activeTabId, setActiveTabId] = useState<string | null>(() => localStorage.getItem(TEMPLATE_ACTIVE_TAB_KEY));

  // Preview
  const [previewContent, setPreviewContent] = useState<string>('');
  const [isRendering, setIsRendering] = useState(false);

  // Variables panel
  const [variables, setVariables] = useState<Record<string, string>>(() => getStoredVariables());
  const [activeSchema, setActiveSchema] = useState<any>(null);
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // Modals
  const [showNewModal, setShowNewModal] = useState(false);
  const [newVendor, setNewVendor] = useState('cisco_ios');
  const [newScope, setNewScope] = useState('shared');
  const [newFeature, setNewFeature] = useState('');
  const [newContent, setNewContent] = useState('');
  const [newError, setNewError] = useState<string | null>(null);

  const scopedNewFeature = useMemo(() => {
    const base = newFeature.trim();
    if (!base) return '';
    return newScope === 'shared' ? base : `${newScope}__${base}`;
  }, [newFeature, newScope]);

  const [, setGitStatus] = useState<TemplateGitStatus | null>(null);
  const [, setGitOutputLines] = useState<string[]>([]);
  const [, setGitLoadingStatus] = useState(false);
  const [gitTerminalHeight] = useState(() => {
    const raw = Number(localStorage.getItem(TEMPLATE_GIT_HEIGHT_KEY));
    return Number.isFinite(raw) && raw >= 140 ? raw : 220;
  });
  const [gitTerminalMinimized] = useState(() => localStorage.getItem(TEMPLATE_GIT_MIN_KEY) === 'true');
  const [showGitHubLoginModal, setShowGitHubLoginModal] = useState(false);
  const [gitHubUsername, setGitHubUsername] = useState('');
  const [gitHubToken, setGitHubToken] = useState('');
  const [gitUserName, setGitUserName] = useState('');
  const [gitUserEmail, setGitUserEmail] = useState('');
  const [gitHubAuthBusy, setGitHubAuthBusy] = useState(false);

  const activeTab = tabs.find(t => t.id === activeTabId) ?? null;
  const schemaProperties = activeSchema?.properties || {};
  const promptFields = useMemo(() => {
    if (!activeTab) return [] as string[];
    const extracted = extractTemplateVariables(activeTab.content);
    const schemaKeys = Object.keys(schemaProperties);
    return Array.from(new Set([...schemaKeys, ...extracted])).sort((a, b) => a.localeCompare(b));
  }, [activeTab, schemaProperties]);

  // -----------------------------------------------------------------------
  //  Data fetching
  // -----------------------------------------------------------------------

  const fetchTemplates = useCallback(async () => {
    setLoadingTree(true);
    try {
      const res = await configApi.listTemplates();
      setTemplates(res);
      // Auto-expand all vendors
      if (!hasStoredExpandedRef.current) {
        const vendors = new Set(res.map(t => t.vendor));
        setExpandedVendors(vendors);
      }
    } catch (e: any) {
      Toast.error('Failed to load templates', e.message);
    } finally {
      setLoadingTree(false);
    }
  }, []);

  useEffect(() => { fetchTemplates(); }, [fetchTemplates]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      fetchTemplates();
    }, 10000);

    const onFocus = () => fetchTemplates();
    window.addEventListener('focus', onFocus);

    return () => {
      window.clearInterval(interval);
      window.removeEventListener('focus', onFocus);
    };
  }, [fetchTemplates]);

  const reloadTabFromSource = useCallback(async (vendor: string, feature: string, forceIfDirty = false) => {
    const id = `${vendor}/${feature}`;
    const existing = tabs.find((t) => t.id === id);
    if (existing?.isDirty && !forceIfDirty) {
      return false;
    }

    const res = await configApi.getTemplateSource(vendor, feature);
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === id);
      const reloaded: OpenTab = {
        id,
        vendor,
        feature,
        content: res.content,
        originalContent: res.content,
        isDirty: false,
      };
      if (idx === -1) return [...prev, reloaded];
      const next = [...prev];
      next[idx] = reloaded;
      return next;
    });
    return true;
  }, [tabs]);

  useEffect(() => {
    if (hasSyncedStoredTabsRef.current) return;
    if (!tabs.length) {
      hasSyncedStoredTabsRef.current = true;
      return;
    }
    hasSyncedStoredTabsRef.current = true;

    const nonDirtyTabs = tabs.filter((t) => !t.isDirty);
    if (!nonDirtyTabs.length) return;

    (async () => {
      await Promise.all(
        nonDirtyTabs.map((t) => reloadTabFromSource(t.vendor, t.feature, false).catch(() => false))
      );
    })();
  }, [tabs, reloadTabFromSource]);

  useEffect(() => {
    localStorage.setItem(TEMPLATE_TABS_KEY, JSON.stringify(tabs));
  }, [tabs]);

  useEffect(() => {
    if (activeTabId) localStorage.setItem(TEMPLATE_ACTIVE_TAB_KEY, activeTabId);
    else localStorage.removeItem(TEMPLATE_ACTIVE_TAB_KEY);
  }, [activeTabId]);

  useEffect(() => {
    const vendors = Array.from(expandedVendors);
    localStorage.setItem(TEMPLATE_EXPANDED_VENDORS_KEY, JSON.stringify(vendors));
    hasStoredExpandedRef.current = true;
  }, [expandedVendors]);

  useEffect(() => {
    localStorage.setItem(TEMPLATE_VARIABLES_KEY, JSON.stringify(variables));
  }, [variables]);

  useEffect(() => {
    localStorage.setItem(TEMPLATE_GIT_HEIGHT_KEY, String(gitTerminalHeight));
  }, [gitTerminalHeight]);

  useEffect(() => {
    localStorage.setItem(TEMPLATE_GIT_MIN_KEY, String(gitTerminalMinimized));
  }, [gitTerminalMinimized]);

  useEffect(() => {
    if (!tabs.length) {
      if (activeTabId !== null) setActiveTabId(null);
      return;
    }
    if (!activeTabId || !tabs.some(t => t.id === activeTabId)) {
      setActiveTabId(tabs[tabs.length - 1].id);
    }
  }, [tabs, activeTabId]);

  // -----------------------------------------------------------------------
  //  Variable extraction
  // -----------------------------------------------------------------------

  const appendGitOutput = useCallback((text: string) => {
    if (!text) return;
    const stamp = new Date().toLocaleTimeString();
    const lines = text.replace(/\r/g, '').split('\n').filter((line) => line.length > 0);
    if (!lines.length) return;
    setGitOutputLines((prev) => [...prev, ...lines.map((line) => `[${stamp}] ${line}`)].slice(-800));
  }, []);

  const refreshGitStatus = useCallback(async () => {
    setGitLoadingStatus(true);
    try {
      const status = await templateGitApi.status();
      setGitStatus(status);
    } catch (e: any) {
      Toast.error('Git status failed', e.message || 'Could not load repository status');
    } finally {
      setGitLoadingStatus(false);
    }
  }, []);

  const handleGitHubLogin = useCallback(async () => {
    if (!gitHubUsername.trim() || !gitHubToken.trim()) {
      Toast.warning('Missing credentials', 'GitHub username and token are required.');
      return;
    }
    setGitHubAuthBusy(true);
    try {
      const res = await templateGitApi.githubLogin({
        username: gitHubUsername.trim(),
        token: gitHubToken.trim(),
        git_name: gitUserName.trim() || undefined,
        git_email: gitUserEmail.trim() || undefined,
      });
      appendGitOutput(`GitHub connected as ${res.username}`);
      setShowGitHubLoginModal(false);
      setGitHubToken('');
      await refreshGitStatus();
      Toast.success('GitHub connected', `Connected as ${res.username}`);
    } catch (e: any) {
      Toast.error('GitHub login failed', e.message || 'Unable to connect GitHub');
    } finally {
      setGitHubAuthBusy(false);
    }
  }, [appendGitOutput, gitHubToken, gitHubUsername, gitUserEmail, gitUserName, refreshGitStatus]);

  useEffect(() => {
    refreshGitStatus();
  }, [refreshGitStatus]);

  // Update schema when active tab changes
  useEffect(() => {
    if (!activeTab) {
      setActiveSchema(null);
      setValidationErrors({});
      return;
    }

    // Reset errors when switching
    setValidationErrors({});

    // Fetch schema for validation
    const loadSchema = async () => {
      try {
        const schema = await configApi.getSchema(activeTab.feature);
        setActiveSchema(schema);
      } catch (e) {
        setActiveSchema(null);
      }
    };
    loadSchema();
  }, [activeTab?.id]);

  useEffect(() => {
    if (!activeTab) return;
    setVariables((prev) => {
      const next: Record<string, string> = {};
      promptFields.forEach((field) => {
        next[field] = prev[field] ?? '';
      });
      return next;
    });
  }, [activeTab?.id, promptFields]);

  // -----------------------------------------------------------------------
  //  File tree actions
  // -----------------------------------------------------------------------

  const toggleVendor = (vendor: string) => {
    setExpandedVendors(prev => {
      const next = new Set(prev);
      if (next.has(vendor)) next.delete(vendor);
      else next.add(vendor);
      return next;
    });
  };

  const openTemplate = async (vendor: string, feature: string) => {
    const id = `${vendor}/${feature}`;

    // Already open?
    const existing = tabs.find(t => t.id === id);
    if (existing) {
      if (!existing.isDirty) {
        try {
          await reloadTabFromSource(vendor, feature, false);
        } catch {
          // keep showing current content if reload fails
        }
      }
      setActiveTabId(id);
      return;
    }

    try {
      await reloadTabFromSource(vendor, feature, true);
      setActiveTabId(id);
    } catch (e: any) {
      Toast.error('Failed to open template', e.message);
    }
  };

  const refreshTreeAndOpenTabs = async () => {
    await fetchTemplates();

    const openTabs = [...tabs];
    if (!openTabs.length) {
      Toast.success('Template list refreshed from disk');
      return;
    }

    let refreshedCount = 0;
    const dirtyTabs = openTabs.filter((t) => t.isDirty);
    let overwriteDirty = false;
    if (dirtyTabs.length > 0) {
      overwriteDirty = await Confirm.ask(
        `Overwrite ${dirtyTabs.length} dirty tab(s)?`,
        'This will discard unsaved changes and reload latest content from local template files.'
      );
    }

    let skippedDirty = 0;
    await Promise.all(
      openTabs.map(async (t) => {
        if (t.isDirty) {
          if (overwriteDirty) {
            try {
              const changed = await reloadTabFromSource(t.vendor, t.feature, true);
              if (changed) refreshedCount += 1;
            } catch {
              // keep current tab content if reload fails for that file
            }
            return;
          }
          skippedDirty += 1;
          return;
        }
        try {
          const changed = await reloadTabFromSource(t.vendor, t.feature, false);
          if (changed) refreshedCount += 1;
        } catch {
          // keep current tab content if reload fails for that file
        }
      })
    );

    if (skippedDirty > 0) {
      Toast.info(
        'Template list refreshed from disk',
        `${refreshedCount} open tab(s) reloaded; ${skippedDirty} dirty tab(s) kept unchanged.`
      );
      return;
    }
    if (overwriteDirty) {
      Toast.success(`Template list refreshed from disk; ${dirtyTabs.length} dirty tab(s) overwritten.`);
      return;
    }
    Toast.success('Template list refreshed from disk');
  };

  // -----------------------------------------------------------------------
  //  Editor actions
  // -----------------------------------------------------------------------

  const updateContent = (content: string) => {
    if (!activeTabId) return;
    setTabs(prev =>
      prev.map(t =>
        t.id === activeTabId
          ? { ...t, content, isDirty: content !== t.originalContent }
          : t
      )
    );
  };

  const saveTemplate = async () => {
    if (!activeTab || !activeTab.isDirty) return;
    try {
      await configApi.saveTemplateSource(activeTab.vendor, activeTab.feature, activeTab.content);
      setTabs(prev =>
        prev.map(t =>
          t.id === activeTab.id
            ? { ...t, originalContent: t.content, isDirty: false }
            : t
        )
      );
      Toast.success(`Template "${activeTab.feature}" saved`);
    } catch (e: any) {
      Toast.error('Save failed', e.message);
    }
  };

  const closeTab = (id: string) => {
    setTabs(prev => {
      const next = prev.filter(t => t.id !== id);
      if (activeTabId === id) {
        setActiveTabId(next.length > 0 ? next[next.length - 1].id : null);
      }
      return next;
    });
  };

  // Ctrl+S handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        saveTemplate();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [activeTab]);

  // -----------------------------------------------------------------------
  //  Preview
  // -----------------------------------------------------------------------

  const renderPreview = async () => {
    if (!activeTab) return;

    // Validate required fields
    const errors: Record<string, string> = {};
    const requiredFields = activeSchema?.required || [];
    const params: Record<string, any> = {};
    
    requiredFields.forEach((field: string) => {
      if (!variables[field] || variables[field].trim() === '') {
        errors[field] = `${field} is required`;
      }
    });

    promptFields.forEach((field) => {
      const raw = variables[field];
      const fieldSchema = schemaProperties[field] as SchemaProperty | undefined;
      if (!raw || raw.trim() === '') return;
      try {
        params[field] = coerceSchemaValue(raw, fieldSchema);
      } catch (error: any) {
        errors[field] = error?.message || 'Invalid value';
      }
    });

    if (Object.keys(errors).length > 0) {
      setValidationErrors(errors);
      Toast.error('Validation Failed', 'Please fill in all required fields');
      return;
    }

    setValidationErrors({});
    setIsRendering(true);
    try {
      const res = await configApi.render(activeTab.vendor, activeTab.feature, params);
      setPreviewContent(res.config);
    } catch (e: any) {
      setPreviewContent(`Error: ${e.message}`);
    } finally {
      setIsRendering(false);
    }
  };

  // -----------------------------------------------------------------------
  //  Create / Delete
  // -----------------------------------------------------------------------

  const handleCreateTemplate = async () => {
    if (!newFeature.trim()) {
      setNewError('Feature name is required');
      return;
    }
    const featureName = scopedNewFeature;
    if (!featureName) {
      setNewError('Feature name is required');
      return;
    }
    setNewError(null);
    try {
      await configApi.createTemplate(
        newVendor,
        featureName,
        newContent || `{# ${featureName} template for ${newVendor} #}\n\n`,
      );
      Toast.success(`Template "${newVendor}/${featureName}" created`);
      setShowNewModal(false);
      setNewScope('shared');
      setNewFeature('');
      setNewContent('');
      fetchTemplates();
    } catch (e: any) {
      setNewError(e.message || 'Failed to create template');
    }
  };

  const handleDeleteTemplate = async (vendor: string, feature: string) => {
    const ok = await Confirm.danger(
      `Delete ${vendor}/${feature}?`,
      'This permanently removes the template file.',
    );
    if (!ok) return;
    try {
      await configApi.deleteTemplate(vendor, feature);
      Toast.success(`Template "${feature}" deleted`);
      closeTab(`${vendor}/${feature}`);
      fetchTemplates();
    } catch (e: any) {
      Toast.error('Delete failed', e.message);
    }
  };

  const handleUpload = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.j2,.jinja2,.txt';
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const content = await file.text();
      const featureName = file.name.replace(/\.(j2|jinja2|txt)$/, '');
      setNewContent(content);
      setNewScope('shared');
      setNewFeature(featureName);
      setShowNewModal(true);
    };
    input.click();
  };

  // -----------------------------------------------------------------------
  //  Group templates by vendor
  // -----------------------------------------------------------------------

  const grouped = templates.reduce<Record<string, TemplateInfo[]>>((acc, t) => {
    if (!acc[t.vendor]) acc[t.vendor] = [];
    acc[t.vendor].push(t);
    return acc;
  }, {});

  // -----------------------------------------------------------------------
  //  Render
  // -----------------------------------------------------------------------

  return (
    <div className="flex flex-col h-full overflow-hidden select-none">
      <div className="flex grow min-h-0 overflow-hidden">

        {/* ─── Left: File Tree ─── */}
        <div
          className="shrink-0 overflow-y-auto border-r border-base-300 bg-base-100 flex flex-col"
          style={{ width: '220px' }}
        >
          <div className="p-2 border-b border-base-300 flex items-center justify-between">
            <span className="text-xs font-bold uppercase opacity-60">Templates</span>
            <div className="flex gap-1">
              <button
                className="btn btn-ghost btn-xs"
                title="Upload .j2 file"
                onClick={handleUpload}
              >
                <Upload className="w-3.5 h-3.5" />
              </button>
              <button
                className="btn btn-ghost btn-xs"
                title="New template"
                onClick={() => { setNewContent(''); setNewScope('shared'); setNewFeature(''); setShowNewModal(true); }}
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
              <button
                className="btn btn-ghost btn-xs"
                title="Refresh"
                onClick={refreshTreeAndOpenTabs}
                disabled={loadingTree}
              >
                <RefreshCw className={`w-3.5 h-3.5 ${loadingTree ? 'animate-spin' : ''}`} />
              </button>
            </div>
          </div>

          <div className="p-1 grow overflow-y-auto">
            {loadingTree ? (
              <div className="text-center py-8 opacity-50">
                <Loader2 className="w-5 h-5 animate-spin mx-auto mb-1" />
                <p className="text-xs">Loading…</p>
              </div>
            ) : Object.keys(grouped).length === 0 ? (
              <div className="text-center py-8 opacity-40">
                <FileCode2 className="w-6 h-6 mx-auto mb-1" />
                <p className="text-xs">No templates found</p>
              </div>
            ) : (
              Object.entries(grouped).sort().map(([vendor, items]) => (
                <div key={vendor} className="mb-1">
                  <button
                    className="flex items-center gap-1 w-full px-2 py-1 text-xs font-semibold hover:bg-base-200 rounded transition-colors"
                    onClick={() => toggleVendor(vendor)}
                  >
                    {expandedVendors.has(vendor)
                      ? <ChevronDown className="w-3 h-3" />
                      : <ChevronRight className="w-3 h-3" />
                    }
                    <FolderOpen className="w-3.5 h-3.5 text-warning" />
                    <span>{vendor}</span>
                    <span className="badge badge-xs badge-ghost ml-auto">{items.length}</span>
                  </button>
                  {expandedVendors.has(vendor) && (
                    <div className="ml-4">
                      {items.sort((a, b) => a.feature.localeCompare(b.feature)).map(t => (
                        <div
                          key={t.feature}
                          className={`flex items-center gap-1 px-2 py-0.5 text-xs cursor-pointer rounded group transition-colors
                            ${activeTabId === `${t.vendor}/${t.feature}` ? 'bg-primary/15 text-primary' : 'hover:bg-base-200'}
                          `}
                          onClick={() => openTemplate(t.vendor, t.feature)}
                        >
                          <FileCode2 className="w-3 h-3 opacity-50" />
                          <span className="truncate grow">{t.feature}.j2</span>
                          <button
                            className="btn btn-ghost btn-xs opacity-0 group-hover:opacity-100 text-error"
                            onClick={(e) => { e.stopPropagation(); handleDeleteTemplate(t.vendor, t.feature); }}
                          >
                            <Trash2 className="w-2.5 h-2.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* ─── Center: Editor + Git CLI ─── */}
        <div className="flex flex-col grow min-w-0 overflow-hidden">
          {/* Tab bar */}
          <div className="flex items-center border-b border-base-300 bg-base-200/50 shrink-0">
            <div className="flex items-center overflow-x-auto grow min-w-0">
              {tabs.map(t => (
                <div
                  key={t.id}
                  className={`flex items-center gap-1 px-3 py-2 text-xs cursor-pointer border-r border-base-300 shrink-0 transition-colors
                    ${activeTabId === t.id ? 'bg-base-100 font-semibold' : 'hover:bg-base-200 opacity-70'}
                  `}
                  onClick={() => setActiveTabId(t.id)}
                >
                  <FileCode2 className="w-3 h-3" />
                  <span>{t.feature}.j2</span>
                  {t.isDirty && <span className="w-1.5 h-1.5 rounded-full bg-warning" />}
                  <button
                    className="btn btn-ghost btn-xs p-0 ml-1 opacity-50 hover:opacity-100"
                    onClick={(e) => { e.stopPropagation(); closeTab(t.id); }}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}
              {tabs.length === 0 && (
                <div className="px-3 py-2 text-xs opacity-40">No files open</div>
              )}
            </div>
            {activeTab && (
              <div className="flex items-center gap-2 px-4 py-2 shrink-0 border-l border-base-300">
                {activeTab.isDirty && <span className="text-[11px] opacity-60">Unsaved changes</span>}
                <button
                  className="btn btn-primary btn-xs gap-1"
                  onClick={saveTemplate}
                  disabled={!activeTab.isDirty}
                  title={activeTab.isDirty ? 'Save (Ctrl+S)' : 'No unsaved changes'}
                >
                  <Save className="w-3.5 h-3.5" /> Save
                </button>
              </div>
            )}
          </div>

          {/* Editor area */}
          <div className="grow overflow-hidden relative">
            {activeTab ? (
              <Editor
                height="100%"
                beforeMount={ensureTemplateEditorLanguage}
                language={TEMPLATE_EDITOR_LANGUAGE}
                theme={themeName}
                path={`${activeTab.vendor}/${activeTab.feature}.j2`}
                value={activeTab.content}
                onChange={(value) => updateContent(value ?? '')}
                options={{
                  minimap: { enabled: false },
                  wordWrap: 'on',
                  fontSize: 13,
                  lineNumbers: 'on',
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  tabSize: 2,
                  renderWhitespace: 'boundary',
                  folding: true,
                  padding: { top: 12, bottom: 12 },
                }}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-full opacity-40 gap-3">
                <FileCode2 className="w-12 h-12" />
                <p className="text-sm">Select a template from the file tree to start editing</p>
                <p className="text-xs">
                  Or{' '}
                  <button className="link link-primary" onClick={handleUpload}>upload a .j2 file</button>{' '}
                  or{' '}
                  <button className="link link-primary" onClick={() => { setNewScope('shared'); setShowNewModal(true); }}>create a new template</button>
                </p>
              </div>
            )}
          </div>

          {/* Middle-bottom Git CLI (center panel only) */}
          {/* <div className="border-t border-base-300 bg-base-100 shrink-0" style={{ height: gitTerminalMinimized ? 40 : gitTerminalHeight }}>
            <div
              className="h-1 cursor-row-resize hover:bg-base-300/40"
              onMouseDown={onGitResizeMouseDown}
              title="Resize git CLI"
            />
            <div className="h-[calc(100%-0.25rem)] flex flex-col">
              <div className="px-3 py-2 border-b border-base-300 flex items-center gap-2 text-xs">
                <Terminal className="w-3.5 h-3.5 text-primary" />
                <span className="font-semibold uppercase tracking-wide opacity-70">Git CLI</span>
                <span className="badge badge-xs badge-outline gap-1">
                  <GitBranch className="w-3 h-3" />
                  {gitStatus?.branch || 'unknown'}
                </span>
                <span className={`badge badge-xs ${gitStatus?.is_dirty ? 'badge-warning' : 'badge-success'}`}>
                  {gitStatus?.is_dirty ? 'dirty' : 'clean'}
                </span>
                <span className={`badge badge-xs ${gitStatus?.github_authenticated ? 'badge-success' : 'badge-ghost'}`}>
                  {gitStatus?.github_authenticated ? `GitHub ${gitStatus.github_username || 'connected'}` : 'GitHub disconnected'}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  <button className="btn btn-ghost btn-xs" onClick={refreshGitStatus} disabled={gitLoadingStatus} title="Refresh git status">
                    <RefreshCw className={`w-3.5 h-3.5 ${gitLoadingStatus ? 'animate-spin' : ''}`} />
                  </button>
                  <button className="btn btn-ghost btn-xs gap-1" onClick={() => setShowGitHubLoginModal(true)} disabled={gitHubAuthBusy}>
                    <LogIn className="w-3.5 h-3.5" /> Login
                  </button>
                  <button className="btn btn-ghost btn-xs gap-1" onClick={handleGitHubLogout} disabled={gitHubAuthBusy}>
                    <LogOut className="w-3.5 h-3.5" /> Logout
                  </button>
                  <button className="btn btn-ghost btn-xs" onClick={() => setGitOutputLines([])} title="Clear CLI output">
                    <Eraser className="w-3.5 h-3.5" />
                  </button>
                  <button className="btn btn-ghost btn-xs" onClick={() => setGitTerminalMinimized((v) => !v)}>
                    {gitTerminalMinimized ? <Maximize2 className="w-3.5 h-3.5" /> : <Minimize2 className="w-3.5 h-3.5" />}
                  </button>
                </div>
              </div>

              {!gitTerminalMinimized && (
                <>
                  <div className="grow min-h-0 overflow-y-auto p-2 bg-neutral/10 font-mono text-xs">
                    {gitOutputLines.length === 0 ? (
                      <p className="opacity-60">Type git commands directly. Examples: git init, git add, git commit, git checkout, git branch, git push, git pull, git merge, git stash.</p>
                    ) : (
                      <div className="space-y-0.5">
                        {gitOutputLines.map((line, idx) => (
                          <div key={`${idx}-${line.slice(0, 20)}`} className="whitespace-pre-wrap wrap-break-word">{line}</div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="p-2 border-t border-base-300 bg-neutral/5">
                    <div
                      className="w-full font-mono text-xs flex items-center gap-2"
                      onClick={() => gitCommandInputRef.current?.focus()}
                    >
                      <span className="opacity-70">$</span>
                      <input
                        ref={gitCommandInputRef}
                        className="flex-1 bg-transparent border-0 outline-none focus:outline-none focus:ring-0 p-0 m-0 text-xs"
                        placeholder=""
                        spellCheck={false}
                        autoCorrect="off"
                        autoCapitalize="none"
                        value={gitCommand}
                        onChange={(e) => setGitCommand(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            runGitCommand();
                          }
                        }}
                      />
                      {gitRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin opacity-70" /> : null}
                    </div>
                  </div>
                </> */}
              {/* )} */}
            {/* </div> */}
          {/* </div> */}
        </div>

        {/* ─── Right: Variables + Preview ─── */}
        <div
          className="shrink-0 overflow-hidden border-l border-base-300 bg-base-100 flex flex-col"
          style={{ width: '300px' }}
        >
          {activeTab ? (
            <>
              {/* Variables panel */}
              <div className="border-b border-base-300 p-3 shrink-0 overflow-y-auto" style={{ maxHeight: '45%' }}>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-bold uppercase opacity-60">Template Variables</span>
                  <span className="badge badge-xs badge-ghost">{promptFields.length}</span>
                </div>
                {promptFields.length === 0 ? (
                  <p className="text-xs opacity-40">No {'{{ variables }}'} found in template</p>
                ) : (
                  <div className="space-y-2">
                    {promptFields.map((key) => {
                      const val = variables[key] ?? '';
                      const fieldSchema = schemaProperties[key] as SchemaProperty | undefined;
                      const fieldType = fieldSchema?.type || 'string';
                      const isRequired = activeSchema?.required?.includes(key);
                      const hasError = !!validationErrors[key];
                      
                      return (
                        <div key={key} className="form-control w-full">
                          <label className="label py-0">
                            <span className="label-text text-xs font-mono">
                              {`{{ ${key} }}`}
                              {isRequired && <span className="text-error ml-1">*</span>}
                            </span>
                            <span className="label-text-alt text-[10px] opacity-50 uppercase">{fieldType}</span>
                          </label>
                          {fieldType === 'boolean' ? (
                            <select
                              className={`select select-bordered select-xs w-full font-mono ${hasError ? 'select-error' : ''}`}
                              value={val}
                              onChange={(e) => {
                                setVariables(prev => ({ ...prev, [key]: e.target.value }));
                                if (validationErrors[key]) {
                                  setValidationErrors(prev => {
                                    const next = { ...prev };
                                    delete next[key];
                                    return next;
                                  });
                                }
                              }}
                            >
                              <option value="">Unset</option>
                              <option value="true">true</option>
                              <option value="false">false</option>
                            </select>
                          ) : fieldType === 'array' || fieldType === 'object' ? (
                            <textarea
                              className={`textarea textarea-bordered w-full font-mono text-xs min-h-24 ${hasError ? 'textarea-error' : ''}`}
                              placeholder={schemaPlaceholder(fieldSchema, key)}
                              value={val}
                              onChange={(e) => {
                                setVariables(prev => ({ ...prev, [key]: e.target.value }));
                                if (validationErrors[key]) {
                                  setValidationErrors(prev => {
                                    const next = { ...prev };
                                    delete next[key];
                                    return next;
                                  });
                                }
                              }}
                            />
                          ) : (
                            <input
                              type={fieldType === 'integer' || fieldType === 'number' ? 'number' : 'text'}
                              className={`input input-bordered input-xs w-full font-mono ${hasError ? 'input-error' : ''}`}
                              placeholder={schemaPlaceholder(fieldSchema, key)}
                              value={val}
                              onChange={(e) => {
                                setVariables(prev => ({ ...prev, [key]: e.target.value }));
                                if (validationErrors[key]) {
                                  setValidationErrors(prev => {
                                    const next = { ...prev };
                                    delete next[key];
                                    return next;
                                  });
                                }
                              }}
                            />
                          )}
                          {fieldSchema?.description && (
                            <div className="label py-0 w-full">
                              <span className="label-text-alt text-[10px] opacity-50">{fieldSchema.description}</span>
                            </div>
                          )}
                          {hasError && (
                            <div className="label py-0 w-full">
                              <span className="label-text-alt text-error text-[10px] mt-0.5">
                                {validationErrors[key]}
                              </span>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Preview */}
              <div className="flex flex-col grow min-h-0">
                <div className="flex items-center justify-between p-3 border-b border-base-300 shrink-0">
                  <span className="text-xs font-bold uppercase opacity-60">Live Preview</span>
                  <button
                    className="btn btn-xs btn-primary gap-1"
                    onClick={renderPreview}
                    disabled={isRendering}
                  >
                    {isRendering
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <Play className="w-3 h-3" />
                    }
                    Render
                  </button>
                </div>
                <div className="grow overflow-y-auto p-3">
                  {previewContent ? (
                    <pre className="text-xs font-mono whitespace-pre-wrap opacity-90">{previewContent}</pre>
                  ) : (
                    <div className="text-center py-8 opacity-40">
                      <Eye className="w-6 h-6 mx-auto mb-1" />
                      <p className="text-xs">Fill in variables and click Render</p>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full opacity-40 gap-2 p-4">
              <Eye className="w-8 h-8" />
              <p className="text-xs">Preview panel</p>
            </div>
          )}
        </div>
      </div>

      {/* ─── New Template Modal ─── */}
      {showNewModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-md">
            <h3 className="font-bold text-lg mb-4">
              {newContent ? 'Upload Template' : 'New Template'}
            </h3>

            {newError && (
              <div className="alert alert-error alert-sm mb-3">
                <AlertTriangle className="w-4 h-4" />
                <span>{newError}</span>
              </div>
            )}

            <div className="space-y-3">
              <label className="form-control w-full">
                <div className="label"><span className="label-text">Vendor *</span></div>
                <select
                  className="select select-bordered w-full"
                  value={newVendor}
                  onChange={(e) => setNewVendor(e.target.value)}
                >
                  <option value="cisco_ios">Cisco IOS / IOS-XE</option>
                  <option value="juniper_junos">Juniper JunOS</option>
                  <option value="huawei_vrp">Huawei VRP</option>
                </select>
              </label>

              <label className="form-control w-full">
                <div className="label"><span className="label-text">Template Scope</span></div>
                <select
                  className="select select-bordered w-full"
                  value={newScope}
                  onChange={(e) => setNewScope(e.target.value)}
                >
                  {TEMPLATE_SCOPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </label>

              <label className="form-control w-full">
                <div className="label"><span className="label-text">Feature Name *</span></div>
                <input
                  type="text"
                  className="input input-bordered w-full"
                  placeholder="e.g. day0_bootstrap"
                  value={newFeature}
                  onChange={(e) => setNewFeature(e.target.value)}
                />
                <div className="label">
                  <span className="label-text-alt opacity-50">
                    Will be saved as {newVendor}/{scopedNewFeature || '…'}.j2
                  </span>
                </div>
              </label>

              {newContent && (
                <label className="form-control w-full">
                  <div className="label"><span className="label-text">Content Preview</span></div>
                  <textarea
                    className="textarea textarea-bordered w-full font-mono text-xs h-32"
                    value={newContent}
                    onChange={(e) => setNewContent(e.target.value)}
                  />
                </label>
              )}
            </div>

            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowNewModal(false)}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleCreateTemplate}>
                <Plus className="w-4 h-4" /> Create Template
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => setShowNewModal(false)} />
        </div>
      )}

      {showGitHubLoginModal && (
        <div className="modal modal-open">
          <div className="modal-box max-w-lg">
            <h3 className="font-bold text-lg mb-2">Connect GitHub</h3>
            <p className="text-xs opacity-60 mb-4">
              Use a GitHub Personal Access Token with repository permissions. Credentials are stored via git credential manager.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="form-control w-full">
                <div className="label"><span className="label-text">GitHub Username</span></div>
                <input
                  type="text"
                  className="input input-bordered"
                  value={gitHubUsername}
                  onChange={(e) => setGitHubUsername(e.target.value)}
                />
              </label>
              <label className="form-control w-full md:col-span-1">
                <div className="label"><span className="label-text">Git Commit Name (optional)</span></div>
                <input
                  type="text"
                  className="input input-bordered"
                  value={gitUserName}
                  onChange={(e) => setGitUserName(e.target.value)}
                />
              </label>
              <label className="form-control w-full md:col-span-2">
                <div className="label"><span className="label-text">Personal Access Token</span></div>
                <input
                  type="password"
                  className="input input-bordered"
                  value={gitHubToken}
                  onChange={(e) => setGitHubToken(e.target.value)}
                />
              </label>
              <label className="form-control w-full md:col-span-2">
                <div className="label"><span className="label-text">Git Commit Email (optional)</span></div>
                <input
                  type="email"
                  className="input input-bordered"
                  value={gitUserEmail}
                  onChange={(e) => setGitUserEmail(e.target.value)}
                />
              </label>
            </div>
            <div className="modal-action">
              <button className="btn btn-ghost" onClick={() => setShowGitHubLoginModal(false)} disabled={gitHubAuthBusy}>Cancel</button>
              <button className="btn btn-primary gap-1" onClick={handleGitHubLogin} disabled={gitHubAuthBusy}>
                {gitHubAuthBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />} Connect
              </button>
            </div>
          </div>
          <div className="modal-backdrop" onClick={() => !gitHubAuthBusy && setShowGitHubLoginModal(false)} />
        </div>
      )}
    </div>
  );
};

export default TemplateStudioView;
