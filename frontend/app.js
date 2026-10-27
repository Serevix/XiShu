"use strict";
// 页面内容通过文本节点显示，避免把数据或模型输出当成 HTML 执行。
const state = {token: sessionStorage.getItem("analytics_token"), datasets: [], tasks: [], datasetId: null, taskId: null, previewId: null, stream: null, blobs: [], toastTimer: null};
const labels = {uploaded:"待确认", importing:"导入中", ready:"已就绪", queued:"等待执行", running:"分析中", waiting_input:"等待补充", succeeded:"已完成", failed:"失败", cancelled:"已取消"};
// taskId 表示一次分析，conversationId 关联多轮分析。选择已有任务不等于创建新会话。
state.conversationId = null; state.historyCursor = 0;
state.artifactFilter = "all"; state.taskStatus = null; state.submitting = false;
state.historyLoading = false; state.historyRequest = 0;
state.artifactRequest = 0;

/** 根据元素 ID 查找页面控件。 */
function byId(id) { return document.getElementById(id); }

/** 创建 DOM 元素，并按需要设置类名和文本，不解析输入中的 HTML。 */
function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = String(text);
  return item;
}

/** 显示操作提示，并在几秒后隐藏。新的提示会替换旧提示的隐藏计时。 */
function toast(message) {
  byId("toast").textContent = message; byId("toast").hidden = false;
  clearTimeout(state.toastTimer); state.toastTimer = setTimeout(hideToast, 5000);
}

/** 隐藏操作提示，不修改后台任务状态。 */
function hideToast() { byId("toast").hidden = true; }

/** 请求项目接口，在请求头中附上登录令牌，并统一处理失败响应。 */
async function api(path, options = {}) {
  const headers = {...options.headers};
  // 登录令牌放在请求头中。FormData 的 Content-Type 由浏览器设置，以保留上传所需的 boundary。
  if (state.token) headers.Authorization = "Bearer " + state.token;
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  if (!response.ok) {
    let body; try { body = await response.json(); } catch { body = {}; }
    const message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || "请求失败");
    if (response.status === 401 && !path.includes("/auth/")) logout();
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response;
}

/** 完成注册或登录后，把令牌保存到当前标签页的 sessionStorage，再打开工作台。 */
async function authenticate(kind) {
  if (!byId("auth-form").reportValidity()) return;
  const buttons = byId("auth-form").querySelectorAll("button");
  for (const button of buttons) button.disabled = true;
  byId("auth-error").textContent = "";
  try {
    const response = await api("/api/auth/" + kind, {method:"POST", body:JSON.stringify({username:byId("username").value, password:byId("password").value})});
    const body = await response.json(); state.token = body.token;
    sessionStorage.setItem("analytics_token", body.token); byId("password").value = "";
    await enterWorkspace();
  } catch (error) { byId("auth-error").textContent = error.message; }
  finally { for (const button of buttons) button.disabled = false; }
}

/** 处理登录表单，并阻止浏览器默认提交导致的页面刷新。 */
async function login(event) { event.preventDefault(); await authenticate("login"); }

/** 注册本项目账号，成功后进入工作台。 */
async function register() { await authenticate("register"); }

/** 删除当前标签页的登录令牌，并停止正在读取的任务事件。 */
function logout() {
  if (state.stream) state.stream.abort();
  state.token = null; state.datasetId = null; state.taskId = null;
  state.conversationId = null; state.historyCursor = 0; state.taskStatus = null; state.submitting = false;
  byId("memory-list").replaceChildren(); byId("memory-suggestions").replaceChildren();
  closeHistory(); byId("memory-panel").open = false;
  byId("frequent-questions").hidden = true; byId("memory-count").textContent = "尚未加载";
  state.datasets = []; state.tasks = []; state.previewId = null;
  for (const url of state.blobs) URL.revokeObjectURL(url); state.blobs = [];
  byId("events").replaceChildren(); byId("artifacts").replaceChildren();
  byId("results-panel").hidden = true; byId("preview-panel").hidden = true;
  byId("resume-form").hidden = true; byId("upload-panel").hidden = true;
  byId("task-info").textContent = "选择一个任务查看执行记录。";
  byId("question").value = ""; byId("answer").value = "";
  updateQuestionCount(); byId("conversation-label").textContent = "新会话";
  renderDatasets(); renderTasks();
  sessionStorage.removeItem("analytics_token"); byId("workspace").hidden = true; byId("auth-screen").hidden = false;
}

/** 检查登录是否有效，随后加载当前用户的数据集和任务。 */
async function enterWorkspace() {
  const response = await api("/api/auth/me"); const user = await response.json();
  byId("account").textContent = user.username; byId("auth-screen").hidden = true; byId("workspace").hidden = false;
  await refreshDashboard(); renderDatasets(); renderTasks(); await checkHealth(); await refreshMemories();
}

/** 读取各项依赖是否可用，以及模型是否已配置，不显示连接凭据。 */
async function checkHealth() {
  try {
    const response = await api("/api/health"); const body = await response.json();
    byId("health").textContent = body.database && body.redis ? "● 服务已连接" : "● 服务连接异常";
    byId("mode").options[1].disabled = !body.model_ready;
    if (!body.model_ready) byId("mode").options[1].textContent = "真实模型 · 待配置 Key";
    byId("mode").value = body.model_ready ? body.default_mode : "demo";
    changeMode();
  } catch { byId("health").textContent = "● 服务暂时不可用"; }
}

