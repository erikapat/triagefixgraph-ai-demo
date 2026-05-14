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
  Image,
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

interface IncidentRowLite {
  incident_id: string;
  category?: string;
  urgency?: string;
}

function hashString(input: string): number {
  let h = 0;
  for (let i = 0; i < input.length; i += 1) {
    h = (h * 31 + input.charCodeAt(i)) >>> 0;
  }
  return h;
}

function pickPalette(seed: number): { a: string; b: string; c: string } {
  const palettes = [
    { a: "#1d4ed8", b: "#0ea5e9", c: "#e0f2fe" },
    { a: "#0f766e", b: "#14b8a6", c: "#ccfbf1" },
    { a: "#7c3aed", b: "#a78bfa", c: "#ede9fe" },
    { a: "#b45309", b: "#f59e0b", c: "#fef3c7" },
    { a: "#be123c", b: "#fb7185", c: "#ffe4e6" },
  ];
  return palettes[seed % palettes.length];
}

function getCategoryPalette(category?: string): { a: string; b: string; c: string } {
  const c = (category || "").toLowerCase();
  if (c.includes("fontan")) return { a: "#075985", b: "#0ea5e9", c: "#dbeafe" };
  if (c.includes("electric")) return { a: "#92400e", b: "#f59e0b", c: "#fef3c7" };
  if (c.includes("cerrad")) return { a: "#1f2937", b: "#6b7280", c: "#e5e7eb" };
  if (c.includes("humedad") || c.includes("filtr")) return { a: "#0f766e", b: "#14b8a6", c: "#ccfbf1" };
  if (c.includes("puerta") || c.includes("ventana")) return { a: "#334155", b: "#64748b", c: "#e2e8f0" };
  if (c.includes("mobili")) return { a: "#7c2d12", b: "#ea580c", c: "#ffedd5" };
  return pickPalette(hashString(category || "general"));
}

function getCategoryPhotoUrls(category?: string, incidentId?: string): { incidentPhoto: string; budgetPhoto: string } {
  const c = (category || "").toLowerCase();
  const seed = encodeURIComponent(`${incidentId || "incident"}-${category || "general"}`);
  const fallbackA = `https://picsum.photos/seed/${seed}-a/640/360`;
  const fallbackB = `https://picsum.photos/seed/${seed}-b/640/360`;

  if (c.includes("fontan")) {
    return {
      incidentPhoto: "https://images.unsplash.com/photo-1585704032915-c3400ca199e7?auto=format&fit=crop&w=1200&q=80",
      budgetPhoto: "https://images.unsplash.com/photo-1558618047-3c8c76ca7d13?auto=format&fit=crop&w=1200&q=80",
    };
  }
  if (c.includes("electric")) {
    return {
      incidentPhoto: "https://images.unsplash.com/photo-1581092580497-e0d23cbdf1dc?auto=format&fit=crop&w=1200&q=80",
      budgetPhoto: "https://images.unsplash.com/photo-1558002038-1055907df827?auto=format&fit=crop&w=1200&q=80",
    };
  }
  if (c.includes("cerrad") || c.includes("puerta") || c.includes("ventana")) {
    return {
      incidentPhoto: "https://images.unsplash.com/photo-1558002038-1055907df827?auto=format&fit=crop&w=1200&q=80",
      budgetPhoto: "https://images.unsplash.com/photo-1616594039964-3cfc4e42c1d0?auto=format&fit=crop&w=1200&q=80",
    };
  }
  if (c.includes("humedad") || c.includes("filtr")) {
    return {
      incidentPhoto: "https://images.unsplash.com/photo-1560185007-c5ca9d2c014d?auto=format&fit=crop&w=1200&q=80",
      budgetPhoto: "https://images.unsplash.com/photo-1554995207-c18c203602cb?auto=format&fit=crop&w=1200&q=80",
    };
  }
  if (c.includes("mobili")) {
    return {
      incidentPhoto: "https://images.unsplash.com/photo-1556228453-efd6c1ff04f6?auto=format&fit=crop&w=1200&q=80",
      budgetPhoto: "https://images.unsplash.com/photo-1505691938895-1758d7feb511?auto=format&fit=crop&w=1200&q=80",
    };
  }

  return { incidentPhoto: fallbackA, budgetPhoto: fallbackB };
}

