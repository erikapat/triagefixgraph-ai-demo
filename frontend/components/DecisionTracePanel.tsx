"use client";

import { useState, useEffect } from "react";
import { Box, Heading, Text, VStack, HStack, Badge, Flex, Table } from "@chakra-ui/react";
import { GitBranch, Brain } from "lucide-react";
import { API_BASE } from "@/lib/config";

interface DecisionSupportStep {
  step_number: number;
  title: string;
  observation: string;
  source: string;
}

interface SeveritySignal {
  dimension: string;
  score: number;
}

interface DecisionSupportResponse {
  incident_id: string;
  recommendation_summary: {
    recommended_action?: string;
    recommended_trade?: string;
    provider?: string;
    provider_confidence?: string;
    severity_average?: number;
    next_action?: string;
  };
  property_info: {
    property_context_id?: string;
    area_cluster?: string;
    prior_same_property_count?: number;
    prior_same_property_categories?: string[];
    latest_prior_incident_date?: string;
  };
  severity_signals: SeveritySignal[];
  decision_trace: DecisionSupportStep[];
}

function SeverityRadarChart({ signals }: { signals: SeveritySignal[] }) {
  if (!signals.length) return null;

  const size = 260;
  const cx = size / 2;
  const cy = size / 2;
  const maxRadius = 85;
  const maxScore = 5;
  const levels = [1, 2, 3, 4, 5];
  const angleStep = (Math.PI * 2) / signals.length;

  const polarPoint = (index: number, radius: number) => {
    const angle = -Math.PI / 2 + index * angleStep;
    return {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    };
  };

  const polygonPoints = signals
    .map((s, i) => {
      const score = Math.max(0, Math.min(maxScore, Number(s.score) || 0));
      const r = (score / maxScore) * maxRadius;
      const p = polarPoint(i, r);
      return `${p.x},${p.y}`;
    })
    .join(" ");

  return (
    <Box mb={3}>
      <svg viewBox={`0 0 ${size} ${size}`} width="100%" height="220" role="img" aria-label="Severity radar chart">
        {levels.map((lvl) => {
          const r = (lvl / maxScore) * maxRadius;
          const pts = signals
            .map((_, i) => {
              const p = polarPoint(i, r);
              return `${p.x},${p.y}`;
            })
            .join(" ");
          return (
            <polygon
              key={`lvl-${lvl}`}
              points={pts}
              fill="none"
              stroke="#E2E8F0"
              strokeWidth="1"
            />
          );
        })}

        {signals.map((_, i) => {
          const p = polarPoint(i, maxRadius);
          return <line key={`axis-${i}`} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="#CBD5E0" strokeWidth="1" />;
        })}

        <polygon points={polygonPoints} fill="rgba(49,130,206,0.22)" stroke="#2B6CB0" strokeWidth="2" />

        {signals.map((s, i) => {
          const edge = polarPoint(i, maxRadius + 16);
          const clean = s.dimension.replaceAll("_", " ");
          return (
            <text
              key={`label-${s.dimension}-${i}`}
              x={edge.x}
              y={edge.y}
              textAnchor="middle"
              fontSize="10"
              fill="#4A5568"
            >
              {clean}
            </text>
          );
        })}
      </svg>
    </Box>
  );
}