/** 重新读取数据集和任务列表，并保留当前选择。 */
async function refreshDashboard() {
  const token = state.token;
  try {
    const dataResponse = await api("/api/datasets"); const datasets = await dataResponse.json();
    const taskResponse = await api("/api/analysis-tasks"); const tasks = await taskResponse.json();
    if (state.token !== token) return;
    const dataChanged = JSON.stringify(state.datasets) !== JSON.stringify(datasets);
    const tasksChanged = JSON.stringify(state.tasks) !== JSON.stringify(tasks);
    state.datasets = datasets; state.tasks = tasks;
    if (!state.datasetId && state.datasets.length) state.datasetId = state.datasets[0].id;
    if (dataChanged) renderDatasets();
    if (tasksChanged) renderTasks();
  } catch (error) { toast(error.message); }
}

/** 定期检查数据集导入状态，没有登录令牌时不发请求。 */
async function pollDatasets() {
  if (!state.token) return;
  await refreshDashboard();
}

/** 显示数据集列表和当前统计口径，输入内容通过文本节点呈现。 */
function renderDatasets() {
  const container = byId("datasets"); container.replaceChildren();
  if (!state.datasets.length) container.append(element("p", "empty-state", "加载模拟示例，或上传你的数据。"));
  for (const dataset of state.datasets) {
    const button = element("button", "dataset-button" + (dataset.id === state.datasetId ? " active" : ""), dataset.name);
    button.dataset.id = dataset.id; button.addEventListener("click", selectDataset);
    button.setAttribute("aria-pressed", String(dataset.id === state.datasetId));
    button.append(element("small", "", (labels[dataset.status] || dataset.status) + " · " + (dataset.source_type === "synthetic" ? "模拟数据" : "用户上传")));
    container.append(button);
  }
  let selected = null; for (const dataset of state.datasets) if (dataset.id === state.datasetId) selected = dataset;
  if (selected) {
    byId("selected-name").textContent = selected.name; byId("selected-status").textContent = labels[selected.status] || selected.status;
    const status = selected.metric_definitions.valid_status;
    byId("selected-meta").textContent = selected.error || (selected.source_type === "synthetic" ? "模拟数据" : "已上传数据") + " · 币种 " + selected.metric_definitions.currency + " · 有效订单：" + (status === "delivered" ? "已送达" : status);
  } else {
    byId("selected-name").textContent = "选择数据，开始探索";
    byId("selected-status").textContent = "未选择";
    byId("selected-meta").textContent = "可以先加载模拟示例，体验完整分析流程。";
  }
  byId("selected-status").dataset.status = selected ? selected.status : "none";
  const stats = byId("dataset-stats"); stats.replaceChildren(); stats.hidden = !selected || selected.status !== "ready";
  if (selected && selected.status === "ready") {
    for (const [key, caption] of [["orders","订单记录"],["products","商品"],["customers","客户记录"],["order_items","商品明细"]]) {
      const card = element("div", "stat-item"); const value = selected.summary?.[key];
      card.append(element("strong", "", typeof value === "number" ? value.toLocaleString("zh-CN") : "—"), element("span", "", caption)); stats.append(card);
    }
  }
  updateAnalyzeButton();
}

/** 切换当前数据集。对于待确认或导入失败的数据集，可以重新打开预览。 */
async function selectDataset(event) {
  if (state.datasetId !== event.currentTarget.dataset.id) newConversation();
  state.datasetId = event.currentTarget.dataset.id; renderDatasets();
  for (const dataset of state.datasets) if (dataset.id === state.datasetId && ["uploaded","failed"].includes(dataset.status)) await showPreview(dataset.id);
}

/** 创建模拟数据集，并刷新列表以显示后台导入进度。 */
async function loadDemo() {
  newConversation();
  byId("demo").disabled = true;
  try {
    const response = await api("/api/datasets/demo", {method:"POST"}); const data = await response.json();
    state.datasetId = data.id; await refreshDashboard(); toast("模拟数据已提交导入，请稍候。");
  } catch (error) { toast(error.message); }
  finally { byId("demo").disabled = false; }
}

/** 切换文件上传表单的显示状态。 */
function toggleUpload() { byId("upload-panel").hidden = !byId("upload-panel").hidden; }

/** 上传数据文件，成功后打开字段映射和统计口径的预览表单。 */
async function uploadFiles(event) {
  event.preventDefault(); const data = new FormData(); data.append("name", byId("dataset-name").value);
  for (const file of byId("files").files) data.append("files", file);
  try {
    const response = await api("/api/datasets", {method:"POST", body:data}); const body = await response.json();
    newConversation(); state.datasetId = body.id; await refreshDashboard(); await showPreview(body.id); byId("upload-panel").hidden = true;
  } catch (error) { toast(error.message); }
}

