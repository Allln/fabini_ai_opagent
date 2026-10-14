const form = document.querySelector("#run-form");
const requestInput = document.querySelector("#request");
const modeInput = document.querySelector("#mode");
const runButton = form.querySelector("button[type=submit]");
const resultSection = document.querySelector("#result-section");
const statusBadge = document.querySelector("#status-badge");
const modeBadge = document.querySelector("#mode-badge");
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
  renderApproval(result);
  document.querySelector("#timeline").innerHTML = (result.steps || [])
    .map((step) => `<div class="event"><strong>${escapeHtml(step.kind.replaceAll("_", " "))}</strong>
      <small>${escapeHtml(step.message)}</small></div>`).join("");
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
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}