export function DecisionTracePanel({ selectedIncidentId }: { selectedIncidentId?: string | null }) {
  const [support, setSupport] = useState<DecisionSupportResponse | null>(null);

  useEffect(() => {
    async function loadDecisionSupport() {
      if (!selectedIncidentId) {
        setSupport(null);
        return;
      }
      try {
        const res = await fetch(
          `${API_BASE}/triagefix/incidents/${encodeURIComponent(selectedIncidentId)}/decision-support`,
          { signal: AbortSignal.timeout(10000) }
        );
        if (!res.ok) {
          setSupport(null);
          return;
        }
        const data = await res.json();
        setSupport(data as DecisionSupportResponse);
      } catch {
        setSupport(null);
      }
    }
    loadDecisionSupport();
  }, [selectedIncidentId]);

  return (
    <Flex direction="column" h="100%">
      <Box px={4} py={3} borderBottom="1px solid" borderColor="gray.200">
        <Heading size="sm">
          <HStack>
            <GitBranch size={16} />
            <span>Decision Support</span>
          </HStack>
        </Heading>
        <Text fontSize="xs" color="gray.500">
          Selected-incident operational context
        </Text>
      </Box>

      <VStack flex={1} overflow="auto" px={4} py={2} gap={2} align="stretch">
        {!selectedIncidentId ? (
          <Box py={8} px={4}>
            <Flex justify="center" mb={3}>
              <GitBranch size={32} color="#A0AEC0" />
            </Flex>
            <Text fontSize="sm" color="gray.500" textAlign="center">
              Select an incident to view decision support.
            </Text>
          </Box>
        ) : !support ? (
          <Text fontSize="sm" color="gray.500">Loading incident decision support...</Text>
        ) : (
          <>
            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={1}>Recommendation Summary</Text>
              <Text fontSize="sm"><b>Action:</b> {support.recommendation_summary.recommended_action || "unknown"}</Text>
              <Text fontSize="sm"><b>Trade:</b> {support.recommendation_summary.recommended_trade || "unknown"}</Text>
              <Text fontSize="sm"><b>Provider:</b> {support.recommendation_summary.provider || "not assigned"}</Text>
              <Text fontSize="sm"><b>Provider confidence:</b> {support.recommendation_summary.provider_confidence || "unknown"}</Text>
              <Text fontSize="sm"><b>Severity avg:</b> {support.recommendation_summary.severity_average ?? "unknown"}</Text>
              <Text fontSize="sm" mt={2}><b>Next action:</b> {support.recommendation_summary.next_action || "unknown"}</Text>
            </Box>

            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={1}>Property Info</Text>
              <Text fontSize="sm"><b>Property:</b> {support.property_info.property_context_id || "unknown"}</Text>
              <Text fontSize="sm"><b>Area:</b> {support.property_info.area_cluster || "unknown"}</Text>
              <Text fontSize="sm"><b>Prior same-property incidents:</b> {support.property_info.prior_same_property_count ?? 0}</Text>
              <Text fontSize="sm"><b>Latest prior date:</b> {support.property_info.latest_prior_incident_date || "unknown"}</Text>
            </Box>

            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={2}>Severity Overview</Text>
              {support.severity_signals.length > 0 ? (
                <SeverityRadarChart signals={support.severity_signals} />
              ) : null}
              <Table.Root size="sm">
                <Table.Header>
                  <Table.Row>
                    <Table.ColumnHeader>Dimension</Table.ColumnHeader>
                    <Table.ColumnHeader>Score</Table.ColumnHeader>
                  </Table.Row>
                </Table.Header>
                <Table.Body>
                  {support.severity_signals.map((s) => (
                    <Table.Row key={s.dimension}>
                      <Table.Cell>{s.dimension}</Table.Cell>
                      <Table.Cell>{s.score}</Table.Cell>
                    </Table.Row>
                  ))}
                </Table.Body>
              </Table.Root>
            </Box>

            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={2}>Decision Trace / Explanation</Text>
              <VStack gap={2} align="stretch">
                {support.decision_trace.map((step) => (
                  <Box key={step.step_number} pl={3} borderLeft="2px solid" borderColor="blue.200">
                    <HStack>
                      <Brain size={12} />
                      <Text fontSize="sm" fontWeight="medium">{step.step_number}. {step.title}</Text>
                    </HStack>
                    <Text fontSize="xs" color="gray.700" mt={1}>{step.observation}</Text>
                    <Badge size="sm" mt={1} variant="outline">{step.source}</Badge>
                  </Box>
                ))}
              </VStack>
            </Box>
          </>
        )}
      </VStack>
    </Flex>
  );
}