/** 显示源列、样例和字段下拉框，让用户检查并修改建议映射。 */
async function showPreview(identifier) {
  try {
    const response = await api("/api/datasets/" + identifier + "/preview?encoding=" + byId("encoding").value);
    const preview = await response.json(); state.previewId = identifier; byId("preview-panel").hidden = false;
    const container = byId("mapping"); container.replaceChildren();
    for (const [table, body] of Object.entries(preview)) {
      const group = element("section", "mapping-group"); group.append(element("h3", "", table));
      const fields = element("div", "mapping-fields");
      for (const field of body.fields) {
        const label = element("label", "", field); const select = element("select"); select.dataset.table = table; select.dataset.field = field;
        select.append(new Option("未提供（可选字段）", ""));
        for (const column of body.columns) select.append(new Option(column, column));
        select.value = body.mapping[field] || ""; label.append(select); fields.append(label);
      }
      group.append(fields, element("pre", "mapping-sample", JSON.stringify(body.rows, null, 2))); container.append(group);
    }
  } catch (error) { toast(error.message); }
}

/** 文件编码改变后重新读取预览，不自动猜测其他编码。 */
async function changeEncoding() { if (state.previewId) await showPreview(state.previewId); }

/** 提交用户确认的字段映射和口径，让后台开始校验并导入数据。 */
async function confirmImport() {
  const mapping = {};
  for (const select of byId("mapping").querySelectorAll("select")) {
    if (!mapping[select.dataset.table]) mapping[select.dataset.table] = {};
    if (select.value) mapping[select.dataset.table][select.dataset.field] = select.value;
  }
  try {
    await api("/api/datasets/" + state.previewId + "/import", {method:"POST", body:JSON.stringify({mapping, currency:byId("currency").value, valid_status:byId("valid-status").value, encoding:byId("encoding").value})});
    byId("preview-panel").hidden = true; toast("导入已提交，完成后可创建分析任务。"); await refreshDashboard();
  } catch (error) { toast(error.message); }
}

/** 把示例问题填入输入框，用户仍可编辑，不立即提交分析。 */
function fillQuestion(event) { byId("question").value = event.currentTarget.dataset.question; updateQuestionCount(); byId("question").focus(); }

/** 显示输入框的字符数，填入模板后也更新计数。 */
function updateQuestionCount() { byId("question-count").textContent = byId("question").value.length + " / 4000"; }

/** 根据数据集就绪状态、任务状态和提交进度决定按钮是否可用，避免重复提交。 */
function updateAnalyzeButton() {
  let ready = false; for (const dataset of state.datasets) if (dataset.id === state.datasetId) ready = dataset.status === "ready";
  const active = state.conversationId && ["queued","running","waiting_input"].includes(state.taskStatus);
  byId("analyze").disabled = !ready || state.submitting || Boolean(active);
  byId("analyze").textContent = state.submitting ? "正在提交…" : active ? (state.taskStatus === "waiting_input" ? "请先补充信息" : "正在分析…") : "开始分析 ↗";
}

/** 显示当前采用规则演示还是模型自主工具调用。 */
function changeMode() {
  byId("mode-notice").textContent = byId("mode").value === "demo" ? "体验月度、品类、地区和商品分析。该模式不调用聊天模型；记忆索引仍可能调用向量模型。" : "根据当前数据选择分析步骤，结果保留计算依据。你可以在同一会话继续追问。";
}

/** 提交分析问题，获取任务记录后订阅执行进度。 */
async function submitAnalysis(event) {
  event.preventDefault(); if (state.submitting) return; state.submitting = true; updateAnalyzeButton();
  try {
    const response = await api("/api/analysis-tasks", {method:"POST", body:JSON.stringify({dataset_id:state.datasetId, conversation_id:state.conversationId, question:byId("question").value, mode:byId("mode").value})});
    const task = await response.json(); state.taskId = task.id; await refreshDashboard(); await openTask(task.id);
  } catch (error) { toast(error.message); }
  finally { state.submitting = false; updateAnalyzeButton(); }
}

/** 显示最近的分析任务，并标明任务状态和运行模式。 */
function renderTasks() {
  const container = byId("tasks"); container.replaceChildren();
  byId("task-count").textContent = state.tasks.length;
  if (!state.tasks.length) container.append(element("p", "empty-state", "提交第一个问题，分析记录会保存在这里。"));
  for (const task of state.tasks) {
    const button = element("button", "task-button" + (task.id === state.taskId ? " active" : ""));
    button.dataset.id = task.id; button.addEventListener("click", selectTask);
    button.title = task.question; button.setAttribute("aria-pressed", String(task.id === state.taskId));
    const meta = element("div", "task-meta"); const badge = element("span", "pill", labels[task.status] || task.status); badge.dataset.status = task.status;
    meta.append(badge, element("span", "", (task.mode === "demo" ? "规则演示" : "真实模型") + " · " + formatTime(task.created_at)));
    button.append(element("span", "task-title", task.question), meta);
    container.append(button);
  }
}

/** 把保存的时间转换为北京时间显示。 */
function formatTime(value) {
  const date = new Date(value); if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);
}

/** 处理任务选择，读取该任务已保存的状态、事件和结果。 */
async function selectTask(event) { await openTask(event.currentTarget.dataset.id); }

