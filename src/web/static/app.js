const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const jobsTable = $("#jobsTable");
const jobState = $("#jobState");
const activeJob = $("#activeJob");
const logOutput = $("#logOutput");
const configModal = $("#configModal");
const modalTitle = $("#modalTitle");
const modalHint = $("#modalHint");
const modalForm = $("#modalForm");
const modalEditor = $("#modalEditor");
const modalState = $("#modalState");
const googleStatusDot = $("#googleStatusDot");
const googleStatusText = $("#googleStatusText");
const googleStatusMeta = $("#googleStatusMeta");
const diskStatusDot = $("#diskStatusDot");
const diskStatusText = $("#diskStatusText");
const diskStatusMeta = $("#diskStatusMeta");
const jobsPrevBtn = $("#jobsPrevBtn");
const jobsNextBtn = $("#jobsNextBtn");
const jobsPageInfo = $("#jobsPageInfo");
const autoLogRefreshToggle = $("#autoLogRefreshToggle");

let selectedJobId = null;
let currentConfigText = "";
let activeConfigTaskId = null;
let activeConfigSection = null;
let activeConfigMode = "form";
let allJobs = [];
let jobsPage = 1;
let logLoaded = false;
const jobsPerPage = 10;

function isConfigModalOpen() {
  return Boolean(configModal && !configModal.hidden);
}

function formatTime(seconds) {
  if (!seconds) return "";
  return new Date(seconds * 1000).toLocaleString();
}

function statusClass(status) {
  return `status ${status || ""}`.trim();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderProgress(progress) {
  const percent = Math.max(0, Math.min(100, Number(progress?.percent || 0)));
  const label = progress?.label || "";
  const count = progress?.current && progress?.total ? `${progress.current}/${progress.total}` : "";
  return `
    <div class="progress-cell" title="${escapeHtml(label)}">
      <div class="progress-track">
        <div class="progress-fill" style="width: ${percent.toFixed(1)}%"></div>
      </div>
      <div class="progress-meta">
        <span>${percent.toFixed(1)}%</span>
        <span>${escapeHtml(count)}</span>
      </div>
      <div class="progress-label">${escapeHtml(label)}</div>
    </div>`;
}

function setDot(dot, state) {
  if (!dot) return;
  dot.className = `status-dot ${state}`;
}

async function refreshSystemStatus(force = false) {
  try {
    const status = await api(`/api/system-status${force ? "?refresh=true" : ""}`);
    if (googleStatusText && googleStatusMeta) {
      if (status.google.ok === null) {
        googleStatusText.textContent = "Checking";
        googleStatusMeta.textContent = "Runs in background";
        setDot(googleStatusDot, "unknown");
      } else {
        googleStatusText.textContent = status.google.ok ? "Connected" : "Offline";
        googleStatusMeta.textContent = status.google.ok
          ? `${status.google.latency_ms} ms`
          : status.google.message;
        setDot(googleStatusDot, status.google.ok ? "ok" : "bad");
      }
    }

    if (diskStatusText && diskStatusMeta) {
      const freePercent = Number(status.disk.percent_free || 0);
      diskStatusText.textContent = `${status.disk.free_human} free`;
      diskStatusMeta.textContent = `${freePercent.toFixed(1)}% free of ${status.disk.total_human}`;
      setDot(diskStatusDot, freePercent < 10 ? "bad" : freePercent < 20 ? "warn" : "ok");
    }
  } catch (error) {
    if (googleStatusText && googleStatusMeta) {
      googleStatusText.textContent = "Unknown";
      googleStatusMeta.textContent = error.message;
      setDot(googleStatusDot, "unknown");
    }
    if (diskStatusText && diskStatusMeta) {
      diskStatusText.textContent = "Unknown";
      diskStatusMeta.textContent = error.message;
      setDot(diskStatusDot, "unknown");
    }
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {"Content-Type": "application/json"},
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return body;
}

async function stopJob(jobId) {
  try {
    await api(`/api/jobs/${jobId}`, {method: "DELETE"});
    await refreshJobs();
  } catch (error) {
    if (jobState) jobState.textContent = error.message;
  }
}

async function startJob(taskId) {
  if (jobState) jobState.textContent = "Starting";
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({task_id: taskId}),
    });
    selectedJobId = data.job.id;
    await refreshJobs();
  } catch (error) {
    if (jobState) jobState.textContent = error.message;
  }
}

