/**
 * Domain configuration for repAIr
 */

export const DOMAIN = {
  id: "triagefix",
  name: "repAIr",
  description: "Graph-based assistant for housing maintenance incident triage",
  tagline: "Housing maintenance incident triage",
};

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

export const NODE_COLORS: Record<string, string> = {
  Incident: "#e11d48",
  PropertyContext: "#0ea5e9",
  AreaCluster: "#0284c7",
  Category: "#7c3aed",
  Subcategory: "#a855f7",
  Urgency: "#f97316",
  Status: "#10b981",
  TradeSpecialist: "#14b8a6",
  Renovator: "#0f766e",
  RecommendedAction: "#6366f1",
  Evidence: "#f59e0b",
  MissingQuestion: "#ef4444",
  SeveritySignal: "#dc2626",
  ResolutionTimeBand: "#84cc16",
  HistoricalCase: "#64748b",
};

export const NODE_SIZES: Record<string, number> = {
  Incident: 32,
  PropertyContext: 24,
  AreaCluster: 20,
  Category: 22,
  Subcategory: 18,
  Urgency: 18,
  Status: 18,
  TradeSpecialist: 20,
  Renovator: 20,
  RecommendedAction: 20,
  Evidence: 18,
  MissingQuestion: 16,
  SeveritySignal: 16,
  ResolutionTimeBand: 16,
  HistoricalCase: 18,
};

export const DEFAULT_CYPHER = `
MATCH (i:Incident:TriageFixManaged)-[r]-(n:TriageFixManaged)
RETURN i, r, n
LIMIT 120
`;

export const SCHEMA_NODE_SIZE = 30;
export const SCHEMA_REL_COLOR = "#94a3b8";

export interface GraphData {
  results: Record<string, unknown>[];
}

export interface DemoScenario {
  name: string;
  prompts: string[];
}

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    name: "Incident triage",
    prompts: [
      "Show the highest severity incidents",
      "Explain this incident using graph context",
      "What information is missing before routing this incident?",
    ],
  },
  {
    name: "Historical context",
    prompts: [
      "Which prior similar incidents are connected?",
      "Which renovator handled similar cases?",
      "What is the recommended action and why?",
    ],
  },
  {
    name: "Evidence and severity",
    prompts: [
      "Show evidence and severity signals for this incident",
    ],
  },
];