/** 显示任务详情。任务还在排队或执行时，继续读取新的步骤事件。 */
async function openTask(identifier) {
  if (state.taskId !== identifier) state.artifactFilter = "all";
  // 切换任务时只关闭旧事件连接，后台分析仍继续。真正取消分析需要调用取消接口。
  if (state.stream) state.stream.abort(); state.taskId = identifier; renderTasks();
  try {
    const response = await api("/api/analysis-tasks/" + identifier); const task = await response.json();
    // 等待请求时用户可能已经切换任务，所以返回后再次检查选择，避免旧结果覆盖当前页面。
    if (state.taskId !== identifier) return;
    if (state.conversationId !== task.conversation_id) { closeHistory(); state.historyCursor = 0; }
    state.conversationId = task.conversation_id; state.datasetId = task.dataset_id; renderDatasets();
    byId("conversation-label").textContent = state.conversationId ? "延续当前会话" : "历史任务；下次将创建新会话";
    byId("events").replaceChildren(); for (const event of task.events) renderEvent(event);
    renderTaskState(task); await renderArtifacts(task);
    if (["queued","running"].includes(task.status)) streamTask(identifier, task.events.length);
  } catch (error) { toast(error.message); }
}

/** 根据任务状态显示错误、补充信息表单和取消按钮。 */
function renderTaskState(task) {
  state.taskStatus = task.status;
  const container = byId("task-info"); container.replaceChildren();
  const badge = element("span", "pill", labels[task.status] || task.status); badge.dataset.status = task.status;
  container.append(badge, element("span", "", " " + task.question));
  if (task.error) container.append(element("p", "error", task.error));
  byId("cancel").hidden = !["queued","running","waiting_input"].includes(task.status);
  byId("resume-form").hidden = task.status !== "waiting_input";
  byId("clarification-label").textContent = task.clarification || "";
  updateAnalyzeButton();
}

/** 显示分析步骤，并提供可展开的参数和结果记录。所有输入作为文本显示。 */
function renderEvent(event) {
  const names = {started:"开始执行", memory_loaded:"读取个性化与历史", context_summarized:"旧消息已总结", context_fallback:"采用近期消息与事实视图", memory_pending:"记忆等待后台处理", model_started:"模型正在分析", model_completed:"模型返回决策", decision:"选择工具", tool_batch_started:"并行分析多个指标", tool_batch_completed:"本轮工具已完成", tool_started:"调用工具", tool_completed:"工具完成", tool_error:"工具错误", verified:"依据检查通过", verification_error:"依据检查失败", report_fallback:"交付已核验结果，说明已降级", waiting_input:"等待补充", report_ready:"报告就绪", completed:"任务完成", failed:"执行失败", cancelled:"已取消"};
  const timing = event.type === "model_completed" && event.payload.seconds !== null ? " · " + event.payload.seconds + " 秒" : "";
  const tools = {get_dataset_schema:"读取数据结构",execute_sql:"查询数据",tool_batch:"多个工具",sales_metrics:"销售指标计算",customer_repeat_metrics:"客户复购计算",run_python:"计算分析",render_chart:"生成图表",finish:"整理报告"};
  const verbose = ["decision","model_started","model_completed","tool_completed","verified","report_ready"].includes(event.type);
  const failure = ["tool_error","verification_error","failed"].includes(event.type);
  const item = element("li", (verbose ? "verbose-event " : "") + (failure ? "event-error" : ""), (names[event.type] || event.type) + (event.payload.tool ? " · " + (tools[event.payload.tool] || event.payload.tool) : "") + timing);
  const details = element("details"); details.append(element("summary", "", "查看记录"), element("pre", "", JSON.stringify(event.payload, null, 2))); item.append(details);
  byId("events").append(item);
}

/** 通过 fetch 读取步骤事件，支持按序号补读，并在请求头中发送令牌。 */
async function streamTask(identifier, after) {
  const controller = new AbortController(); state.stream = controller;
  for (let attempt = 0; attempt < 6 && state.taskId === identifier && !controller.signal.aborted; attempt++) {
    try {
      const response = await api("/api/analysis-tasks/" + identifier + "/events?after=" + after, {signal:controller.signal});
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      try {
        while (true) {
          const chunk = await reader.read(); if (chunk.done) break;
          // 流式解码保留半个字符，buffer 保留半条事件。补读时只接受比已读序号大的事件。
          buffer += decoder.decode(chunk.value, {stream:true}); const parts = buffer.split("\n\n"); buffer = parts.pop();
          for (const part of parts) {
            let raw = ""; for (const line of part.split("\n")) if (line.startsWith("data: ")) raw = line.slice(6);
            if (!raw || state.taskId !== identifier) continue;
            const event = JSON.parse(raw);
            if (event.seq > after) { renderEvent(event); after = event.seq; }
          }
        }
      } finally {
        await reader.cancel().catch(ignoreStreamClose); reader.releaseLock();
      }
      if (state.taskId !== identifier || controller.signal.aborted) return;
      await reloadTaskResult(identifier);
      if (["succeeded","failed","cancelled","waiting_input"].includes(state.taskStatus)) return;
    } catch (error) {
      if (error.name === "AbortError" || [401,403,404].includes(error.status) || controller.signal.aborted) return;
      if (attempt === 0 && state.taskId === identifier) toast("进度连接中断，正在自动恢复；后台分析继续执行。");
      if (attempt === 5 && state.taskId === identifier) toast("自动重连暂未成功，可点击任务继续查看进度。");
    }
    if (attempt < 5) await reconnectDelay(Math.min(8000, 1000 * 2 ** attempt), controller.signal);
  }
}