function renderJobs(jobs) {
  if (!jobsTable || !jobState) return;

  allJobs = jobs;
  if (!jobs.length) {
    jobsTable.innerHTML = '<tr><td colspan="7" class="empty">No jobs yet.</td></tr>';
    jobState.textContent = "Idle";
    updateJobPagination(0);
    return;
  }

  const running = jobs.filter((job) => job.status === "running").length;
  jobState.textContent = running ? `${running} running` : `${jobs.length} total`;

  const totalPages = Math.max(1, Math.ceil(jobs.length / jobsPerPage));
  jobsPage = Math.min(Math.max(1, jobsPage), totalPages);
  const start = (jobsPage - 1) * jobsPerPage;
  const visibleJobs = jobs.slice(start, start + jobsPerPage);

  jobsTable.innerHTML = visibleJobs.map((job) => {
    const stopBtn = job.status === "running"
      ? `<button class="danger" type="button" data-stop-job-id="${job.id}">Stop</button>`
      : "";
    return `
    <tr>
      <td>${escapeHtml(job.task_name)}</td>
      <td><span class="${statusClass(job.status)}">${escapeHtml(job.status)}</span></td>
      <td>${renderProgress(job.progress)}</td>
      <td>${escapeHtml(job.progress?.eta || "")}</td>
      <td>${job.pid || ""}</td>
      <td>${formatTime(job.started_at)}</td>
      <td>${stopBtn}<button class="secondary" type="button" data-job-id="${job.id}">Log</button></td>
    </tr>`;
  }).join("");

  $$("[data-job-id]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedJobId = button.dataset.jobId;
      logLoaded = true;
      refreshLog(true);
    });
  });

  $$("[data-stop-job-id]").forEach((button) => {
    button.addEventListener("click", () => stopJob(button.dataset.stopJobId));
  });

  if (!selectedJobId) {
    selectedJobId = jobs[0].id;
  }
  updateJobPagination(jobs.length);
}

function updateJobPagination(totalJobs) {
  const totalPages = Math.max(1, Math.ceil(totalJobs / jobsPerPage));
  if (jobsPageInfo) {
    jobsPageInfo.textContent = totalJobs ? `Page ${jobsPage} of ${totalPages}` : "Page 1 of 1";
  }
  if (jobsPrevBtn) {
    jobsPrevBtn.disabled = jobsPage <= 1 || totalJobs === 0;
  }
  if (jobsNextBtn) {
    jobsNextBtn.disabled = jobsPage >= totalPages || totalJobs === 0;
  }
}

function changeJobsPage(direction) {
  const totalPages = Math.max(1, Math.ceil(allJobs.length / jobsPerPage));
  jobsPage = Math.min(Math.max(1, jobsPage + direction), totalPages);
  renderJobs(allJobs);
}

async function refreshJobs() {
  if (isConfigModalOpen()) return;
  try {
    const data = await api("/api/jobs");
    renderJobs(data.jobs);
  } catch (error) {
    if (jobState) jobState.textContent = error.message;
  }
}

function shouldAutoRefreshLog() {
  return Boolean(autoLogRefreshToggle && autoLogRefreshToggle.checked);
}

