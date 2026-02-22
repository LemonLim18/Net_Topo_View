import React, { useState, useEffect } from 'react';
import { ScanSearch, Sparkles, AlertOctagon, Terminal, ArrowRight, Shield, AlertTriangle, BookOpen, Play, RefreshCw } from 'lucide-react';
import { useNotification } from '../contexts/NotificationContext';
import { securityApi, llmApi } from '../src/services/api';

interface AnalysisResult {
    summary: string;
    containment: string;
    remediation: string;
    escalation: string;
    severity: string;
}

interface Playbook {
    name: string;
    description: string;
    trigger_types: string[];
    mode: string;
    steps: number;
}

interface CveStats {
    total_cves: number;
    by_severity: Record<string, number>;
    by_vendor: Record<string, number>;
}

const SecurityAnalysis: React.FC = () => {
    const [logInput, setLogInput] = useState('');
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
    const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
    const [cveStats, setCveStats] = useState<CveStats | null>(null);
    const { addNotification } = useNotification();

    /* Fetch playbooks and CVE stats on mount */
    useEffect(() => {
        const load = async () => {
            try {
                const [pbs, stats] = await Promise.all([
                    securityApi.listPlaybooks(),
                    securityApi.getCveStats(),
                ]);
                setPlaybooks(pbs);
                setCveStats(stats);
            } catch (e) {
                console.error('Failed to load security data:', e);
            }
        };
        load();
    }, []);

    /* Analyze via backend M5 LLM agent (routes through Gemini with tool context) */
    const handleAnalyze = async () => {
        if (!logInput.trim()) return;
        setIsAnalyzing(true);
        setAnalysis(null);

        try {
            const prompt = `Analyze the following security log/event and provide a structured incident response plan. Return your analysis as JSON with these exact fields: summary (string), containment (string), remediation (string), escalation (string), severity (CRITICAL | HIGH | MEDIUM | LOW).

Security Log:
${logInput}`;

            const response = await llmApi.chat(prompt);

            // Parse the LLM response — try JSON first, fall back to structured extraction
            let result: AnalysisResult;
            try {
                // Try to extract JSON from the response
                const jsonMatch = response.content.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                    result = JSON.parse(jsonMatch[0]);
                } else {
                    // Build from raw text
                    result = {
                        summary: response.content.substring(0, 200),
                        containment: 'See full analysis above',
                        remediation: 'See full analysis above',
                        escalation: 'Review with security team',
                        severity: 'MEDIUM',
                    };
                }
            } catch {
                result = {
                    summary: response.content.substring(0, 300),
                    containment: 'Unable to parse structured response. Review raw analysis.',
                    remediation: response.content,
                    escalation: 'Escalate to security team for review',
                    severity: 'MEDIUM',
                };
            }

            setAnalysis(result);

            if (['CRITICAL', 'HIGH'].includes(result.severity.toUpperCase())) {
                addNotification(
                    `${result.severity} Threat Detected`,
                    result.summary.substring(0, 100) + '...',
                    result.severity.toUpperCase() === 'CRITICAL' ? 'error' : 'warning'
                );
            } else {
                addNotification('Analysis Complete', `Log analysis finished with severity: ${result.severity}`, 'success');
            }

        } catch (e) {
            console.error(e);
            addNotification('Analysis Failed', 'Could not process the security log. Ensure the backend is running.', 'error');
        } finally {
            setIsAnalyzing(false);
        }
    };

    return (
        <div className="h-full flex flex-col bg-base-200 p-4 gap-4 overflow-hidden">
            {/* Top bar — CVE stats + Playbooks */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* CVE Stats */}
                <div className="card bg-base-100 shadow-lg">
                    <div className="card-body p-4">
                        <h3 className="font-bold text-sm flex items-center gap-2 mb-2">
                            <AlertOctagon className="w-4 h-4 text-error" /> CVE Database
                        </h3>
                        {cveStats ? (
                            <div className="flex gap-4 text-sm">
                                <div className="text-center">
                                    <div className="text-2xl font-bold">{cveStats.total_cves}</div>
                                    <div className="text-xs opacity-60">Total CVEs</div>
                                </div>
                                <div className="divider divider-horizontal m-0"></div>
                                <div className="flex flex-col gap-1 text-xs">
                                    {Object.entries(cveStats.by_severity).map(([sev, count]) => (
                                        <span key={sev} className="flex justify-between gap-2">
                                            <span className={`badge badge-xs ${sev === 'critical' ? 'badge-error' : sev === 'high' ? 'badge-warning' : sev === 'medium' ? 'badge-info' : 'badge-ghost'}`}>{sev}</span>
                                            <span className="font-mono">{count}</span>
                                        </span>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <span className="loading loading-spinner loading-sm"></span>
                        )}
                    </div>
                </div>

                {/* Playbooks */}
                <div className="card bg-base-100 shadow-lg md:col-span-2">
                    <div className="card-body p-4">
                        <h3 className="font-bold text-sm flex items-center gap-2 mb-2">
                            <BookOpen className="w-4 h-4 text-secondary" /> Response Playbooks
                        </h3>
                        <div className="flex flex-wrap gap-2">
                            {playbooks.map(pb => (
                                <div key={pb.name} className="badge badge-outline gap-1 text-xs py-3">
                                    <Play className="w-3 h-3" />
                                    {pb.name.replace(/_/g, ' ')}
                                    <span className={`badge badge-xs ${pb.mode === 'auto' ? 'badge-success' : pb.mode === 'semi_auto' ? 'badge-warning' : 'badge-ghost'}`}>
                                        {pb.mode.replace('_', '-')}
                                    </span>
                                </div>
                            ))}
                            {playbooks.length === 0 && <span className="text-xs opacity-50">Loading...</span>}
                        </div>
                    </div>
                </div>
            </div>

            {/* Main analysis area */}
            <div className="flex-1 flex flex-col md:flex-row gap-4 min-h-0 overflow-hidden">
                {/* Input Panel */}
                <div className="flex-1 flex flex-col gap-4 min-w-0 overflow-y-auto">
                    <div className="card bg-base-100 shadow-xl h-full">
                        <div className="card-body p-6 flex flex-col">
                            <div className="flex items-center gap-2 mb-4 text-secondary">
                                <ScanSearch className="w-6 h-6" />
                                <h2 className="card-title">AI Incident Analysis</h2>
                            </div>
                            <p className="text-sm opacity-70 mb-2">Paste raw SIEM logs, Suricata alerts, or syslog entries for AI-driven threat assessment via the M5 agent.</p>

                            <div className="flex-1 relative">
                                <textarea
                                    className="textarea textarea-bordered font-mono text-xs w-full h-full resize-none leading-relaxed bg-base-300 text-base-content"
                                    value={logInput}
                                    onChange={(e) => setLogInput(e.target.value)}
                                    placeholder={`Paste security log here, e.g.:\n{\n  "event_type": "alert",\n  "src_ip": "192.168.45.5",\n  "dest_ip": "10.0.0.5",\n  "alert": {\n    "signature": "ET EXPLOIT SMB Double Pulsar Probe",\n    "severity": 1\n  }\n}`}
                                />
                            </div>

                            <div className="mt-4 flex justify-end">
                                <button
                                    className="btn btn-primary gap-2"
                                    onClick={handleAnalyze}
                                    disabled={isAnalyzing || !logInput.trim()}
                                >
                                    {isAnalyzing ? <span className="loading loading-spinner"></span> : <Sparkles className="w-4 h-4" />}
                                    Analyze Incident
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Output Panel */}
                <div className="flex-1 min-w-0 overflow-y-auto">
                    {analysis ? (
                        <div className="flex flex-col gap-4 animate-in fade-in slide-in-from-right-10 duration-500">
                            {/* Summary */}
                            <div className={`card bg-base-100 shadow-xl border-l-4 ${analysis.severity === 'CRITICAL' ? 'border-error' :
                                analysis.severity === 'HIGH' ? 'border-warning' : 'border-info'
                                }`}>
                                <div className="card-body p-5">
                                    <h3 className={`font-bold text-lg flex items-center gap-2 ${analysis.severity === 'CRITICAL' ? 'text-error' :
                                        analysis.severity === 'HIGH' ? 'text-warning' : 'text-info'
                                        }`}>
                                        {analysis.severity === 'CRITICAL' ? <AlertOctagon className="w-5 h-5" /> : <AlertTriangle className="w-5 h-5" />}
                                        {analysis.severity} Threat Detected
                                    </h3>
                                    <p className="text-sm mt-2">{analysis.summary}</p>
                                </div>
                            </div>

                            {/* Action Grid */}
                            <div className="grid grid-cols-1 gap-4">
                                <div className="card bg-base-100 shadow-lg">
                                    <div className="card-body p-4">
                                        <h4 className="font-bold text-sm text-warning uppercase tracking-wide mb-2 flex items-center gap-2">
                                            <Shield className="w-4 h-4" /> Containment Strategy
                                        </h4>
                                        <div className="bg-base-200 p-3 rounded-lg text-sm">
                                            {analysis.containment}
                                        </div>
                                    </div>
                                </div>

                                <div className="card bg-base-100 shadow-lg">
                                    <div className="card-body p-4">
                                        <h4 className="font-bold text-sm text-info uppercase tracking-wide mb-2 flex items-center gap-2">
                                            <Terminal className="w-4 h-4" /> Remediation Steps
                                        </h4>
                                        <div className="bg-base-200 p-3 rounded-lg text-sm">
                                            {analysis.remediation}
                                        </div>
                                    </div>
                                </div>

                                <div className="card bg-base-100 shadow-lg">
                                    <div className="card-body p-4">
                                        <h4 className="font-bold text-sm text-secondary uppercase tracking-wide mb-2 flex items-center gap-2">
                                            <ArrowRight className="w-4 h-4" /> Escalation Path
                                        </h4>
                                        <div className="bg-base-200 p-3 rounded-lg text-sm">
                                            {analysis.escalation}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <div className="h-full flex items-center justify-center opacity-30 flex-col gap-4">
                            <Shield className="w-24 h-24" />
                            <p>Ready to analyze security events</p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default SecurityAnalysis;