/** 连接已经关闭时无需再向用户报告清理错误。 */
function ignoreStreamClose() {}

/** 重连退避可被切换任务或退出登录中断，避免旧任务稍后重新建立连接。 */
function reconnectDelay(milliseconds, signal) {
  return new Promise(function waitForReconnect(resolve) {
    const timer = setTimeout(finishDelay, milliseconds);
    /** 等待结束或收到取消信号时只清理本次计时器与监听器。 */
    function finishDelay() { clearTimeout(timer); signal.removeEventListener("abort", finishDelay); resolve(); }
    signal.addEventListener("abort", finishDelay, {once:true});
    if (signal.aborted) finishDelay();
  });
}

/** 事件连接结束后读取最新任务状态和结果，不重新建立事件连接。 */
async function reloadTaskResult(identifier) {
  const response = await api("/api/analysis-tasks/" + identifier); const task = await response.json();
  if (state.taskId !== identifier) return;
  renderTaskState(task); await renderArtifacts(task); await refreshDashboard();
  await refreshMemories();
}

/** 提交用户补充的信息，让后台从原任务保存的进度继续执行。 */
async function resumeAnalysis(event) {
  event.preventDefault();
  try {
    await api("/api/analysis-tasks/" + state.taskId + "/resume", {method:"POST", body:JSON.stringify({answer:byId("answer").value})});
    byId("answer").value = ""; await openTask(state.taskId);
  } catch (error) { toast(error.message); }
}

/** 向接口请求取消当前任务，然后读取取消后的状态。 */
async function cancelAnalysis() {
  try { await api("/api/analysis-tasks/" + state.taskId + "/cancel", {method:"POST"}); await openTask(state.taskId); }
  catch (error) { toast(error.message); }
}

/** 通过带登录令牌的请求下载结果，不把令牌放在公开下载 URL 中。 */
async function downloadArtifact(event) {
  const button = event.currentTarget;
  try {
    const response = await api("/api/artifacts/" + button.dataset.id + (button.dataset.kind === "table" ? "?format=csv" : ""));
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = element("a"); link.href = url;
    link.download = button.dataset.kind + "-" + button.dataset.id.slice(0,8) + (button.dataset.kind === "table" ? ".csv" : button.dataset.kind === "chart" ? ".png" : ".md");
    link.click(); URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
}

/** 显示表格的有限行预览，完整结果通过文件下载查看。 */
function tablePreview(artifact) {
  const wrapper = element("div", "table-wrap"); const table = element("table", "result-table");
  const thead = element("thead"); const head = element("tr");
  for (const column of artifact.columns) { const cell = element("th", "", column); cell.scope = "col"; head.append(cell); }
  thead.append(head); table.append(thead); const tbody = element("tbody");
  for (const row of artifact.preview) {
    const line = element("tr"); for (const column of artifact.columns) line.append(element("td", "", row[column] === null ? "无定义" : row[column])); tbody.append(line);
  }
  table.append(tbody); wrapper.append(table); return wrapper;
}

/** 把加粗和行内代码转成对应节点，其他内容仍按文本显示，不执行 HTML。 */
function appendInline(container, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g; let position = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(position, match.index)));
    const bold = match[0].startsWith("**"); const count = bold ? 2 : 1;
    container.append(element(bold ? "strong" : "code", "", match[0].slice(count,-count))); position = match.index + match[0].length;
  }
  container.append(document.createTextNode(text.slice(position)));
}

/** 拆分 Markdown 表格的单元格，不把转义后的竖线误当成列分隔符。 */
function markdownCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map(trimMarkdownCell);
}

/** 去掉单元格两侧空白，并还原文本中转义的竖线。 */
function trimMarkdownCell(value) { return value.trim().replace(/\\\|/g, "|"); }

/** 检查下一行是否是 Markdown 表头分隔行，避免把普通竖线文本当成表格。 */
function isTableSeparator(line) { return Boolean(line) && markdownCells(line).every(isSeparatorCell); }

/** 判断一个单元格是否只包含表头分隔线或对齐标记。 */
function isSeparatorCell(value) { return /^:?-{3,}:?$/.test(value); }