async function refreshLog(force = false) {
  if (isConfigModalOpen()) return;
  if (!force && (!logLoaded || !shouldAutoRefreshLog())) return;
  if (!selectedJobId || !logOutput || !activeJob) return;
  try {
    const response = await fetch(`/jobs/${selectedJobId}/log?tail_bytes=81920`);
    const text = await response.text();
    logOutput.textContent = text || "No log output yet.";
    activeJob.textContent = selectedJobId;
    logOutput.scrollTop = logOutput.scrollHeight;
  } catch (error) {
    logOutput.textContent = error.message;
  }
}

async function loadConfigText() {
  const data = await api("/api/config");
  currentConfigText = data.content || "";
  return currentConfigText;
}

function findSectionRange(configText, sectionName) {
  const lines = configText.split(/\r?\n/);
  const startIndex = lines.findIndex((line) => line.trim() === `${sectionName}:`);
  if (startIndex === -1) return null;

  let endIndex = lines.length;
  for (let index = startIndex + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() && !line.startsWith(" ") && !line.startsWith("\t") && !line.startsWith("#")) {
      endIndex = index;
      break;
    }
  }

  return {startIndex, endIndex, lines};
}

function extractSection(configText, sectionName) {
  const range = findSectionRange(configText, sectionName);
  if (!range) return `${sectionName}:\n`;
  return range.lines.slice(range.startIndex, range.endIndex).join("\n");
}

function replaceSection(configText, sectionName, sectionText) {
  const normalized = sectionText.trimEnd();
  const range = findSectionRange(configText, sectionName);
  if (!range) {
    return `${configText.trimEnd()}\n\n${normalized}\n`;
  }

  const before = range.lines.slice(0, range.startIndex);
  const after = range.lines.slice(range.endIndex);
  return [...before, normalized, ...after].join("\n");
}

function stripInlineComment(value) {
  let quote = null;
  for (let index = 0; index < value.length; index += 1) {
    const char = value[index];
    if ((char === '"' || char === "'") && value[index - 1] !== "\\") {
      quote = quote === char ? null : quote || char;
    }
    if (char === "#" && !quote && (index === 0 || /\s/.test(value[index - 1]))) {
      return value.slice(0, index).trimEnd();
    }
  }
  return value.trim();
}

function parseScalar(value) {
  const trimmed = stripInlineComment(value);
  if (!trimmed) return "";
  if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
    return trimmed.slice(1, -1);
  }
  if (/^(true|false)$/i.test(trimmed)) return trimmed.toLowerCase() === "true";
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  return trimmed;
}

function inferControlType(values) {
  if (values.length === 1 && typeof values[0] === "boolean") return "boolean";
  if (values.length === 1 && typeof values[0] === "number") return "number";
  if (values.length <= 1) return "text";
  if (values.every((value) => typeof value === "number")) return "number-list";
  return "list";
}

function parseConfigSection(sectionText, sectionName) {
  const lines = sectionText.split(/\r?\n/);
  if (lines[0]?.trim() !== `${sectionName}:`) return null;

  const fields = [];
  let currentField = null;

  for (const line of lines.slice(1)) {
    if (!line.trim() || line.trimStart().startsWith("#")) continue;

    const keyMatch = line.match(/^ {2}([A-Za-z0-9_]+):\s*(.*?)\s*$/);
    if (keyMatch) {
      currentField = {
        key: keyMatch[1],
        values: [],
      };
      const inlineValue = stripInlineComment(keyMatch[2]);
      if (inlineValue) currentField.values.push(parseScalar(inlineValue));
      fields.push(currentField);
      continue;
    }

    const itemMatch = line.match(/^ {2,4}-\s*(.*?)\s*$/);
    if (itemMatch && currentField) {
      currentField.values.push(parseScalar(itemMatch[1]));
      continue;
    }

    return null;
  }

  return fields.map((field) => ({
    ...field,
    controlType: inferControlType(field.values),
  }));
}

