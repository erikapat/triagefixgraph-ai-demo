"use client";

import { useState, useEffect } from "react";
import {
  Box,
  Heading,
  Text,
  VStack,
  HStack,
  Badge,
  Flex,
  Table,
} from "@chakra-ui/react";
import { FileText } from "lucide-react";
import { API_BASE } from "@/lib/config";

interface SimilarCase {
  incident_id: string;
  created_date?: string;
  category?: string;
  status?: string;
  severity_average?: number;
  similarity_score?: number;
}

interface EvidenceItem {
  type: string;
  count: number;
}

interface DecisionSupportResponse {
  incident_id: string;
  evidence: {
    has_incidence_docs: boolean;
    incidence_docs_count: number;
    lease_contract_count: number;
    finance_invoice_count: number;
    items: EvidenceItem[];
  };
  similar_cases: {
    prior_same_property: SimilarCase[];
    semantic_similar_resolved: SimilarCase[];
  };
}

export function DocumentBrowser({ selectedIncidentId }: { selectedIncidentId?: string | null }) {
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
            <FileText size={16} />
            <span>Evidence / Documents</span>
          </HStack>
        </Heading>
        <Text fontSize="xs" color="gray.500">
          Selected-incident evidence and similar case references
        </Text>
      </Box>

      <VStack flex={1} overflow="auto" px={4} py={2} gap={2} align="stretch">
        {!selectedIncidentId ? (
          <Box py={8} px={4}>
            <Flex justify="center" mb={3}>
              <FileText size={32} color="#A0AEC0" />
            </Flex>
            <Text fontSize="sm" color="gray.500" textAlign="center">
              Select an incident to view decision support.
            </Text>
          </Box>
        ) : !support ? (
          <Text fontSize="sm" color="gray.500">Loading evidence context...</Text>
        ) : (
          <>
            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={2}>Evidence Summary</Text>
              <Text fontSize="sm"><b>Has incidence docs:</b> {support.evidence.has_incidence_docs ? "yes" : "no"}</Text>
              <Text fontSize="sm"><b>Incidence docs:</b> {support.evidence.incidence_docs_count}</Text>
              <Text fontSize="sm"><b>Lease contract:</b> {support.evidence.lease_contract_count}</Text>
              <Text fontSize="sm"><b>Finance invoice:</b> {support.evidence.finance_invoice_count}</Text>
              <HStack mt={2} flexWrap="wrap">
                {support.evidence.items.length === 0 ? (
                  <Badge variant="outline">No evidence items</Badge>
                ) : (
                  support.evidence.items.map((item) => (
                    <Badge key={item.type} variant="outline">{item.type}: {item.count}</Badge>
                  ))
                )}
              </HStack>
            </Box>

            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={2}>Prior Same-Property Incidents</Text>
              {support.similar_cases.prior_same_property.length === 0 ? (
                <Text fontSize="sm" color="gray.500">No prior same-property incidents found.</Text>
              ) : (
                <Table.Root size="sm">
                  <Table.Header>
                    <Table.Row>
                      <Table.ColumnHeader>Incident</Table.ColumnHeader>
                      <Table.ColumnHeader>Date</Table.ColumnHeader>
                      <Table.ColumnHeader>Category</Table.ColumnHeader>
                    </Table.Row>
                  </Table.Header>
                  <Table.Body>
                    {support.similar_cases.prior_same_property.map((c) => (
                      <Table.Row key={c.incident_id}>
                        <Table.Cell>{c.incident_id}</Table.Cell>
                        <Table.Cell>{c.created_date || ""}</Table.Cell>
                        <Table.Cell>{c.category || ""}</Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Root>
              )}
            </Box>

            <Box p={3} borderRadius="md" border="1px solid" borderColor="gray.200">
              <Text fontSize="xs" color="gray.500" mb={2}>Semantic Similar Resolved Cases</Text>
              {support.similar_cases.semantic_similar_resolved.length === 0 ? (
                <Text fontSize="sm" color="gray.500">No semantic resolved matches found.</Text>
              ) : (
                <Table.Root size="sm">
                  <Table.Header>
                    <Table.Row>
                      <Table.ColumnHeader>Incident</Table.ColumnHeader>
                      <Table.ColumnHeader>Similarity</Table.ColumnHeader>
                      <Table.ColumnHeader>Status</Table.ColumnHeader>
                    </Table.Row>
                  </Table.Header>
                  <Table.Body>
                    {support.similar_cases.semantic_similar_resolved.map((c) => (
                      <Table.Row key={c.incident_id}>
                        <Table.Cell>{c.incident_id}</Table.Cell>
                        <Table.Cell>{c.similarity_score?.toFixed(3) ?? ""}</Table.Cell>
                        <Table.Cell>{c.status || ""}</Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Root>
              )}
            </Box>
          </>
        )}
      </VStack>
    </Flex>
  );
}