/** 解析报告中的标题、列表、表格和代码块，通过 DOM 节点显示，不执行 HTML 或外部链接。 */
function renderMarkdown(text) {
  // 这里只实现报告需要的 Markdown 语法。内容通过文本节点添加，不交给 innerHTML 执行。
  const container = element("article", "markdown-report"); const lines = text.replace(/\r/g, "").split("\n"); let index = 0;
  while (index < lines.length) {
    const line = lines[index]; if (!line.trim()) { index++; continue; }
    if (line.startsWith("```")) {
      const language = line.slice(3).trim().toLowerCase(); const code = []; index++;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      index++; const details = element("details"); const pre = element("pre"); pre.append(element("code", "", code.join("\n")));
      details.append(element("summary", "", language === "sql" ? "查看 SQL 计算依据" : language === "python" ? "查看 Python 计算依据" : "查看计算依据"), pre); container.append(details); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { const node = element("h" + Math.min(4,heading[1].length + 1)); appendInline(node,heading[2]); container.append(node); index++; continue; }
    if (line.trim().startsWith("|") && isTableSeparator(lines[index+1])) {
      const wrapper = element("div", "table-wrap"); const table = element("table", "result-table"); const head = element("thead"); const row = element("tr");
      for (const value of markdownCells(line)) { const cell = element("th"); cell.scope = "col"; appendInline(cell,value); row.append(cell); }
      head.append(row); table.append(head); const body = element("tbody"); index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        const record = element("tr"); for (const value of markdownCells(lines[index++])) { const cell = element("td"); appendInline(cell,value); record.append(cell); } body.append(record);
      }
      table.append(body); wrapper.append(table); container.append(wrapper); continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const list = element("ul");
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) { const item = element("li"); appendInline(item,lines[index++].replace(/^\s*[-*]\s+/, "")); list.append(item); }
      container.append(list); continue;
    }
    const paragraph = element("p"); appendInline(paragraph,line); container.append(paragraph); index++;
  }
  return container;
}

/** 筛选已加载的报告、图表或表格，同时更新按钮选择状态和没有结果时的提示。 */
function applyArtifactFilter() {
  let count = 0;
  for (const card of byId("artifacts").children) { card.hidden = state.artifactFilter !== "all" && card.dataset.kind !== state.artifactFilter; if (!card.hidden) count++; }
  for (const button of document.querySelectorAll("[data-artifact-kind]")) { const active = button.dataset.artifactKind === state.artifactFilter; button.classList.toggle("active",active); button.setAttribute("aria-pressed",String(active)); }
  byId("artifact-empty").hidden = count > 0;
}

/** 切换结果类型，复用已经加载的内容，不重新下载文件。 */
function filterArtifacts(event) { state.artifactFilter = event.currentTarget.dataset.artifactKind; applyArtifactFilter(); }

/** 切换详细步骤的显示状态。精简显示仍保留重要操作和错误。 */
function toggleEventDetails() { byId("events").classList.toggle("show-details",byId("event-details").checked); }

/** 只调整页面显示顺序，让报告排在图表和表格之前，不改变原始结果引用。 */
function artifactOrder(left, right) { const rank = {report:0,chart:1,table:2}; return rank[left.kind] - rank[right.kind]; }

/** 显示表格、图表和报告，图像通过授权请求下载后使用 Blob URL 展示。 */
async function renderArtifacts(task) {
  // 每轮刷新使用新的请求编号。异步下载后检查编号，避免上一次刷新返回的文件插入本轮页面。
  const request = ++state.artifactRequest;
  // 不再使用的 Blob URL 要主动释放，避免反复打开图表时一直保留旧图像内存。
  for (const url of state.blobs) URL.revokeObjectURL(url); state.blobs = [];
  byId("results-panel").hidden = !task.artifacts.length; const container = byId("artifacts"); container.replaceChildren();
  const names = {all:"全部",report:"报告",chart:"图表",table:"数据表"};
  for (const button of document.querySelectorAll("[data-artifact-kind]")) {
    let count = 0; for (const artifact of task.artifacts) if (button.dataset.artifactKind === "all" || artifact.kind === button.dataset.artifactKind) count++;
    button.textContent = names[button.dataset.artifactKind] + " " + count; button.disabled = !count;
  }
  byId("artifact-total").textContent = "共 " + task.artifacts.length + " 份结果 · 可下载完整文件";
  for (const artifact of [...task.artifacts].sort(artifactOrder)) {
    if (state.taskId !== task.id || request !== state.artifactRequest) return;
    const card = element(artifact.delivery_role === "intermediate" ? "details" : "section", "artifact-card");
    if (artifact.delivery_role === "intermediate") card.append(element("summary", "muted", "中间结果 · " + artifact.label + "（展开查看计算过程）"));
    card.dataset.kind = artifact.kind; const heading = element("div", "section-head"); heading.append(element("h3", "", artifact.kind === "report" ? "分析报告" : artifact.label));
    const button = element("button", "secondary", artifact.kind === "table" ? "下载 CSV" : "下载文件"); button.dataset.id = artifact.id; button.dataset.kind = artifact.kind; button.addEventListener("click", downloadArtifact); heading.append(button); card.append(heading);
    if (artifact.kind === "table") {
      if (artifact.row_count > artifact.preview.length) card.append(element("p", "muted", "共 " + artifact.row_count + " 行，当前预览 " + artifact.preview.length + " 行；CSV 和报告包含完整结果。"));
      card.append(tablePreview(artifact)); const details = element("details"); details.append(element("summary", "", "查看计算依据"), element("pre", "", artifact.sql || artifact.python || "")); card.append(details);
    } else {
      try {
        const response = await api("/api/artifacts/" + artifact.id);
        if (state.taskId !== task.id || request !== state.artifactRequest) return;
        if (artifact.kind === "chart") {
          const blob = await response.blob(); if (state.taskId !== task.id || request !== state.artifactRequest) return;
          const url = URL.createObjectURL(blob); state.blobs.push(url); const image = element("img"); image.src = url; image.alt = artifact.label; image.loading = "lazy"; card.append(image);
          if (artifact.plotted_row_count !== undefined) card.append(element("p", "muted", "来源 " + artifact.source_row_count + " 行，完整绘制 " + artifact.plotted_row_count + " 行。"));
        } else { const text = await response.text(); if (state.taskId !== task.id || request !== state.artifactRequest) return; card.append(renderMarkdown(text)); }
      } catch (error) { card.append(element("p", "error", error.message)); }
    }
    if (state.taskId === task.id && request === state.artifactRequest) { container.append(card); applyArtifactFilter(); }
  }
  applyArtifactFilter();
}