function formatYamlScalar(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);

  const text = String(value ?? "");
  if (!text) return '""';
  if (/^(true|false|null|~)$/i.test(text) || /^-?\d+(\.\d+)?$/.test(text) || /[:#\[\]{},&*!|>'"%@`]/.test(text) || /^\s|\s$/.test(text)) {
    return JSON.stringify(text);
  }
  return text;
}

function configValuesFromControl(control, controlType) {
  if (controlType === "boolean") return [control.checked];
  if (controlType === "number") return [Number(control.value || 0)];

  if (controlType === "number-list") {
    return control.value
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean)
      .map(Number);
  }

  if (controlType === "list") {
    return control.value
      .split(/\r?\n/)
      .map((value) => value.trim())
      .filter(Boolean);
  }

  return [control.value];
}

function sectionTextFromForm(sectionName) {
  if (!modalForm) return "";
  const lines = [`${sectionName}:`];
  $$("[data-config-key]").forEach((control) => {
    const values = configValuesFromControl(control, control.dataset.configType);
    lines.push(`  ${control.dataset.configKey}:`);
    if (!values.length) {
      lines.push("    - \"\"");
      return;
    }
    values.forEach((value) => {
      lines.push(`    - ${formatYamlScalar(value)}`);
    });
  });
  return lines.join("\n");
}

function renderConfigForm(fields) {
  if (!modalForm) return;
  modalForm.innerHTML = fields.map((field) => {
    const id = `configField_${field.key}`;
    const value = field.values[0] ?? "";
    if (field.controlType === "boolean") {
      return `
        <div class="config-field">
          <label for="${escapeHtml(id)}">${escapeHtml(field.key)}</label>
          <input id="${escapeHtml(id)}" class="config-checkbox" type="checkbox" data-config-key="${escapeHtml(field.key)}" data-config-type="${field.controlType}" ${value ? "checked" : ""}>
        </div>`;
    }
    if (field.controlType === "number") {
      return `
        <div class="config-field">
          <label for="${escapeHtml(id)}">${escapeHtml(field.key)}</label>
          <input id="${escapeHtml(id)}" class="config-input" type="number" step="any" data-config-key="${escapeHtml(field.key)}" data-config-type="${field.controlType}" value="${escapeHtml(value)}">
        </div>`;
    }
    if (field.controlType === "list" || field.controlType === "number-list") {
      return `
        <div class="config-field">
          <label for="${escapeHtml(id)}">${escapeHtml(field.key)}</label>
          <textarea id="${escapeHtml(id)}" class="config-textarea" data-config-key="${escapeHtml(field.key)}" data-config-type="${field.controlType}" spellcheck="false">${escapeHtml(field.values.join("\n"))}</textarea>
        </div>`;
    }
    return `
      <div class="config-field">
        <label for="${escapeHtml(id)}">${escapeHtml(field.key)}</label>
        <input id="${escapeHtml(id)}" class="config-input" type="text" data-config-key="${escapeHtml(field.key)}" data-config-type="${field.controlType}" value="${escapeHtml(value)}">
      </div>`;
  }).join("");
}

function showRawConfigEditor(sectionText) {
  activeConfigMode = "raw";
  if (modalForm) modalForm.hidden = true;
  if (modalEditor) {
    modalEditor.hidden = false;
    modalEditor.classList.add("is-active");
    modalEditor.value = sectionText;
    modalEditor.focus();
  }
}

function showConfigForm(fields) {
  activeConfigMode = "form";
  if (modalEditor) {
    modalEditor.hidden = true;
    modalEditor.classList.remove("is-active");
  }
  if (modalForm) {
    modalForm.hidden = false;
    renderConfigForm(fields);
    const firstControl = modalForm.querySelector("input, textarea");
    if (firstControl) firstControl.focus();
  }
}

function closeConfigModal() {
  if (!configModal) return;
  configModal.hidden = true;
  activeConfigTaskId = null;
  activeConfigSection = null;
  activeConfigMode = "form";
}

