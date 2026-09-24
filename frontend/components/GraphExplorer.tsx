"use client";

import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import { Maximize2, Minus, Plus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { bestDefinition, displayPos, uriTail } from "@/lib/format";
import type { GraphResult, SenseCandidate } from "@/lib/types";

interface GraphExplorerProps {
  graph: GraphResult;
  selected: SenseCandidate | null;
  cueWords?: string[];
}

interface InspectedNode {
  label: string;
  relation: string;
  source: string;
}

const lexicalRelations = new Set([
  "synonym", "antonym", "hypernym", "hyponym", "similar", "relatedTerm",
  "holonym", "meronym", "entails", "causes", "domainTopic", "attribute",
  "coordinateTerm", "derivedTerm",
]);

export function hasRenderableRelations(graph: GraphResult, selectedUri?: string | null): boolean {
  const nodeUris = new Set(graph.nodes.map((node) => node.uri));
  return graph.edges.some(
    (edge) => lexicalRelations.has(uriTail(edge.predicate))
      && nodeUris.has(edge.target)
      && edge.target !== selectedUri,
  );
}

const relationLabels: Record<string, string> = {
  synonym: "คำพ้อง", antonym: "คำตรงข้าม", hypernym: "คำกว้างกว่า",
  hyponym: "คำเฉพาะกว่า", similar: "ความหมายใกล้เคียง",
  relatedTerm: "คำที่เกี่ยวข้อง", holonym: "สิ่งที่เป็นองค์รวม",
  meronym: "ส่วนประกอบ", entails: "การกระทำที่ตามมา",
  causes: "เป็นเหตุให้", domainTopic: "หมวดความรู้",
  attribute: "คุณสมบัติ", coordinateTerm: "คำในระดับเดียวกัน",
  derivedTerm: "คำที่สืบเนื่อง",
};

function category(relation: string): string {
  if (relation === "synonym" || relation === "similar") return "similar";
  if (relation === "antonym") return "opposite";
  if (["hypernym", "hyponym", "holonym", "meronym"].includes(relation)) return "hierarchy";
  return "related";
}

function sourceLabel(graph?: string | null): string {
  if (!graph) return "ไม่ระบุแหล่งข้อมูล";
  try {
    return decodeURIComponent(graph.split("/").filter(Boolean).slice(-2).join(" / "));
  } catch {
    return graph;
  }
}

export function GraphExplorer({ graph, selected, cueWords = [] }: GraphExplorerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [inspected, setInspected] = useState<InspectedNode | null>(null);

  const visibleRelations = useMemo(() => {
    if (!selected) return [];
    const nodes = new Map(graph.nodes.map((node) => [node.uri, node]));
    return graph.edges
      .filter((edge) => lexicalRelations.has(uriTail(edge.predicate)))
      .map((edge) => ({ edge, node: nodes.get(edge.target), relation: uriTail(edge.predicate) }))
      .filter((item) => item.node && item.node.uri !== selected.sense_uri)
      .sort((left, right) => {
        const leftCue = cueWords.some((cue) => left.node?.label.includes(cue)) ? 1 : 0;
        const rightCue = cueWords.some((cue) => right.node?.label.includes(cue)) ? 1 : 0;
        return rightCue - leftCue;
      })
      .slice(0, 8);
  }, [cueWords, graph.edges, graph.nodes, selected]);

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!selected) return [];
    const referenceEdge = graph.edges.find(
      (edge) => edge.source === selected.sense_uri && uriTail(edge.predicate) === "reference",
    );
    const conceptLabel = graph.nodes.find((node) => node.uri === referenceEdge?.target)?.label;
    const definition = conceptLabel || bestDefinition(selected)?.text || "ยังไม่มีนิยาม";
    const shortDefinition = definition.length > 34 ? `${definition.slice(0, 34)}…` : definition;
    const nodes: ElementDefinition[] = [{ data: {
      id: selected.sense_uri,
      label: `${selected.lemma}\n${shortDefinition} · ${displayPos(selected.pos)}`,
      detailLabel: selected.lemma,
      relation: "ความหมายที่เลือก",
      sourceLabel: sourceLabel(selected.source_graph),
      kind: "selected",
    } }];
    const edges: ElementDefinition[] = [];
    const seen = new Set<string>();
    visibleRelations.forEach(({ edge, node, relation }, index) => {
      if (!node || seen.has(node.uri)) return;
      seen.add(node.uri);
      const kind = category(relation);
      nodes.push({ data: {
        id: node.uri,
        label: node.label,
        detailLabel: node.label,
        relation: relationLabels[relation] || relation,
        sourceLabel: sourceLabel(node.source_graph || edge.source_graph),
        kind,
      } });
      edges.push({ data: {
        id: `relation-${index}`,
        source: selected.sense_uri,
        target: node.uri,
        label: relationLabels[relation] || relation,
        kind,
      } });
    });
    return [...nodes, ...edges];
  }, [graph.edges, graph.nodes, selected, visibleRelations]);

  useEffect(() => {
    if (!hostRef.current) return;
    cyRef.current?.destroy();
    const cy = cytoscape({
      container: hostRef.current,
      elements,
      minZoom: 0.45,
      maxZoom: 2.2,
      style: [
        { selector: "node", style: {
          width: 144, height: 66, shape: "round-rectangle",
          "background-color": "#fffaf7", "border-color": "#ad7861", "border-width": 1.6,
          label: "data(label)", color: "#563e42",
          "font-family": "Noto Sans Thai, sans-serif", "font-size": 14,
          "font-weight": 650, "text-wrap": "wrap", "text-max-width": "128px",
          "text-valign": "center", "text-halign": "center", "overlay-opacity": 0,
        } },
        { selector: 'node[kind = "selected"]', style: {
          width: 212, height: 108, "background-color": "#5e3969",
          "border-color": "#4d2b57", "border-width": 3, color: "#ffffff",
          "font-size": 16, "font-weight": 750, "text-max-width": "182px",
        } },
        { selector: 'node[kind = "similar"]', style: {
          "background-color": "#f2eaf6", "border-color": "#7b5186", color: "#55305f",
        } },
        { selector: 'node[kind = "opposite"]', style: {
          "background-color": "#fcefed", "border-color": "#b86a68", color: "#823e42",
        } },
        { selector: 'node[kind = "hierarchy"]', style: {
          "background-color": "#eef8f4", "border-color": "#258a75", color: "#155f51",
        } },
        { selector: "node:selected", style: { "border-width": 4, "border-color": "#b56d43" } },
        { selector: "edge", style: {
          width: 1.8, "line-color": "#ae9caf", "target-arrow-color": "#ae9caf",
          "target-arrow-shape": "triangle", "arrow-scale": 0.85,
          "curve-style": "bezier", label: "data(label)", color: "#665268",
          "font-family": "Noto Sans Thai, sans-serif", "font-size": 10,
          "font-weight": 600, "text-background-color": "#ffffff",
          "text-background-opacity": 0.94, "text-background-padding": "3px",
          "text-wrap": "wrap", "text-max-width": "126px",
        } },
        { selector: 'edge[kind = "hierarchy"]', style: {
          "line-color": "#57a391", "target-arrow-color": "#57a391",
        } },
      ],
      layout: {
        name: "breadthfirst", directed: true, fit: true,
        roots: selected ? [selected.sense_uri] : undefined,
        padding: 36, spacingFactor: 1.1, nodeDimensionsIncludeLabels: true,
      },
    });
    cyRef.current = cy;
    cy.on("tap", "node", (event) => {
      const data = event.target.data();
      setInspected({ label: data.detailLabel || data.label, relation: data.relation, source: data.sourceLabel });
    });
    cy.on("tap", (event) => { if (event.target === cy) setInspected(null); });
    return () => { cy.destroy(); cyRef.current = null; };
  }, [elements, selected]);

  const adjustZoom = (amount: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() + amount, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };

  return (
    <section className="graph-panel demo-graph-panel" aria-labelledby="graph-title">
      <div className="panel-heading graph-heading">
        <div>
          <h2 id="graph-title">ความสัมพันธ์ของ “{selected?.lemma || "คำที่เลือก"}”</h2>
          <span>{visibleRelations.length} ความสัมพันธ์จากข้อมูลที่นำเข้า</span>
        </div>
        <p>เลือกโหนดเพื่อดูประเภทความสัมพันธ์และแหล่งข้อมูล</p>
      </div>
      <div className="graph-stage">
        <div ref={hostRef} className="cytoscape-host" aria-label="กราฟความสัมพันธ์ของคำ" />
        {!visibleRelations.length && <div className="graph-empty">ยังไม่มีคำที่เชื่อมกับความหมายนี้โดยตรง</div>}
        {inspected && (
          <aside className="graph-node-inspector" aria-live="polite">
            <button type="button" onClick={() => setInspected(null)} aria-label="ปิดรายละเอียดโหนด"><X aria-hidden="true" /></button>
            <small>{inspected.relation}</small>
            <strong>{inspected.label}</strong>
            <span>ที่มา: {inspected.source}</span>
          </aside>
        )}
        <div className="graph-controls" aria-label="ควบคุมกราฟ">
          <button type="button" aria-label="ขยายกราฟ" onClick={() => adjustZoom(0.18)}><Plus /></button>
          <button type="button" aria-label="ย่อกราฟ" onClick={() => adjustZoom(-0.18)}><Minus /></button>
          <button type="button" aria-label="จัดกราฟให้อยู่กึ่งกลาง" onClick={() => cyRef.current?.fit(undefined, 36)}><Maximize2 /></button>
        </div>
      </div>
      <div className="graph-legend" aria-label="คำอธิบายสีของโหนด">
        <span><i className="legend-word" />ความหมายที่เลือก</span>
        <span><i className="legend-dialect" />คำพ้องหรือใกล้เคียง</span>
        <span><i className="legend-medical" />ความสัมพันธ์เชิงลำดับ</span>
      </div>
    </section>
  );
}
