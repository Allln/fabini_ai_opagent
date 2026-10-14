const form = document.querySelector("#run-form");
const requestInput = document.querySelector("#request");
const modeInput = document.querySelector("#mode");
const runButton = form.querySelector("button[type=submit]");
const resultSection = document.querySelector("#result-section");
const statusBadge = document.querySelector("#status-badge");
const modeBadge = document.querySelector("#mode-badge");
const dbDetailsToggle = document.querySelector("#db-details-toggle");
const dbDetails = document.querySelector("#db-details");
const stateGraph = document.querySelector("#state-graph");
const stateGraphCaption = document.querySelector("#state-graph-caption");
const stateGraphOrder = document.querySelector("#state-graph-order");
const stateGraphAudit = document.querySelector("#state-graph-audit");
let currentResult = null;
const MAX_VISIBLE_AUDIT_RECORDS = 5;

document.querySelectorAll("[data-example]").forEach((button) => {
  button.addEventListener("click", () => {
    requestInput.value = button.dataset.example;
    requestInput.focus();
  });
});

modeInput.addEventListener("change", () => {
  modeBadge.textContent = `${modeInput.value.toUpperCase()} MODE`;
});

dbDetailsToggle.addEventListener("change", () => {
  renderDatabaseDetails(currentResult);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  runButton.disabled = true;
  runButton.innerHTML = "Investigating <span>...</span>";
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({request: requestInput.value, mode: modeInput.value}),
    });
    if (!response.ok) throw new Error(await response.text());
    render(await response.json());
  } catch (error) {
    renderError(error.message);
  } finally {
    runButton.disabled = false;
    runButton.innerHTML = "Run investigation <span>→</span>";
  }
});

function render(result) {
  currentResult = result;
  resultSection.classList.remove("hidden");
  statusBadge.textContent = result.status.replaceAll("_", " ").toUpperCase();
  statusBadge.className = `status ${result.status}`;
  document.querySelector("#result-title").textContent = `Run ${result.run_id.slice(0, 8)}`;
  document.querySelector("#summary").textContent = result.summary || "The agent is working...";
  renderStateGraph(result);
  document.querySelector("#findings").innerHTML = (result.findings || [])
    .map((finding) => `<li>${escapeHtml(finding)}</li>`).join("") || "<li>No separate findings recorded.</li>";
  document.querySelector("#tool-calls").innerHTML = (result.tool_calls || [])
    .map((call) => `<div class="tool ${call.status === "succeeded" ? "ok" : "fail"}">
      <strong>${escapeHtml(call.name)}</strong><small>${escapeHtml(call.status)}</small>
    </div>`).join("") || "<span class='hint'>No tools called.</span>";
  renderDatabaseDetails(result);
  renderApproval(result);
  document.querySelector("#timeline").innerHTML = (result.steps || [])
    .map((step) => `<div class="event"><strong>${escapeHtml(step.kind.replaceAll("_", " "))}</strong>
      <small>${escapeHtml(step.message)}</small></div>`).join("");
}

function renderDatabaseDetails(result) {
  if (!dbDetailsToggle.checked || !result) {
    dbDetails.classList.add("hidden");
    return;
  }
  const auditCall = (result.tool_calls || []).find((call) => call.name === "get_order_audit");
  const auditResult = auditCall?.result || {};
  const records = (auditResult.records || []).slice(0, MAX_VISIBLE_AUDIT_RECORDS);
  const totalRecordCount = auditResult.record_count ?? records.length;
  if (!records.length) {
    dbDetails.innerHTML = "<p class='hint'>No related SQLite records were returned.</p>";
    dbDetails.classList.remove("hidden");
    return;
  }
  dbDetails.innerHTML = `
    <div class="db-details-heading">
      <h3>Related DB details</h3>
      <span class="hint">Showing latest ${records.length} of ${totalRecordCount} record(s)</span>
    </div>
    <div class="table-scroll">
      <table class="db-table">
        <thead>
          <tr>
            <th>Source</th>
            <th>Operation</th>
            <th>Status</th>
            <th>Run</th>
            <th>Time</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>${records.map(renderDatabaseRow).join("")}</tbody>
      </table>
    </div>`;
  dbDetails.classList.remove("hidden");
}