function buildMockPhotoDataUri(seedKey: string, title: string, subtitle: string, category?: string): string {
  const seed = hashString(seedKey);
  const p = getCategoryPalette(category);
  const short = title.slice(0, 26);
  const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="${p.a}" />
      <stop offset="100%" stop-color="${p.b}" />
    </linearGradient>
  </defs>
  <rect width="640" height="360" fill="url(#g)" />
  <circle cx="${80 + (seed % 460)}" cy="${70 + (seed % 170)}" r="60" fill="${p.c}" opacity="0.22" />
  <rect x="24" y="252" width="592" height="84" rx="10" fill="#0f172a" opacity="0.28" />
  <text x="36" y="286" fill="white" font-family="Arial, sans-serif" font-size="24" font-weight="700">${short}</text>
  <text x="36" y="316" fill="white" font-family="Arial, sans-serif" font-size="16" opacity="0.88">${subtitle}</text>
</svg>`;
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

export function DocumentBrowser({ selectedIncidentId }: { selectedIncidentId?: string | null }) {
  const [support, setSupport] = useState<DecisionSupportResponse | null>(null);
  const [incidentMeta, setIncidentMeta] = useState<IncidentRowLite | null>(null);
  const [incidentPhotoFailed, setIncidentPhotoFailed] = useState(false);
  const [budgetPhotoFailed, setBudgetPhotoFailed] = useState(false);

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

  useEffect(() => {
    setIncidentPhotoFailed(false);
    setBudgetPhotoFailed(false);
  }, [selectedIncidentId, incidentMeta?.category]);

  useEffect(() => {
    async function loadIncidentMeta() {
      if (!selectedIncidentId) {
        setIncidentMeta(null);
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/triagefix/incidents`, { signal: AbortSignal.timeout(10000) });
        if (!res.ok) {
          setIncidentMeta(null);
          return;
        }
        const data = await res.json();
        const rows = Array.isArray(data?.incidents) ? (data.incidents as IncidentRowLite[]) : [];
        const row = rows.find((r) => String(r.incident_id) === String(selectedIncidentId)) || null;
        setIncidentMeta(row);
      } catch {
        setIncidentMeta(null);
      }
    }
    loadIncidentMeta();
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
              <Text fontSize="xs" color="gray.500" mb={2}>Photo Previews</Text>
              {!support.evidence.has_incidence_docs ? (
                <Box
                  p={3}
                  borderRadius="md"
                  border="1px dashed"
                  borderColor="orange.300"
                  bg="orange.50"
                >
                  <Text fontSize="sm" color="orange.900" fontWeight="semibold">
                    No hay fotos reales del incidente en evidencia
                  </Text>
                  <Text fontSize="xs" color="orange.800" mt={1}>
                    Estas imágenes son solo referencia visual por categoría, no prueba documental.
                  </Text>
                </Box>
              ) : null}
              <HStack align="stretch" gap={3} flexWrap="wrap" mt={support.evidence.has_incidence_docs ? 0 : 2}>
                <Box w="100%" maxW="280px">
                  {(() => {
                    const photos = getCategoryPhotoUrls(incidentMeta?.category, selectedIncidentId || undefined);
                    return (
                  <Image
                    src={
                      incidentPhotoFailed
                        ? buildMockPhotoDataUri(
                            `${selectedIncidentId}:incident:${incidentMeta?.category || "general"}`,
                            `${incidentMeta?.category || "Incident"} ${selectedIncidentId}`,
                            `${incidentMeta?.urgency || "Normal"} urgency • fallback`,
                            incidentMeta?.category
                          )
                        : photos.incidentPhoto
                    }
                    alt={`Incident reference photo ${selectedIncidentId}`}
                    w="100%"
                    h="160px"
                    objectFit="cover"
                    borderRadius="md"
                    border="1px solid"
                    borderColor="gray.200"
                    onError={() => setIncidentPhotoFailed(true)}
                  />
                    );
                  })()}
                  <Text fontSize="xs" color="gray.600" mt={1}>
                    {support.evidence.has_incidence_docs ? "Incident photo" : "Reference photo"} {incidentMeta?.category ? `(${incidentMeta.category})` : ""}
                  </Text>
                </Box>
                <Box w="100%" maxW="280px">
                  {(() => {
                    const photos = getCategoryPhotoUrls(incidentMeta?.category, selectedIncidentId || undefined);
                    return (
                  <Image
                    src={
                      budgetPhotoFailed
                        ? buildMockPhotoDataUri(
                            `${selectedIncidentId}:budget:${support.evidence.items.map((x) => x.type).join(",")}`,
                            "Budget attachment",
                            `${support.evidence.items.length} evidence types • fallback`,
                            incidentMeta?.category
                          )
                        : photos.budgetPhoto
                    }
                    alt={`Budget reference photo ${selectedIncidentId}`}
                    w="100%"
                    h="160px"
                    objectFit="cover"
                    borderRadius="md"
                    border="1px solid"
                    borderColor="gray.200"
                    onError={() => setBudgetPhotoFailed(true)}
                  />
                    );
                  })()}
                  <Text fontSize="xs" color="gray.600" mt={1}>
                    {support.evidence.furniture_budget_count > 0 ? "Budget doc photo" : "Budget reference photo"}
                  </Text>
                </Box>
              </HStack>
            </Box>

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
