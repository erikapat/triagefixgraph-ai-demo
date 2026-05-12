"use client";

import { useEffect, useCallback, useMemo, useState, useRef } from "react";
import {
  Box,
  Button,
  Text,
  Flex,
  Badge,
  VStack,
  HStack,
  Heading,
  IconButton,
  Spinner,
  Input,
} from "@chakra-ui/react";
import { X, RotateCcw } from "lucide-react";
import {
  NODE_COLORS,
  NODE_SIZES,
  SCHEMA_NODE_SIZE,
  SCHEMA_REL_COLOR,
  API_BASE,
} from "@/lib/config";
import type { GraphData } from "@/lib/config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

interface GraphRelationship {
  id: string;
  type: string;
  startNodeId: string;
  endNodeId: string;
  properties: Record<string, unknown>;
}

interface InternalGraphData {
  nodes: GraphNode[];
  relationships: GraphRelationship[];
}
interface ExpansionRecord {
  addedNodeIds: string[];
  addedRelationshipIds: string[];
}

interface NvlNode {
  id: string;
  caption?: string;
  color?: string;
  size?: number;
  selected?: boolean;
}

interface NvlRelationship {
  id: string;
  from: string;
  to: string;
  caption?: string;
  color?: string;
  selected?: boolean;
}

interface SelectedElement {
  type: "node" | "relationship";
  data: GraphNode | GraphRelationship;
}

interface ContextGraphViewProps {
  externalGraphData?: GraphData | null;
  selectedIncidentId?: string | null;
  onSelectedIncidentChange?: (incidentId: string | null) => void;
  onAskAbout?: (entityName: string) => void;
}

const TECHNICAL_LABELS = new Set(["TriageFixManaged"]);
const DEFAULT_INCIDENT_ID = "rec7i2cq4iVkb0eTy";
const FILTERABLE_FIELDS = [
  { key: "incident_id", label: "Incident" },
  { key: "property_context_id", label: "PropertyContext" },
  { key: "area_cluster", label: "AreaCluster" },
  { key: "category", label: "Category" },
  { key: "subcategory", label: "Subcategory" },
  { key: "urgency", label: "Urgency" },
  { key: "status", label: "Status" },
  { key: "trade_specialist", label: "TradeSpecialist" },
  { key: "renovator", label: "Renovator" },
  { key: "resolution_time_band", label: "ResolutionTimeBand" },
] as const;

// ---------------------------------------------------------------------------
// Helpers — convert backend serialized data to internal format
// ---------------------------------------------------------------------------

function extractNodesAndRels(results: Record<string, unknown>[]): InternalGraphData {
  const nodeMap = new Map<string, GraphNode>();
  const relMap = new Map<string, GraphRelationship>();

  function processValue(value: unknown, depth = 0) {
    if (!value || typeof value !== "object" || depth > 10) return;

    if (Array.isArray(value)) {
      for (const item of value) processValue(item, depth + 1);
      return;
    }

    const v = value as Record<string, unknown>;

    // Node: Neo4j serialized node (labels + elementId)
    if (Array.isArray(v.labels) && v.elementId) {
      const id = String(v.elementId);
      if (!nodeMap.has(id)) {
        const sanitizedLabels = (v.labels as string[]).filter(
          (label) => !TECHNICAL_LABELS.has(label),
        );
        const { elementId, labels, ...props } = v;
        nodeMap.set(id, {
          id,
          labels: sanitizedLabels.length > 0 ? sanitizedLabels : ["Node"],
          properties: props,
        });
      }
      return;
    }

    // Node: incident context scalar map (id + label + properties)
    if (v.id && (v.label || v.properties)) {
      const id = String(v.id);
      if (!nodeMap.has(id)) {
        const label = v.label ? String(v.label) : "Node";
        const props = (v.properties && typeof v.properties === "object")
          ? (v.properties as Record<string, unknown>)
          : {};
        nodeMap.set(id, {
          id,
          labels: [label],
          properties: {
            ...props,
            title: v.title ?? props.title,
          },
        });
      }
      return;
    }

    // Relationship: Neo4j serialized or incident context scalar map
    if (v.type && (v.startNodeElementId || (v.source && v.target))) {
      const id = String(v.elementId || v.id || Math.random());
      if (!relMap.has(id)) {
        const { elementId, type, startNodeElementId, endNodeElementId, source, target, ...props } = v;
        relMap.set(id, {
          id,
          type: String(type),
          startNodeId: String(startNodeElementId ?? source),
          endNodeId: String(endNodeElementId ?? target),
          properties: props,
        });
      }
      return;
    }

    // Path: has nodes + relationships arrays
    if (Array.isArray(v.nodes) && Array.isArray(v.relationships)) {
      for (const n of v.nodes) processValue(n, depth + 1);
      for (const r of v.relationships) processValue(r, depth + 1);
      return;
    }

    // Recurse into nested objects
    for (const val of Object.values(v)) {
      processValue(val, depth + 1);
    }
  }

  for (const record of results) {
    for (const value of Object.values(record)) {
      processValue(value);
    }
  }

  return {
    nodes: Array.from(nodeMap.values()),
    relationships: Array.from(relMap.values()),
  };
}