async function openConfigModal(button) {
  if (!configModal || !modalForm || !modalEditor || !modalTitle || !modalHint || !modalState) return;

  activeConfigTaskId = button.dataset.configTaskId;
  activeConfigSection = button.dataset.configSection;
  modalTitle.textContent = `${button.dataset.taskName || "Task"} Config`;
  modalHint.textContent = activeConfigSection;
  modalState.textContent = "Loading";
  modalForm.innerHTML = "";
  modalForm.hidden = false;
  modalEditor.hidden = true;
  modalEditor.classList.remove("is-active");
  configModal.hidden = false;

  try {
    const configText = await loadConfigText();
    const sectionText = extractSection(configText, activeConfigSection);
    const fields = parseConfigSection(sectionText, activeConfigSection);
    if (fields) {
      showConfigForm(fields);
      modalState.textContent = "Loaded controls";
    } else {
      showRawConfigEditor(sectionText);
      modalState.textContent = "Loaded YAML";
    }
  } catch (error) {
    modalState.textContent = error.message;
  }
}

async function saveModalConfig() {
  if (!modalEditor || !modalForm || !modalState || !activeConfigSection) return false;

  modalState.textContent = "Saving";
  try {
    const configText = currentConfigText || await loadConfigText();
    const sectionText = activeConfigMode === "form"
      ? sectionTextFromForm(activeConfigSection)
      : modalEditor.value;
    const nextConfig = replaceSection(configText, activeConfigSection, sectionText);
    await api("/api/config", {
      method: "PUT",
      body: JSON.stringify({content: nextConfig}),
    });
    currentConfigText = nextConfig;
    modalState.textContent = "Saved";
    return true;
  } catch (error) {
    modalState.textContent = error.message;
    return false;
  }
}

function bindOptional(selector, eventName, handler) {
  const element = $(selector);
  if (element) {
    element.addEventListener(eventName, handler);
  }
}

$$("[data-task-id]").forEach((button) => {
  button.addEventListener("click", () => startJob(button.dataset.taskId));
});

$$("[data-config-task-id]").forEach((button) => {
  button.addEventListener("click", () => openConfigModal(button));
});

bindOptional("#refreshBtn", "click", async () => {
  if (isConfigModalOpen()) return;
  await refreshSystemStatus(true);
  await refreshJobs();
  await refreshLog(false);
});

bindOptional("#jobsPrevBtn", "click", () => changeJobsPage(-1));
bindOptional("#jobsNextBtn", "click", () => changeJobsPage(1));
bindOptional("#loadLogBtn", "click", () => {
  logLoaded = true;
  refreshLog(true);
});
bindOptional("#scrollTopBtn", "click", () => window.scrollTo({top: 0, behavior: "smooth"}));
bindOptional("#scrollBottomBtn", "click", () => window.scrollTo({top: document.body.scrollHeight, behavior: "smooth"}));

bindOptional("#modalCloseBtn", "click", closeConfigModal);
bindOptional("#modalCancelBtn", "click", closeConfigModal);
if (modalForm) {
  modalForm.addEventListener("submit", (event) => event.preventDefault());
}
bindOptional("#modalSaveBtn", "click", async () => {
  const saved = await saveModalConfig();
  if (saved) closeConfigModal();
});
bindOptional("#modalSaveRunBtn", "click", async () => {
  const saved = await saveModalConfig();
  if (saved && activeConfigTaskId) {
    const taskId = activeConfigTaskId;
    closeConfigModal();
    await startJob(taskId);
  }
});

if (configModal) {
  configModal.addEventListener("click", (event) => {
    if (event.target === configModal) {
      closeConfigModal();
    }
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && configModal && !configModal.hidden) {
    closeConfigModal();
  }
});

refreshJobs();
refreshSystemStatus();
setInterval(refreshJobs, 3000);
setInterval(() => refreshLog(false), 3000);
setInterval(refreshSystemStatus, 60000);