/** 手动刷新列表和当前任务，也可重新连接已经断开的事件流。 */
async function refreshCurrent() { await refreshDashboard(); if (state.taskId) await openTask(state.taskId); }

/** 清除当前会话选择，下一次提问创建新会话。已有任务和历史仍保留在数据库中。 */
function newConversation() {
  if (state.stream) state.stream.abort();
  state.conversationId = null; state.taskId = null; state.historyCursor = 0; state.taskStatus = null;
  byId("conversation-label").textContent = "新会话；可连续追问当前数据集";
  closeHistory(); byId("results-panel").hidden = true;
  byId("events").replaceChildren(); byId("artifacts").replaceChildren();
  byId("events").append(element("li", "empty-state", "分析开始后，这里会展示查询、绘图与验证进度。"));
  for (const url of state.blobs) URL.revokeObjectURL(url); state.blobs = [];
  byId("task-info").textContent = "填写问题创建新会话。";
  byId("resume-form").hidden = true; byId("cancel").hidden = true; renderTasks(); updateAnalyzeButton();
}

/** 读取个人记忆，显示同步状态、删除按钮和需要补齐日期的常问模板。 */
async function refreshMemories() {
  if (!state.token) return;
  const token = state.token;
  try {
    const response = await api("/api/memories"); const body = await response.json();
    if (state.token !== token) return;
    byId("preferred-chart").value = ""; byId("preferred-report").value = "";
    const container = byId("memory-list"); container.replaceChildren();
    if (!body.enabled) container.append(element("p", "muted", "记忆学习与召回已关闭，仍可管理已有记录。"));
    const values = {chart_kind:{line:"默认折线图",bar:"默认柱状图"}, report_style:{concise:"简洁说明",detailed:"详细说明"}};
    const statuses = {pending:"等待索引",indexed:"已同步",deleted:"已删除",failed:"同步失败"};
    for (const memory of [...body.preferences, ...body.patterns]) {
      if (memory.kind === "preference") byId(memory.key === "chart_kind" ? "preferred-chart" : "preferred-report").value = memory.value;
      const card = element("div", "memory-item");
      const caption = memory.kind === "preference" ? values[memory.key][memory.value] : memory.template + " · 成功使用 " + memory.use_count + " 次";
      card.append(element("span", "memory-text", caption), element("span", "sync-label", statuses[memory.sync_status] || memory.sync_status));
      if (memory.sync_error) card.append(element("small", "error", memory.sync_error));
      const button = element("button", "secondary", "忘记"); button.dataset.id = memory.id; button.dataset.kind = memory.kind;
      button.addEventListener("click", forgetMemory); card.append(button); container.append(card);
    }
    if (!body.preferences.length && !body.patterns.length) container.append(element("p", "muted", "还没有记忆。可设置偏好，成功分析后会学习问题模式。"));
    byId("memory-count").textContent = body.preferences.length + body.patterns.length + " 条记忆";
    const recommended = await (await api("/api/memories/suggestions")).json();
    if (state.token !== token) return;
    const suggestions = byId("memory-suggestions"); suggestions.replaceChildren();
    byId("frequent-questions").hidden = !recommended.patterns.length;
    for (const pattern of recommended.patterns) {
      const button = element("button", "secondary", pattern.template + "（" + pattern.use_count + " 次）");
      button.dataset.question = pattern.template; button.addEventListener("click", useTemplate); suggestions.append(button);
    }
  } catch (error) { toast(error.message); }
}

/** 保存允许的图表和报告偏好。选择自动时删除对应偏好，让下次分析自行选择。 */
async function savePreferences(event) {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector("button"); button.disabled = true; button.textContent = "正在保存…";
  try {
    await api("/api/memories/preferences", {method:"PUT", body:JSON.stringify({chart_kind:byId("preferred-chart").value || null, report_style:byId("preferred-report").value || null})});
    await refreshMemories(); toast("偏好已保存，下次分析生效；本次明确要求优先。");
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = "保存偏好"; }
}

/** 请求删除指定记忆。数据库记录立即停止使用，向量由后台稍后删除。 */
async function forgetMemory(event) {
  const button = event.currentTarget;
  try { await api("/api/memories/" + button.dataset.kind + "/" + button.dataset.id, {method:"DELETE"}); await refreshMemories(); toast("已忘记该记忆。"); }
  catch (error) { toast(error.message); }
}