function renderStateGraph(result) {
  const orderCall = (result.tool_calls || []).find((call) => call.name === "get_order");
  const order = successfulToolResult(result, "get_order");
  const payment = successfulToolResult(result, "get_payment");
  const fulfillment = successfulToolResult(result, "get_fulfillment");
  const syncLogs = successfulToolResult(result, "get_sync_logs");
  const audit = successfulToolResult(result, "get_order_audit");
  const orderId = order?.order_id || extractOrderId(result.request);
  const nodes = [];

  if (order) {
    const orderDetails = [
      order.status,
      order.items ? `${order.items} item(s)` : null,
      order.warehouse,
    ].filter(Boolean).join(" · ");
    nodes.push({label: `Order ${orderId}`, detail: orderDetails, tone: toneFor(order.status)});
  } else {
    nodes.push({
      label: `Order ${orderId || "not identified"}`,
      detail: orderCall?.error || "No order data returned.",
      tone: orderCall?.status === "failed" ? "danger" : "neutral",
    });
  }

  if (payment) {
    nodes.push({
      label: "Payment",
      detail: [payment.status, payment.transaction_id].filter(Boolean).join(" · "),
      tone: toneFor(payment.status),
    });
  }
  if (fulfillment) {
    nodes.push({
      label: "Fulfillment",
      detail: [fulfillment.status, fulfillment.reason, fulfillment.tracking]
        .filter(Boolean).join(" · "),
      tone: toneFor(fulfillment.status),
    });
  }
  const latestSync = syncLogs?.entries?.[0];
  if (latestSync) {
    nodes.push({
      label: "Synchronization",
      detail: [latestSync.status, latestSync.message].filter(Boolean).join(" · "),
      tone: toneFor(latestSync.status),
    });
  }

  const action = actionState(result);
  if (action) nodes.push(action);

  if (!nodes.length) {
    stateGraph.innerHTML = "<p class='hint'>No order state was detected.</p>";
    stateGraphCaption.textContent = "The run did not return state evidence.";
    stateGraphOrder.textContent = "";
    stateGraphAudit.textContent = "";
    return;
  }

  const edgeLabels = ["payment", "fulfillment", "sync evidence", "next step"];
  stateGraph.innerHTML = nodes.map((node, index) => `
    ${index ? `<span class="state-edge" aria-hidden="true">
      <span>${escapeHtml(edgeLabels[index - 1] || "related")}</span>→
    </span>` : ""}
    <div class="state-node ${node.tone}" aria-label="${escapeHtml(`${node.label}: ${node.detail}`)}">
      <strong>${escapeHtml(node.label)}</strong>
      <span>${escapeHtml(node.detail || "State not available")}</span>
    </div>`).join("");
  stateGraphCaption.textContent = "A visual summary of evidence detected by the agent.";
  stateGraphOrder.textContent = orderId ? `ORDER ${orderId}` : "";
  stateGraphAudit.textContent = audit
    ? `Audit history: ${audit.record_count || audit.records?.length || 0} related record(s).`
    : "";
}

function successfulToolResult(result, name) {
  return (result.tool_calls || []).find(
    (call) => call.name === name && call.status === "succeeded",
  )?.result || null;
}

function extractOrderId(request) {
  return String(request || "").match(/\b\d{4,20}\b/)?.[0] || "";
}

function actionState(result) {
  const actionCall = (result.tool_calls || []).find(
    (call) => ["retry_sync", "create_ticket"].includes(call.name),
  );
  const approval = (result.approvals || []).find((item) => item.status === "pending");
  if (approval) {
    return {
      label: "Next action",
      detail: `${approval.tool_name} · approval required`,
      tone: "warning",
    };
  }
  if (actionCall) {
    return {
      label: "Action",
      detail: `${actionCall.name} · ${actionCall.status}`,
      tone: toneFor(actionCall.status),
    };
  }
  return null;
}

function toneFor(status) {
  const normalized = String(status || "").toLowerCase();
  if (["captured", "shipped", "success", "succeeded", "completed", "paid"].includes(normalized)) {
    return "success";
  }
  if (["blocked", "failed", "error", "rejected", "timed_out"].includes(normalized)) {
    return "danger";
  }
  if (["pending", "retryable", "queued", "waiting_approval", "approved"].includes(normalized)) {
    return "warning";
  }
  return "neutral";
}

function renderDatabaseRow(record) {
  const operation = record.name || record.tool_name || "-";
  const timestamp = record.started_at || record.created_at || "-";
  const details = {
    arguments: record.arguments,
    ...(record.result ? {result: record.result} : {}),
    ...(record.error ? {error: record.error} : {}),
    ...(record.resolved_at ? {resolved_at: record.resolved_at} : {}),
  };
  return `<tr>
    <td>${escapeHtml(record.source || "-")}</td>
    <td><strong>${escapeHtml(operation)}</strong></td>
    <td><span class="db-status ${escapeHtml(record.status || "")}">
      ${escapeHtml(record.status || "-")}</span></td>
    <td><code>${escapeHtml(shortRunId(record.run_id))}</code></td>
    <td>${escapeHtml(formatTimestamp(timestamp))}</td>
    <td><code>${escapeHtml(JSON.stringify(details))}</code></td>
  </tr>`;
}

function shortRunId(runId) {
  return runId ? String(runId).slice(0, 8) : "-";
}

function formatTimestamp(timestamp) {
  if (timestamp === "-") return timestamp;
  return String(timestamp).replace("T", " ").replace(/\.\d+/, "");
}

function renderApproval(result) {
  const panel = document.querySelector("#approval-panel");
  const approval = (result.approvals || []).find((item) => item.status === "pending");
  if (!approval) {
    panel.innerHTML = "";
    return;
  }
  panel.innerHTML = `<div class="approval">
    <p><strong>Approval required.</strong> The agent wants to call
      <code>${escapeHtml(approval.tool_name)}</code> with
      <code>${escapeHtml(JSON.stringify(approval.arguments))}</code>.</p>
    <div class="approval-actions">
      <button data-decision="approve">Approve action</button>
      <button class="reject" data-decision="reject">Reject</button>
    </div>
  </div>`;
  panel.querySelectorAll("[data-decision]").forEach((button) => {
    button.addEventListener("click", () => resolveApproval(approval.id, button.dataset.decision));
  });
}

async function resolveApproval(approvalId, decision) {
  const response = await fetch(`/api/runs/${currentResult.run_id}/approvals/${approvalId}`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({decision}),
  });
  if (!response.ok) {
    renderError(await response.text());
    return;
  }
  render(await response.json());
}

function renderError(message) {
  resultSection.classList.remove("hidden");
  statusBadge.textContent = "ERROR";
  statusBadge.className = "status failed";
  document.querySelector("#result-title").textContent = "Request failed";
  document.querySelector("#summary").textContent = message;
  stateGraph.innerHTML = "<p class='hint'>No order state available.</p>";
  stateGraphCaption.textContent = "The request did not produce a state graph.";
  stateGraphOrder.textContent = "";
  stateGraphAudit.textContent = "";
  dbDetails.classList.add("hidden");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
