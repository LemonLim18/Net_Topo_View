import React, { useState } from 'react';
import { GoogleGenAI, Type } from '@google/genai';
import { ScanSearch, Sparkles, AlertOctagon, Terminal, ArrowRight, CheckCircle, Shield, AlertTriangle } from 'lucide-react';
import { useNotification } from '../contexts/NotificationContext';

const MOCK_SIEM_LOG = `{
  "event_id": "INC-2024-8821",
  "timestamp": "2024-11-17T08:42:11Z",
  "source_ip": "192.168.45.5",
  "destination_ip": "10.0.0.5",
  "protocol": "TCP",
  "port": 445,
  "alert_signature": "ET EXPLOIT SMB Double Pulsar Probe",
  "severity": "CRITICAL",
  "payload_snippet": "00000000 00 00 00 2a ff 53 4d 42 72 00 00 00 00 08 01 c8 ..."
}`;

interface AnalysisResult {
    summary: string;
    containment: string;
    remediation: string;
    escalation: string;
    severity: string;
}

const SecurityAnalysis: React.FC = () => {
    const [logInput, setLogInput] = useState(MOCK_SIEM_LOG);
    const [isAnalyzing, setIsAnalyzing] = useState(false);
    const [analysis, setAnalysis] = useState<AnalysisResult | null>(null);
    const { addNotification } = useNotification();

    const handleAnalyze = async () => {
        setIsAnalyzing(true);
        setAnalysis(null);

        try {
             const ai = new GoogleGenAI({ apiKey: process.env.API_KEY as string });
             
             const prompt = `Analyze the following security log and provide a detailed incident response plan.
             
             Log Data:
             ${logInput}
             `;

             const response = await ai.models.generateContent({ 
                 model: 'gemini-2.5-flash', 
                 contents: prompt,
                 config: {
                     responseMimeType: "application/json",
                     responseSchema: {
                         type: Type.OBJECT,
                         properties: {
                             summary: { type: Type.STRING },
                             containment: { type: Type.STRING },
                             remediation: { type: Type.STRING },
                             escalation: { type: Type.STRING },
                             severity: { type: Type.STRING, enum: ["CRITICAL", "HIGH", "MEDIUM", "LOW"] }
                         },
                         required: ["summary", "containment", "remediation", "escalation", "severity"]
                     }
                 }
             });

             if (response.text) {
                 const result = JSON.parse(response.text) as AnalysisResult;
                 setAnalysis(result);

                 // Trigger Notification based on Severity
                 if (['CRITICAL', 'HIGH'].includes(result.severity.toUpperCase())) {
                     addNotification(
                         `${result.severity} Threat Detected`,
                         result.summary.substring(0, 100) + '...',
                         result.severity.toUpperCase() === 'CRITICAL' ? 'error' : 'warning'
                     );
                 } else {
                     addNotification(
                         `Analysis Complete`,
                         `Log analysis finished with severity: ${result.severity}`,
                         'success'
                     );
                 }
             }

        } catch (e) {
            console.error(e);
            addNotification("Analysis Failed", "Could not process the security log. Please check the input.", "error");
        } finally {
            setIsAnalyzing(false);
        }
    };

    return (
        <div className="h-full flex flex-col md:flex-row bg-base-200 p-4 gap-4 overflow-hidden">
            {/* Input Panel */}
            <div className="flex-1 flex flex-col gap-4 min-w-0 overflow-y-auto">
                <div className="card bg-base-100 shadow-xl h-full">
                    <div className="card-body p-6 flex flex-col">
                        <div className="flex items-center gap-2 mb-4 text-secondary">
                            <ScanSearch className="w-6 h-6" />
                            <h2 className="card-title">Incident Analysis</h2>
                        </div>
                        <p className="text-sm opacity-70 mb-2">Paste raw SIEM logs or JSON events below for AI-driven threat assessment.</p>
                        
                        <div className="flex-1 relative">
                            <textarea 
                                className="textarea textarea-bordered font-mono text-xs w-full h-full resize-none leading-relaxed bg-base-300 text-base-content"
                                value={logInput}
                                onChange={(e) => setLogInput(e.target.value)}
                            />
                        </div>

                        <div className="mt-4 flex justify-end">
                            <button 
                                className="btn btn-primary gap-2"
                                onClick={handleAnalyze}
                                disabled={isAnalyzing}
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
                         {/* High Level Summary */}
                        <div className={`card bg-base-100 shadow-xl border-l-4 ${
                            analysis.severity === 'CRITICAL' ? 'border-error' : 
                            analysis.severity === 'HIGH' ? 'border-warning' : 'border-info'
                        }`}>
                            <div className="card-body p-5">
                                <h3 className={`font-bold text-lg flex items-center gap-2 ${
                                    analysis.severity === 'CRITICAL' ? 'text-error' : 
                                    analysis.severity === 'HIGH' ? 'text-warning' : 'text-info'
                                }`}>
                                    {analysis.severity === 'CRITICAL' ? <AlertOctagon className="w-5 h-5" /> : <AlertTriangle className="w-5 h-5"/>}
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
                                        <Shield className="w-4 h-4"/> Containment Strategy
                                    </h4>
                                    <div className="bg-base-200 p-3 rounded-lg text-sm">
                                        {analysis.containment}
                                    </div>
                                    <div className="mt-3 flex gap-2">
                                        <button className="btn btn-xs btn-outline btn-warning">Generate Block Rule</button>
                                        <button className="btn btn-xs btn-outline">Quarantine Host</button>
                                    </div>
                                </div>
                            </div>

                            <div className="card bg-base-100 shadow-lg">
                                <div className="card-body p-4">
                                    <h4 className="font-bold text-sm text-info uppercase tracking-wide mb-2 flex items-center gap-2">
                                        <Terminal className="w-4 h-4"/> Remediation Steps
                                    </h4>
                                     <div className="bg-base-200 p-3 rounded-lg text-sm">
                                        {analysis.remediation}
                                    </div>
                                </div>
                            </div>
                            
                             <div className="card bg-base-100 shadow-lg">
                                <div className="card-body p-4">
                                    <h4 className="font-bold text-sm text-secondary uppercase tracking-wide mb-2 flex items-center gap-2">
                                        <ArrowRight className="w-4 h-4"/> Escalation Path
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
    );
};

export default SecurityAnalysis;
