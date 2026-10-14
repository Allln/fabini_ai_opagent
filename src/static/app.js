const form = document.querySelector("#run-form");
const requestInput = document.querySelector("#request");
const modeInput = document.querySelector("#mode");
const runButton = form.querySelector("button[type=submit]");
const resultSection = document.querySelector("#result-section");
const statusBadge = document.querySelector("#status-badge");
const modeBadge = document.querySelector("#mode-badge");
const dbDetailsToggle = document.querySelector("#db-details-toggle");
const dbDetails = document.querySelector("#db-details");
let currentResult = null;

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
  const records = auditCall?.result?.records || [];
  if (!records.length) {
    dbDetails.innerHTML = "<p class='hint'>No related SQLite records were returned.</p>";
    dbDetails.classList.remove("hidden");
    return;
  }
  dbDetails.innerHTML = `
    <div class="db-details-heading">
      <h3>Related DB details</h3>
      <span class="hint">${records.length} record(s)</span>
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
  dbDetails.classList.add("hidden");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