/** 把常问模板填入输入框，并提醒用户填写本次日期，不自动提交。 */
function useTemplate(event) {
  // 推荐内容只是可编辑的问题模板。用户确认后再提交，程序不会直接复用旧答案。
  fillQuestion(event);
  if (event.currentTarget.dataset.question.includes("[时间范围]")) toast("请将 [时间范围] 替换为本次日期后再提交。");
}

/** 打开历史弹窗，分别显示原始消息和摘要，不用摘要替换完整历史。 */
async function showHistory() {
  if (!state.conversationId) { toast("先创建或打开一个分析任务。"); return; }
  state.historyRequest++; state.historyLoading = false;
  state.historyCursor = 0; byId("conversation-messages").replaceChildren();
  byId("summary-status").textContent = "正在读取历史…"; byId("conversation-summary").textContent = "";
  byId("more-history").hidden = true;
  if (!byId("conversation-panel").open) byId("conversation-panel").showModal();
  await moreHistory();
}

/** 关闭历史弹窗，同时让尚未返回的旧请求失效。按 Escape 关闭时也重置加载状态。 */
function closeHistory() {
  state.historyRequest++; state.historyLoading = false;
  if (byId("conversation-panel").open) byId("conversation-panel").close();
  byId("more-history").disabled = false;
}

/** 处理弹窗关闭事件，防止尚未完成的历史请求继续修改已关闭的页面。 */
function historyClosed() {
  if (byId("conversation-panel").open) return;
  state.historyRequest++; state.historyLoading = false; byId("more-history").disabled = false;
}

/** 点击弹窗外区域时关闭弹窗，点击内部内容时保持打开。 */
function historyBackdrop(event) {
  if (event.target !== byId("conversation-panel")) return;
  const bounds = event.target.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) closeHistory();
}

/** 按消息序号读取下一页历史，并显示摘要进度及摘要失败信息。 */
async function moreHistory() {
  if (!state.conversationId || state.historyLoading) return;
  const identifier = state.conversationId;
  const request = state.historyRequest; state.historyLoading = true; byId("more-history").disabled = true;
  try {
    const body = await (await api("/api/conversations/" + identifier + "?after=" + state.historyCursor)).json();
    // 关闭弹窗或切换会话后，旧请求编号会失效。消息分页按 sequence 前进，摘要另行显示。
    if (state.conversationId !== identifier || request !== state.historyRequest) return;
    byId("summary-status").textContent = "已总结到消息 " + body.summary_cursor + "；原文共 " + body.last_sequence + " 条" + (body.summary_error ? " · " + body.summary_error : "");
    byId("conversation-summary").textContent = body.summary || "历史较短，暂不需要摘要。";
    for (const message of body.messages) {
      const card = element(message.role === "user" ? "section" : "details", "history-message" + (message.role === "user" ? " user-message" : ""));
      if (message.role === "user") card.append(element("strong", "", "用户问题 · " + message.sequence), element("p", "", message.content));
      else card.append(element("summary", "", "分析报告 · " + message.sequence + " · 点击展开"), renderMarkdown(message.content));
      byId("conversation-messages").append(card); state.historyCursor = message.sequence;
    }
    byId("more-history").hidden = state.historyCursor >= body.last_sequence;
  } catch (error) { if (request === state.historyRequest) { byId("summary-status").textContent = "历史读取失败，可关闭后重试。"; toast(error.message); } }
  finally { if (request === state.historyRequest) { state.historyLoading = false; byId("more-history").disabled = false; } }
}

/** 为页面控件绑定事件处理函数，并恢复当前标签页的登录状态。 */
async function initialize() {
  byId("close-history").addEventListener("click",closeHistory); byId("conversation-panel").addEventListener("close",historyClosed);
  byId("conversation-panel").addEventListener("click",historyBackdrop);
  byId("question").addEventListener("input",updateQuestionCount); byId("event-details").addEventListener("change",toggleEventDetails);
  for (const button of document.querySelectorAll("[data-artifact-kind]")) button.addEventListener("click",filterArtifacts);
  byId("new-conversation").addEventListener("click", newConversation); byId("show-history").addEventListener("click", showHistory);
  byId("more-history").addEventListener("click", moreHistory); byId("refresh-memory").addEventListener("click", refreshMemories);
  byId("preferences-form").addEventListener("submit", savePreferences);
  byId("auth-form").addEventListener("submit", login); byId("register").addEventListener("click", register);
  byId("logout").addEventListener("click", logout); byId("demo").addEventListener("click", loadDemo);
  byId("show-upload").addEventListener("click", toggleUpload); byId("upload-form").addEventListener("submit", uploadFiles);
  byId("confirm-import").addEventListener("click", confirmImport); byId("encoding").addEventListener("change", changeEncoding);
  byId("analysis-form").addEventListener("submit", submitAnalysis); byId("mode").addEventListener("change", changeMode);
  byId("refresh").addEventListener("click", refreshCurrent); byId("resume-form").addEventListener("submit", resumeAnalysis);
  byId("cancel").addEventListener("click", cancelAnalysis);
  for (const button of document.querySelectorAll("[data-question]")) button.addEventListener("click", fillQuestion);
  if (state.token) { try { await enterWorkspace(); } catch { logout(); } }
  setInterval(pollDatasets, 4000);
}

initialize();