function getNodeColor(labels: string[]): string {
  for (const label of labels) {
    if (NODE_COLORS[label]) return NODE_COLORS[label];
  }
  return "#6366f1";
}

function getNodeSize(labels: string[]): number {
  for (const label of labels) {
    if (NODE_SIZES[label]) return NODE_SIZES[label];
  }
  return 20;
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function ContextGraphView({
  externalGraphData,
  selectedIncidentId,
  onSelectedIncidentChange,
  onAskAbout,
}: ContextGraphViewProps) {
  const [isSchemaView, setIsSchemaView] = useState(false);
  const [loading, setLoading] = useState(false);
  const [isExpanding, setIsExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedElement, setSelectedElement] = useState<SelectedElement | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedRelId, setSelectedRelId] = useState<string | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Record<string, ExpansionRecord>>({});
  const [baseIncidentGraph, setBaseIncidentGraph] = useState<InternalGraphData | null>(null);
  const [graphData, setGraphData] = useState<InternalGraphData | null>(null);
  const [incidents, setIncidents] = useState<Record<string, unknown>[]>([]);
  const [incidentInput, setIncidentInput] = useState("");
  const [incidentContext, setIncidentContext] = useState<Record<string, unknown> | null>(null);
  const [filterField, setFilterField] = useState<(typeof FILTERABLE_FIELDS)[number]["key"]>("incident_id");
  const [filterValue, setFilterValue] = useState("");
  const [focusMode, setFocusMode] = useState<
    "decision_context" | "incident_links_only" | "missing_questions" | "severity_signals" | "all_context"
  >("decision_context");

  // Load incidents on mount
  useEffect(() => {
    loadIncidents();
  }, []);

  // When external graph data arrives from chat, switch to data view
  useEffect(() => {
    if (externalGraphData?.results?.length) {
      const data = extractNodesAndRels(externalGraphData.results);
      if (data.nodes.length > 0) {
        setGraphData(data);
        setIsSchemaView(false);
        setExpandedNodes({});
        setSelectedElement(null);
        setSelectedNodeId(null);
        setSelectedRelId(null);
      }
    }
  }, [externalGraphData]);

  useEffect(() => {
    if (selectedIncidentId && selectedIncidentId !== incidentInput) {
      // Keep chat-driven incident selection in sync with controls.
      // Force Incident filter so the selected id is not overwritten by
      // a previous field filter (e.g., Renovator).
      setFilterField("incident_id");
      setFilterValue(selectedIncidentId);
      setIncidentInput(selectedIncidentId);
      loadIncidentContext(selectedIncidentId);
    }
  }, [selectedIncidentId]);

  const availableFilterValues = useMemo(() => {
    const values = new Set<string>();
    for (const row of incidents) {
      const raw = String(row[filterField] ?? "").trim();
      if (raw) values.add(raw);
    }
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [incidents, filterField]);

  const filteredIncidents = useMemo(() => {
    const needle = filterValue.trim().toLowerCase();
    if (!needle) return incidents;
    return incidents.filter((row) =>
      String(row[filterField] ?? "").trim().toLowerCase().includes(needle)
    );
  }, [incidents, filterField, filterValue]);

  useEffect(() => {
    const currentInFiltered = filteredIncidents.some(
      (row) => String(row.incident_id ?? "").trim() === incidentInput.trim()
    );
    if (incidentInput.trim() && currentInFiltered) {
      return;
    }

    const firstFilteredId = String(filteredIncidents[0]?.incident_id ?? "").trim();
    setIncidentInput(firstFilteredId || "");
  }, [filteredIncidents]);

  async function loadIncidents() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/triagefix/incidents`, { signal: AbortSignal.timeout(10000) });
      const data = await res.json();
      const rows = data.incidents || [];
      setIncidents(rows);
      const preferredId =
        (rows.find((r: Record<string, unknown>) => String(r.incident_id || "") === DEFAULT_INCIDENT_ID)
          ?.incident_id as string | undefined) ||
        (rows[0]?.incident_id as string | undefined);
      if (preferredId) {
        setIncidentInput(preferredId);
        onSelectedIncidentChange?.(preferredId);
        await loadIncidentContext(preferredId);
      }
    } catch {
      setError("Unable to load incidents list. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  async function loadIncidentContext(incidentId: string) {
    if (!incidentId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/triagefix/incidents/${encodeURIComponent(incidentId)}/context`, {
        signal: AbortSignal.timeout(12000),
      });
      if (!res.ok) throw new Error("incident not found");
      const payload = await res.json();
      setIncidentContext(payload.context || null);
      onSelectedIncidentChange?.(incidentId);
      const graph = payload.graph || {};
      const nodes = graph.nodes ?? [];
      const relationships = graph.relationships ?? graph.edges ?? [];
      console.log("incident graph", nodes.length, relationships.length);
      const parsed = extractNodesAndRels([{ nodes, relationships }]);
      setGraphData(parsed);
      setBaseIncidentGraph(parsed);
      setIsSchemaView(false);
      setExpandedNodes({});
      setFocusMode("decision_context");
      setSelectedElement(null);
      setSelectedNodeId(null);
      setSelectedRelId(null);
    } catch {
      setError("Unable to load selected incident context.");
    } finally {
      setLoading(false);
    }
  }

  async function loadSchema() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/schema/visualization`, { signal: AbortSignal.timeout(10000) });
      const data = await res.json();
      if (data.nodes && data.relationships) {
        // db.schema.visualization() returns serialized Node/Relationship objects
        const schemaData = extractNodesAndRels([data]);
        setGraphData(schemaData);
      } else if (data.labels) {
        // Fallback: basic schema — create synthetic nodes for labels
        const nodes: GraphNode[] = data.labels.map((label: string, i: number) => ({
          id: `schema-${label}`,
          labels: [label],
          properties: { name: label, isSchemaNode: true },
        }));
        setGraphData({ nodes, relationships: [] });
      }
      setIsSchemaView(true);
      setExpandedNodes({});
      setSelectedElement(null);
    } catch {
      setError("Unable to load schema. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  // Double-click: expand node (schema → load label instances, data → expand neighbors)
  const handleNodeDoubleClick = useCallback(
    async (node: NvlNode) => {
      if (!graphData || isExpanding) return;

      if (isSchemaView) {
        // Schema node: load instances of this label
        const label = node.caption?.replace(/\s*\(\d+\)$/, "");
        if (!label) return;
        setLoading(true);
        try {
          const res = await fetch(`${API_BASE}/cypher`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              query: `MATCH (n:TriageFixManaged:\`${label}\`)-[r]-(m:TriageFixManaged) RETURN n, r, m LIMIT 50`,
            }),
            signal: AbortSignal.timeout(10000),
          });
          const data = await res.json();
          const parsed = extractNodesAndRels(data.results || []);
          if (parsed.nodes.length > 0) {
            setGraphData(parsed);
            setIsSchemaView(false);
            setExpandedNodes({});
          }
        } catch (err) {
          console.error("Error loading label data:", err);
        } finally {
          setLoading(false);
        }
        return;
      }

      // Data node: toggle expand/collapse
      if (expandedNodes[node.id]) {
        const current = expandedNodes[node.id];
        const otherNodeIds = Object.entries(expandedNodes)
          .filter(([id]) => id !== node.id)
          .flatMap(([, rec]) => rec.addedNodeIds);
        const otherRelIds = Object.entries(expandedNodes)
          .filter(([id]) => id !== node.id)
          .flatMap(([, rec]) => rec.addedRelationshipIds);

        const otherNodeSet = new Set(otherNodeIds);
        const otherRelSet = new Set(otherRelIds);
        const baseNodeSet = new Set((baseIncidentGraph?.nodes ?? []).map((n) => n.id));
        const baseRelSet = new Set((baseIncidentGraph?.relationships ?? []).map((r) => r.id));

        setGraphData({
          nodes: graphData.nodes.filter(
            (n) =>
              !current.addedNodeIds.includes(n.id) ||
              otherNodeSet.has(n.id) ||
              baseNodeSet.has(n.id),
          ),
          relationships: graphData.relationships.filter(
            (r) =>
              !current.addedRelationshipIds.includes(r.id) ||
              otherRelSet.has(r.id) ||
              baseRelSet.has(r.id),
          ),
        });

        setExpandedNodes((prev) => {
          const next = { ...prev };
          delete next[node.id];
          return next;
        });
        return;
      }
      setIsExpanding(true);

      try {
        const res = await fetch(`${API_BASE}/expand`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ element_id: node.id }),
          signal: AbortSignal.timeout(10000),
        });
        const data = await res.json();
        const expanded = extractNodesAndRels([data]);

        if (expanded.nodes.length === 0) return;

        // Merge, deduplicating
        const existingNodeIds = new Set(graphData.nodes.map((n) => n.id));
        const existingRelIds = new Set(graphData.relationships.map((r) => r.id));
        const newNodes = expanded.nodes.filter((n) => !existingNodeIds.has(n.id));
        const newRels = expanded.relationships.filter((r) => !existingRelIds.has(r.id));

        setGraphData({
          nodes: [...graphData.nodes, ...newNodes],
          relationships: [...graphData.relationships, ...newRels],
        });
        setExpandedNodes((prev) => ({
          ...prev,
          [node.id]: {
            addedNodeIds: newNodes.map((n) => n.id),
            addedRelationshipIds: newRels.map((r) => r.id),
          },
        }));
      } catch (err) {
        console.error("Error expanding node:", err);
      } finally {
        setIsExpanding(false);
      }
    },
    [graphData, isSchemaView, isExpanding, expandedNodes, baseIncidentGraph],
  );

  // Click: select node and show properties
  const handleNodeClick = useCallback(
    (node: NvlNode) => {
      if (!graphData) return;
      const original = graphData.nodes.find((n) => n.id === node.id);
      if (original) {
        setSelectedElement({ type: "node", data: original });
        setSelectedNodeId(node.id);
        setSelectedRelId(null);
      }
    },
    [graphData],
  );

  // Click relationship
  const handleRelationshipClick = useCallback(
    (rel: NvlRelationship) => {
      if (!graphData) return;
      const original = graphData.relationships.find((r) => r.id === rel.id);
      if (original) {
        setSelectedElement({ type: "relationship", data: original });
        setSelectedRelId(rel.id);
        setSelectedNodeId(null);
      }
    },
    [graphData],
  );

  // Click canvas: deselect
  const handleCanvasClick = useCallback(() => {
    setSelectedElement(null);
    setSelectedNodeId(null);
    setSelectedRelId(null);
  }, []);

  const filteredGraphData = useMemo((): InternalGraphData | null => {
    if (!graphData) return null;
    if (isSchemaView) return graphData;
    if (focusMode === "all_context") return graphData;

    const nodeById = new Map(graphData.nodes.map((n) => [n.id, n]));
    const selectedIncidentNodeId =
      graphData.nodes.find(
        (n) =>
          n.labels.includes("Incident") &&
          String((n.properties.incident_id as string | undefined) ?? "") === (selectedIncidentId || ""),
      )?.id ?? null;
    if (!selectedIncidentNodeId) return graphData;

    const keepNodeIds = new Set<string>([selectedIncidentNodeId]);
    const keepRelIds = new Set<string>();

    if (focusMode === "decision_context") {
      const contextLabels = new Set([
        "Incident",
        "PropertyContext",
        "AreaCluster",
        "Category",
        "Subcategory",
        "Urgency",
        "Status",
        "RecommendedAction",
        "TradeSpecialist",
        "Renovator",
        "Evidence",
      ]);
      for (const n of graphData.nodes) {
        if (contextLabels.has(n.labels[0])) keepNodeIds.add(n.id);
      }
      for (const r of graphData.relationships) {
        if ((r.type === "PRIOR_SIMILAR" || r.type === "SIMILAR_TO") &&
            (r.startNodeId === selectedIncidentNodeId || r.endNodeId === selectedIncidentNodeId)) {
          keepNodeIds.add(r.startNodeId);
          keepNodeIds.add(r.endNodeId);
          keepRelIds.add(r.id);
        }
      }
      for (const r of graphData.relationships) {
        if (keepNodeIds.has(r.startNodeId) && keepNodeIds.has(r.endNodeId)) keepRelIds.add(r.id);
      }
    } else if (focusMode === "incident_links_only") {
      for (const r of graphData.relationships) {
        if (r.type !== "PRIOR_SIMILAR" && r.type !== "SIMILAR_TO") continue;
        const a = nodeById.get(r.startNodeId);
        const b = nodeById.get(r.endNodeId);
        if (!a || !b) continue;
        if (!a.labels.includes("Incident") || !b.labels.includes("Incident")) continue;
        keepNodeIds.add(a.id);
        keepNodeIds.add(b.id);
        keepRelIds.add(r.id);
      }
    } else if (focusMode === "missing_questions") {
      for (const r of graphData.relationships) {
        if (r.type !== "NEEDS_QUESTION") continue;
        if (r.startNodeId !== selectedIncidentNodeId && r.endNodeId !== selectedIncidentNodeId) continue;
        keepNodeIds.add(r.startNodeId);
        keepNodeIds.add(r.endNodeId);
        keepRelIds.add(r.id);
      }
    } else if (focusMode === "severity_signals") {
      for (const r of graphData.relationships) {
        if (r.type !== "HAS_SEVERITY_SIGNAL") continue;
        if (r.startNodeId !== selectedIncidentNodeId && r.endNodeId !== selectedIncidentNodeId) continue;
        keepNodeIds.add(r.startNodeId);
        keepNodeIds.add(r.endNodeId);
        keepRelIds.add(r.id);
      }
    }

    return {
      nodes: graphData.nodes.filter((n) => keepNodeIds.has(n.id)),
      relationships: graphData.relationships.filter(
        (r) => keepRelIds.has(r.id) && keepNodeIds.has(r.startNodeId) && keepNodeIds.has(r.endNodeId),
      ),
    };
  }, [graphData, isSchemaView, focusMode, selectedIncidentId]);

  // Transform to NVL format
  const nvlData = useMemo(() => {
    if (!filteredGraphData) return { nodes: [], relationships: [] };

    const nodes: NvlNode[] = filteredGraphData.nodes.map((node) => {
      const isSelected = selectedNodeId === node.id;
      const isSelectedIncident =
        !!selectedIncidentId &&
        node.labels.includes("Incident") &&
        String((node.properties.incident_id as string | undefined) ?? "") === selectedIncidentId;
      const isExpanded = !!expandedNodes[node.id];
      const isSchema = isSchemaView;

      const caption =
        (node.properties.name as string) ||
        (node.properties.title as string) ||
        node.labels[0] ||
        node.id.slice(0, 8);

      // Build tooltip with full name and labels
      const tooltip = [
        caption,
        `Labels: ${node.labels.join(", ")}`,
        ...Object.entries(node.properties)
          .filter(([k]) => k !== "name" && k !== "title")
          .slice(0, 5)
          .map(([k, v]) => `${k}: ${v}`),
      ].join("\n");

      return {
        id: node.id,
        caption,
        title: tooltip,
        color: isSelected
          ? "#E53E3E"
          : isSelectedIncident
            ? "#C53030"
          : isExpanded
            ? "#38A169"
            : getNodeColor(node.labels),
        size: isSchema
          ? SCHEMA_NODE_SIZE
          : isSelected
            ? getNodeSize(node.labels) * 1.3
            : isSelectedIncident
              ? getNodeSize(node.labels) * 1.35
            : getNodeSize(node.labels),
        selected: isSelected,
      };
    });

    const relationships: NvlRelationship[] = filteredGraphData.relationships.map((rel) => {
      const isSelected = selectedRelId === rel.id;
      return {
        id: rel.id,
        from: rel.startNodeId,
        to: rel.endNodeId,
        caption: rel.type,
        color: isSelected ? "#E53E3E" : isSchemaView ? SCHEMA_REL_COLOR : "#A0AEC0",
        selected: isSelected,
      };
    });

    return { nodes, relationships };
  }, [filteredGraphData, selectedNodeId, selectedRelId, expandedNodes, isSchemaView, selectedIncidentId]);

  // Empty / error states
  if (error) {
    return (
      <Flex h="100%" align="center" justify="center">
        <Text color="gray.500">{error}</Text>
      </Flex>
    );
  }

  return (
    <Box h="100%" position="relative">
      {/* Header bar */}
      <Flex
        position="absolute"
        top={0}
        left={0}
        right={0}
        zIndex={10}
        px={4}
        py={2}
        bg="white"
        borderBottom="1px solid"
        borderColor="gray.200"
        justify="space-between"
        align="center"
      >
        <Box>
          <Heading size="sm">Knowledge Graph</Heading>
          <Text fontSize="xs" color="gray.500">
            {isSchemaView
              ? "Schema view — double-click a label to explore"
              : "Incident view — local context for selected incident"}
          </Text>
        </Box>
        <HStack gap={2}>
          <Button
            size="xs"
            variant={isSchemaView ? "outline" : "solid"}
            onClick={() => setIsSchemaView(false)}
          >
            Incident view
          </Button>
          <Button
            size="xs"
            variant={isSchemaView ? "solid" : "outline"}
            onClick={loadSchema}
          >
            Schema view
          </Button>
          <IconButton aria-label="Reload incidents" size="xs" variant="ghost" onClick={loadIncidents}>
            <RotateCcw size={14} />
          </IconButton>
          {!isSchemaView && (
            <Button
              size="xs"
              variant="outline"
              onClick={() => {
                if (baseIncidentGraph) {
                  setGraphData(baseIncidentGraph);
                  setExpandedNodes({});
                  setFocusMode("decision_context");
                  setSelectedElement(null);
                  setSelectedNodeId(null);
                  setSelectedRelId(null);
                }
              }}
            >
              Reset view
            </Button>
          )}
        </HStack>
      </Flex>

      {!isSchemaView && (
        <Box position="absolute" top="52px" left={2} right={2} zIndex={10} bg="white" borderWidth="1px" borderColor="gray.200" borderRadius="md" p={2} boxShadow="sm">
          <VStack align="stretch" gap={2}>
            <HStack gap={2}>
              <select
                value={filterField}
                onChange={(e) => {
                  const nextField = e.currentTarget.value as (typeof FILTERABLE_FIELDS)[number]["key"];
                  setFilterField(nextField);
                  setFilterValue("");
                }}
                style={{
                  flex: 1,
                  border: "1px solid #e2e8f0",
                  borderRadius: "0.375rem",
                  padding: "0.25rem 0.5rem",
                  minHeight: "32px",
                  background: "white",
                }}
              >
                {FILTERABLE_FIELDS.map((field) => (
                  <option key={field.key} value={field.key}>
                    {field.label}
                  </option>
                ))}
              </select>
              <Input
                size="sm"
                value={filterValue}
                onChange={(e) => setFilterValue(e.target.value)}
                placeholder={`Type ${FILTERABLE_FIELDS.find((f) => f.key === filterField)?.label || "value"}...`}
                list="triagefix-filter-values"
                maxW="460px"
              />
              <datalist id="triagefix-filter-values">
                <option value="">(all)</option>
                {availableFilterValues.map((value) => (
                  <option key={value} value={value} />
                ))}
              </datalist>
              <Button
                size="sm"
                disabled={!incidentInput}
                onClick={async () => {
                  await loadIncidentContext(incidentInput.trim());
                }}
              >
                Load
              </Button>
            </HStack>
            <HStack gap={2} flexWrap="wrap">
              <Badge>Matches: {filteredIncidents.length}</Badge>
              <Badge>Filter value: {filterValue || "(all)"}</Badge>
              <Badge>Selected incident: {incidentInput || "none"}</Badge>
            </HStack>
            {incidentContext && (
              <HStack gap={2} flexWrap="wrap">
                <Badge>Severity: {String(incidentContext.severity_average ?? "—")}</Badge>
                <Badge>Category: {String(incidentContext.category ?? "—")}</Badge>
                <Badge>Urgency: {String(incidentContext.urgency ?? "—")}</Badge>
                <Badge>Action: {String(incidentContext.recommended_action ?? "—")}</Badge>
                <Badge>Provider: {String(incidentContext.renovator ?? "—")}</Badge>
                <Badge>Docs: {String(incidentContext.has_incidence_docs ?? false)}</Badge>
                <Badge>Missing Q: {Array.isArray(incidentContext.missing_questions) ? incidentContext.missing_questions.length : 0}</Badge>
                <Badge>Prior Similar: {Array.isArray(incidentContext.prior_similar_incidents) ? incidentContext.prior_similar_incidents.length : 0}</Badge>
              </HStack>
            )}
            <HStack gap={2} flexWrap="wrap">
              <Button size="xs" variant={focusMode === "decision_context" ? "solid" : "outline"} onClick={() => setFocusMode("decision_context")}>
                Decision context
              </Button>
              <Button size="xs" variant={focusMode === "incident_links_only" ? "solid" : "outline"} onClick={() => setFocusMode("incident_links_only")}>
                Incident links only
              </Button>
              <Button size="xs" variant={focusMode === "missing_questions" ? "solid" : "outline"} onClick={() => setFocusMode("missing_questions")}>
                Missing questions
              </Button>
              <Button size="xs" variant={focusMode === "severity_signals" ? "solid" : "outline"} onClick={() => setFocusMode("severity_signals")}>
                Severity signals
              </Button>
              <Button size="xs" variant={focusMode === "all_context" ? "solid" : "outline"} onClick={() => setFocusMode("all_context")}>
                All context
              </Button>
            </HStack>
          </VStack>
        </Box>
      )}

      {/* Legend */}
      <Flex
        position="absolute"
        bottom={2}
        right={2}
        zIndex={10}
        bg="white"
        borderRadius="md"
        p={2}
        gap={2}
        flexWrap="wrap"
        maxW="220px"
        maxH="220px"
        overflowY="auto"
        css={{ "&::-webkit-scrollbar": { width: "4px" }, "&::-webkit-scrollbar-thumb": { background: "#CBD5E0", borderRadius: "4px" } }}
        boxShadow="sm"
        borderWidth="1px"
        borderColor="gray.200"
      >
        {Object.entries(NODE_COLORS)
          .map(([label, color]) => (
            <Badge
              key={label}
              size="sm"
              style={{ backgroundColor: color, color: "white" }}
            >
              {label}
            </Badge>
          ))}
      </Flex>

      {/* Properties panel */}
      {selectedElement && (
        <Box
          position="absolute"
          top={isSchemaView ? "52px" : "140px"}
          right={2}
          zIndex={10}
          bg="white"
          borderRadius="md"
          p={3}
          maxW="300px"
          maxH="calc(100% - 80px)"
          overflow="auto"
          boxShadow="md"
          borderWidth="1px"
          borderColor="gray.200"
        >
          <Flex justify="space-between" align="center" mb={2}>
            <Heading size="sm">
              {selectedElement.type === "node" ? "Node" : "Relationship"} Properties
            </Heading>
            <IconButton
              aria-label="Close"
              size="xs"
              variant="ghost"
              onClick={handleCanvasClick}
            >
              <X size={14} />
            </IconButton>
          </Flex>

          {selectedElement.type === "node" && (
            <VStack align="stretch" gap={2}>
              <HStack flexWrap="wrap" gap={1}>
                <Text fontSize="xs" fontWeight="bold" color="gray.500">
                  Labels:
                </Text>
                {(selectedElement.data as GraphNode).labels.map((label) => (
                  <Badge
                    key={label}
                    size="sm"
                    style={{
                      backgroundColor: NODE_COLORS[label] || "#718096",
                      color: "white",
                    }}
                  >
                    {label}
                  </Badge>
                ))}
              </HStack>
              {onAskAbout && typeof (selectedElement.data as GraphNode).properties.name === "string" && (
                <Button
                  size="xs"
                  colorPalette="blue"
                  variant="outline"
                  onClick={() => {
                    const name = (selectedElement.data as GraphNode).properties.name as string;
                    onAskAbout(name);
                  }}
                >
                  Ask about {((selectedElement.data as GraphNode).properties.name as string).slice(0, 30)}
                </Button>
              )}
              <VStack align="stretch" gap={1}>
                {Object.entries((selectedElement.data as GraphNode).properties)
                  .filter(([key]) => !key.startsWith("_") && key !== "isSchemaNode" && key !== "embedding")
                  .map(([key, value]) => (
                    <Box key={key} bg="gray.50" p={1} borderRadius="sm" fontSize="xs">
                      <Text fontWeight="medium" color="gray.600">
                        {key.replace(/_/g, " ")}
                      </Text>
                      <Text color="gray.800" wordBreak="break-word" whiteSpace="pre-wrap">
                        {typeof value === "object"
                          ? JSON.stringify(value, null, 2)
                          : String(value ?? "—")}
                      </Text>
                    </Box>
                  ))}
              </VStack>
            </VStack>
          )}

          {selectedElement.type === "relationship" && (
            <VStack align="stretch" gap={2}>
              <HStack>
                <Text fontSize="xs" fontWeight="bold" color="gray.500">
                  Type:
                </Text>
                <Badge size="sm" colorPalette="gray">
                  {(selectedElement.data as GraphRelationship).type}
                </Badge>
              </HStack>
              {Object.keys((selectedElement.data as GraphRelationship).properties).length > 0 && (
                <VStack align="stretch" gap={1}>
                  {Object.entries((selectedElement.data as GraphRelationship).properties).map(
                    ([key, value]) => (
                      <Box key={key} bg="gray.50" p={1} borderRadius="sm" fontSize="xs">
                        <Text fontWeight="medium" color="gray.600">
                          {key.replace(/_/g, " ")}
                        </Text>
                        <Text color="gray.800" wordBreak="break-word">
                          {typeof value === "object"
                            ? JSON.stringify(value, null, 2)
                            : String(value ?? "—")}
                        </Text>
                      </Box>
                    ),
                  )}
                </VStack>
              )}
            </VStack>
          )}
        </Box>
      )}

      {/* Instructions */}
      <Box
        position="absolute"
        bottom={2}
        left={2}
        zIndex={10}
        bg="white"
        borderRadius="md"
        px={2}
        py={1}
        boxShadow="sm"
        borderWidth="1px"
        borderColor="gray.200"
        opacity={0.8}
      >
        <Text fontSize="xs" color="gray.500">
          Scroll to zoom | Drag to pan | Click to inspect | Double-click to expand/collapse
        </Text>
      </Box>

      {/* Loading overlay */}
      {(loading || isExpanding) && (
        <Flex
          position="absolute"
          top="50%"
          left="50%"
          transform="translate(-50%, -50%)"
          zIndex={20}
          bg="white"
          borderRadius="md"
          p={3}
          boxShadow="md"
          borderWidth="1px"
          borderColor="gray.200"
          align="center"
          gap={2}
        >
          <Spinner size="sm" />
          <Text fontSize="sm">{isExpanding ? "Expanding node..." : "Loading..."}</Text>
        </Flex>
      )}

      {/* Graph */}
      <Box h="100%" w="100%" pt="48px">
        {nvlData.nodes.length > 0 ? (
          <NvlGraph
            nodes={nvlData.nodes}
            relationships={nvlData.relationships}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
            onRelationshipClick={handleRelationshipClick}
            onCanvasClick={handleCanvasClick}
          />
        ) : (
          <Flex h="100%" align="center" justify="center" direction="column" gap={4} p={8}>
            <Box color="gray.400" fontSize="4xl">🔗</Box>
            <Text color="gray.400" fontWeight="medium" fontSize="lg">
              Your TriageFix graph will appear here
            </Text>
            <Text color="gray.500" fontSize="sm" textAlign="center" maxW="300px">
              Ask a question in the chat, or double-click a node in schema view to explore incident context.
            </Text>
          </Flex>
        )}
      </Box>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// NvlGraph — handles dynamic NVL import (avoids SSR issues)
// ---------------------------------------------------------------------------

function NvlGraph({
  nodes,
  relationships,
  onNodeClick,
  onNodeDoubleClick,
  onRelationshipClick,
  onCanvasClick,
}: {
  nodes: NvlNode[];
  relationships: NvlRelationship[];
  onNodeClick: (node: NvlNode) => void;
  onNodeDoubleClick: (node: NvlNode) => void;
  onRelationshipClick: (rel: NvlRelationship) => void;
  onCanvasClick: () => void;
}) {
  /* eslint-disable @typescript-eslint/no-explicit-any */
  const [NvlComponent, setNvlComponent] = useState<React.ComponentType<any> | null>(null);
  const [isReady, setIsReady] = useState(false);
  const nvlRef = useRef<any>(null);

  useEffect(() => {
    import("@neo4j-nvl/react").then((mod) => {
      setNvlComponent(() => mod.InteractiveNvlWrapper);
    });
  }, []);

  useEffect(() => {
    if (NvlComponent && nodes.length > 0) {
      const timer = setTimeout(() => setIsReady(true), 100);
      return () => clearTimeout(timer);
    }
  }, [NvlComponent, nodes.length]);

  if (!NvlComponent) {
    return (
      <Flex h="100%" align="center" justify="center">
        <Text color="gray.500">Loading graph visualization...</Text>
      </Flex>
    );
  }

  return (
    <NvlComponent
      ref={nvlRef}
      nodes={nodes}
      rels={relationships}
      nvlOptions={{
        layout: "d3Force",
        initialZoom: 1,
        minZoom: 0.1,
        maxZoom: 5,
        relationshipThickness: 2,
        disableTelemetry: true,
      }}
      mouseEventCallbacks={{
        onNodeClick: (node: NvlNode) => onNodeClick(node),
        onNodeDoubleClick: (node: NvlNode) => onNodeDoubleClick(node),
        onRelationshipClick: (rel: NvlRelationship) => onRelationshipClick(rel),
        onCanvasClick: () => onCanvasClick(),
        onZoom: isReady,
        onPan: isReady,
        onDrag: isReady,
      }}
      style={{ width: "100%", height: "100%" }}
    />
  );
}
