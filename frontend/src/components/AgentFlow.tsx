/** Agent 流转图：Intake -> Evidence -> Fraud -> Sentiment -> Decision -> [人工/终态]。
 *  绿=完成、黄=挂起/运行、灰=未执行、红=失败。SUSPENDED 时人工节点闪烁。
 *  v4.0 合并后链路将扩展为 8 节点（Critic/DLP/订单三查等），后端 graph 接口扩展后此处加节点即可。
 */
import type { AgentNode } from "../api/types";

const NODE_ORDER = ["INTAKE", "EVIDENCE", "FRAUD", "SENTIMENT", "DECISION", "FINALIZE"];

const NODE_LABEL: Record<string, string> = {
  INTAKE: "Intake 接入",
  EVIDENCE: "Evidence OCR",
  FRAUD: "Fraud 欺诈",
  SENTIMENT: "Sentiment 舆情",
  DECISION: "Decision 决策",
  FINALIZE: "Finalize 终态",
};

function nodeColor(agent: string, node?: AgentNode, suspended = false): string {
  if (agent === "FINALIZE" && suspended) return "#ffc107";
  if (!node) return "#d0d0d0";
  if (node.status === "FAILED") return "#dc3545";
  return "#28a745";
}

export default function AgentFlow({
  nodes,
  status,
}: {
  nodes: AgentNode[];
  status: string;
}) {
  const suspended = status === "SUSPENDED";
  const nodeMap = new Map(nodes.map((n) => [n.agent, n]));
  const humanNode = nodeMap.get("HUMAN_REVIEW");

  return (
    <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
      {NODE_ORDER.map((agent) => {
        const node = nodeMap.get(agent);
        return (
          <div key={agent} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div
              style={{
                padding: "8px 14px",
                borderRadius: 8,
                background: nodeColor(agent, node, suspended),
                color: "#fff",
                fontSize: 13,
                fontWeight: 600,
                opacity: node ? 1 : 0.55,
                animation: suspended && agent === "FINALIZE" ? "blink 1s infinite" : undefined,
              }}
              title={node ? `${node.agent} ${node.duration_ms ?? 0}ms${node.error_tag ? " " + node.error_tag : ""}` : "未执行"}
            >
              {NODE_LABEL[agent] ?? agent}
            </div>
            {agent !== "FINALIZE" && <span style={{ color: "#999" }}>→</span>}
          </div>
        );
      })}
      {humanNode && (
        <>
          <span style={{ color: "#999" }}>→</span>
          <div
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              background: "#ffc107",
              color: "#333",
              fontSize: 13,
              fontWeight: 600,
              animation: suspended ? "blink 1s infinite" : undefined,
            }}
          >
            人工审核 {humanNode.error_tag ? `(${humanNode.error_tag})` : ""}
          </div>
        </>
      )}
      <style>{`@keyframes blink { 50% { opacity: 0.35; } }`}</style>
    </div>
  );
}
