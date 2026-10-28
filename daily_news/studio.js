/* Local settings client. All retrieved content is inserted as text, never HTML. */
"use strict";

const $ = (selector, parent = document) => parent.querySelector(selector);
const $$ = (selector, parent = document) => Array.from(parent.querySelectorAll(selector));
const clone = value => JSON.parse(JSON.stringify(value));
const state = {
  options: null,
  saved: null,
  draft: null,
  dirty: false,
  secrets: {},
  scheduler: null,
  history: [],
  favorites: [],
  historyId: null,
  weeklyId: null,
  confirmWeeklyUntil: 0,
  previewSequence: 0,
  previewTimer: null,
  previewController: null,
  messageTimer: null,
  historyTimer: null,
};

const descriptions = {
  layout: {
    editorial: "纵向展开，清晰阅读",
    cards: "每条新闻独立呈现",
    columns: "报纸式双栏浏览",
  },
  theme: {
    light: "清爽明亮，适合日常",
    dark: "柔和深色，适合夜读",
    accent: "温暖色调，杂志质感",
  },
  density: {
    brief: "只看核心要点",
    standard: "要点与必要背景",
    detailed: "保留更多上下文",
  },
};
const swatches = {
  light: ["#173c3a", "#ffffff", "#dcebdc"],
  dark: ["#102a2b", "#1a2c32", "#ade8d3"],
  accent: ["#8e4233", "#fffdf9", "#efd9cb"],
};
const pageNames = {overview: "总览", content: "内容订阅", appearance: "排版与风格", delivery: "投递与时间", library: "收藏与回顾"};
const weekdayNames = ["一", "二", "三", "四", "五", "六", "日"];
const channelNames = {email: "邮件", wecom: "企业微信", feishu: "飞书", serverchan: "Server酱", webhook: "Webhook"};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function clear(element) {
  element.replaceChildren();
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function keyOf(value) {
  return value === null || value === undefined ? "" : String(value);
}

function getPath(object, path) {
  return path.split(".").reduce((current, part) => current && current[part], object);
}

function setPath(object, path, value) {
  const parts = path.split(".");
  let current = object;
  for (const part of parts.slice(0, -1)) {
    if (!current[part] || typeof current[part] !== "object") current[part] = {};
    current = current[part];
  }
  current[parts[parts.length - 1]] = value;
}

function safeLink(value) {
  try {
    const url = new URL(String(value));
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password) return null;
    return url.href;
  } catch (_) {
    return null;
  }
}

function message(text, success = false) {
  const element = $("#global-message");
  clearTimeout(state.messageTimer);
  element.textContent = String(text);
  element.classList.toggle("success", success);
  element.hidden = !text;
  if (success) state.messageTimer = setTimeout(() => { element.hidden = true; }, 5500);
}

function apiToken() {
  const meta = $('meta[name="studio-token"]');
  return meta ? meta.content : "";
}

async function api(path, method = "GET", body, options = {}) {
  const token = apiToken();
  if (!token || token === "__STUDIO_TOKEN__") throw new Error("安全令牌缺失，请通过日报工作台服务打开此页面。");
  const init = {
    method,
    headers: {"Accept": "application/json", "X-DailyNews-Token": token},
    credentials: "same-origin",
    cache: "no-store",
    signal: options.signal,
  };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  let data;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error("服务返回了无法读取的数据，请检查工作台服务。");
  }
  if (!response.ok) {
    const error = new Error(typeof data.error === "string" ? data.error : "请求失败，请稍后重试。");
    error.status = response.status;
    throw error;
  }
  return data;
}

function setDirty(dirty = true) {
  state.dirty = dirty;
  $("#save-indicator").textContent = dirty ? "有未保存的更改" : "设置已保存";
  $("#save-indicator").classList.toggle("dirty", dirty);
  $("#save-title").textContent = dirty ? "还有更改没有保存" : "设置已保存";
  $("#save-description").textContent = dirty ? "保存后，采集预览和定时任务才会使用新设置。" : "修改后可保存到本地配置。";
  $("#save-settings").disabled = !dirty || !state.draft;
}

function updateOverview() {
  if (!state.draft) return;
  const draft = state.draft;
  const selected = asArray(draft.topics?.selected);
  const customCount = asArray(draft.sources).filter(source => source && source.enabled !== false).length;
  const topicEnabled = Boolean(draft.topics?.enabled);
  $("#overview-topics").textContent = topicEnabled
    ? `已选择 ${selected.length} 个主题 · ${customCount} 个自定义来源`
    : `主题库已关闭 · ${customCount} 个自定义来源`;
  const nameFor = (group, id) => asArray(state.options?.[group]).find(option => option.id === id)?.name || id || "未设置";
  const presentation = draft.presentation || {};
  $("#overview-appearance").textContent = [
    nameFor("layouts", presentation.layout), nameFor("themes", presentation.theme),
    nameFor("densities", presentation.density),
  ].join(" · ");
  const channels = Object.keys(channelNames).filter(channel => draft[channel]?.enabled).map(channel => channelNames[channel]);
  const times = asArray(draft.schedule?.times);
  $("#overview-schedule").textContent = channels.length
    ? `${channels.join("、")} · ${times.length ? times.join(" / ") : "未设置时间"}`
    : "尚未启用接收方式";
  $("#selected-topic-count").textContent = `${selected.length} 已选`;
  $("#source-warning").hidden = (topicEnabled && selected.length > 0) || customCount > 0;
}

function setPage(page) {
  if (!pageNames[page]) return;
  document.body.dataset.page = page;
  $$(".page").forEach(section => { section.hidden = section.dataset.section !== page; });
  $$(".nav-item").forEach(button => {
    const active = button.dataset.page === page;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $("#breadcrumb-name").textContent = pageNames[page];
  if (page === "library") loadHistory();
  window.scrollTo({top: 0, behavior: "auto"});
}

function setLibraryTab(tab) {
  if (!["history", "favorites", "weekly"].includes(tab)) return;
  $("#library-search-tools").hidden = tab === "weekly";
  $$(".library-tab").forEach(button => {
    const active = button.dataset.libraryTab === tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$(".library-view").forEach(view => { view.hidden = view.dataset.libraryView !== tab; });
  if (tab === "history" || tab === "favorites") loadHistory();
}

function buildLayoutThumb(id) {
  const visual = node("span", `mini-layout ${["editorial", "cards", "columns"].includes(id) ? id : ""}`);
  visual.setAttribute("aria-hidden", "true");
  visual.append(node("i"));
  if (id === "columns") {
    for (let i = 0; i < 4; i++) visual.append(node("b"));
  } else {
    visual.append(node("b"), node("b"), node("em"), node("b"));
  }
  return visual;
}

function buildThemeThumb(id) {
  const visual = node("span", "theme-swatches");
  visual.setAttribute("aria-hidden", "true");
  for (const color of swatches[id] || swatches.light) {
    const dot = node("i");
    dot.style.backgroundColor = color;
    visual.append(dot);
  }
  return visual;
}

function renderOptionGroup(kind, optionName, containerId) {
  const container = $(containerId);
  clear(container);
  const options = asArray(state.options?.[optionName]);
  for (const option of options) {
    if (!option || typeof option.id !== "string" || typeof option.name !== "string") continue;
    const label = node("label", `choice ${kind}-choice`);
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `presentation-${kind}`;
    input.value = option.id;
    input.checked = state.draft.presentation?.[kind] === option.id;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      state.draft.presentation[kind] = option.id;
      setDirty();
      updateOverview();
      queueSamplePreview();
    });
    const shell = node("span", "choice-shell");
    if (kind === "layout") shell.append(buildLayoutThumb(option.id));
    if (kind === "theme") shell.append(buildThemeThumb(option.id));
    shell.append(node("strong", "choice-title", option.name));
    const description = descriptions[kind]?.[option.id];
    if (description) shell.append(node("small", "choice-description", description));
    label.append(input, shell);
    container.append(label);
  }
  if (!options.length) container.append(node("p", "placeholder", "未找到可用选项，请重新加载。"));
}

function renderTopics() {
  const container = $("#topic-groups");
  clear(container);
  const all = asArray(state.options?.topics);
  const selected = new Set(asArray(state.draft.topics?.selected).map(keyOf));
  const enabled = Boolean(state.draft.topics?.enabled);
  const term = $("#topic-search").value.trim().toLocaleLowerCase();
  const groups = new Map();
  for (const topic of all) {
    if (!topic || typeof topic.id !== "string" || typeof topic.name !== "string") continue;
    const group = typeof topic.group === "string" && topic.group ? topic.group : "其他";
    const description = typeof topic.description === "string" ? topic.description : "";
    if (term && ![topic.name, topic.id, group, description].some(value => value.toLocaleLowerCase().includes(term))) continue;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(topic);
  }
  for (const [group, topics] of groups) {
    const section = node("section", "topic-group");
    section.append(node("h3", "topic-group-title", group));
    const grid = node("div", "topic-options");
    for (const topic of topics) {
      const label = node("label", "topic-option");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = topic.id;
      input.checked = selected.has(topic.id);
      input.disabled = !enabled;
      input.addEventListener("change", () => {
        const set = new Set(asArray(state.draft.topics.selected).map(keyOf));
        if (input.checked) set.add(topic.id);
        else set.delete(topic.id);
        state.draft.topics.selected = [...set];
        setDirty();
        updateOverview();
      });
      const copy = node("span");
      copy.append(node("strong", null, topic.name));
      if (topic.description) copy.append(node("small", null, topic.description));
      label.append(input, copy);
      grid.append(label);
    }
    section.append(grid);
    container.append(section);
  }
  if (!groups.size) container.append(node("p", "placeholder", term ? "没有找到匹配的主题。" : "暂无可选主题。"));
  container.classList.toggle("is-disabled", !enabled);
  $("#select-topics").disabled = !enabled;
  $("#clear-topics").disabled = !enabled;
}

function hostLabel(url) {
  try { return new URL(String(url)).hostname || "自定义来源"; }
  catch (_) { return "自定义来源"; }
}

function renderSources() {
  const container = $("#source-list");
  clear(container);
  const sources = asArray(state.draft?.sources);
  if (!sources.length) {
    container.append(node("p", "placeholder", "尚未添加自定义来源。"));
    updateOverview();
    return;
  }
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    const row = node("div", "source-row");
    const copy = node("div", "source-copy");
    copy.append(node("strong", null, source.name || source.id || "未命名来源"));
    copy.append(node("small", null, `${source.category || "综合"} · ${hostLabel(source.url)}`));
    const toggleLabel = node("label", "source-toggle");
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = source.enabled !== false;
    toggle.setAttribute("aria-label", `启用来源 ${source.name || source.id || ""}`);
    toggle.addEventListener("change", () => {
      source.enabled = toggle.checked;
      setDirty();
      updateOverview();
    });
    toggleLabel.append(toggle, document.createTextNode(" 启用"));
    const remove = node("button", "icon-button", "移除");
    remove.type = "button";
    remove.setAttribute("aria-label", `移除来源 ${source.name || source.id || ""}`);
    remove.addEventListener("click", () => {
      state.draft.sources = state.draft.sources.filter(item => item !== source);
      setDirty();
      renderSources();
      updateOverview();
    });
    row.append(copy, toggleLabel, remove);
    container.append(row);
  }
  updateOverview();
}

function renderWeekdays() {
  const container = $("#weekday-options");
  clear(container);
  const selected = new Set(asArray(state.draft.schedule?.weekdays).map(Number));
  for (let day = 1; day <= 7; day++) {
    const label = node("label", "weekday-option");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = String(day);
    input.checked = selected.has(day);
    input.setAttribute("aria-label", `星期${weekdayNames[day - 1]}`);
    input.addEventListener("change", () => {
      const current = new Set(asArray(state.draft.schedule.weekdays).map(Number));
      if (input.checked) current.add(day);
      else current.delete(day);
      state.draft.schedule.weekdays = [...current].sort((a, b) => a - b);
      setDirty();
    });
    label.append(input, node("span", null, weekdayNames[day - 1]));
    container.append(label);
  }
}

function renderTimes() {
  const container = $("#time-list");
  clear(container);
  const times = asArray(state.draft.schedule?.times);
  for (let index = 0; index < times.length; index++) {
    const row = node("div", "time-row");
    const input = document.createElement("input");
    input.type = "time";
    input.required = true;
    input.value = times[index] || "";
    input.setAttribute("aria-label", `第 ${index + 1} 个发送时间`);
    input.addEventListener("input", () => {
      state.draft.schedule.times[index] = input.value;
      setDirty();
      updateOverview();
    });
    const remove = node("button", "icon-button", "移除");
    remove.type = "button";
    remove.setAttribute("aria-label", `移除第 ${index + 1} 个发送时间`);
    remove.addEventListener("click", () => {
      state.draft.schedule.times.splice(index, 1);
      setDirty();
      renderTimes();
      updateOverview();
    });
    row.append(input, remove);
    container.append(row);
  }
  if (!times.length) container.append(node("p", "placeholder", "尚未设置时间，请至少添加一个。"));
  $("#add-time").disabled = times.length >= 12;
}

function renderChannelDetails() {
  for (const channel of Object.keys(channelNames)) {
    const panel = $(`[data-details="${channel}"]`);
    if (panel) panel.hidden = !Boolean(state.draft?.[channel]?.enabled);
  }
  $("#ai-details").hidden = !Boolean(state.draft?.ai?.enabled);
}

function renderSecretStatus() {
  const saved = state.saved?.secrets_set || {};
  $$("[data-secret]").forEach(input => {
    const present = Boolean(saved[input.dataset.secret]);
    input.value = "";
    input.placeholder = present ? "已设置，留空保留" : "未设置";
  });
  $$("[data-secret-status]").forEach(label => {
    const name = label.dataset.secretStatus;
    const overridden = Boolean(state.saved?.secret_overrides?.[name]);
    label.classList.toggle("secret-override", overridden);
    label.textContent = overridden
      ? "当前由外部环境变量覆盖；本地保存的值不会生效。"
      : saved[name] ? "已配置；留空不修改。" : "尚未配置。";
  });
  state.secrets = {};
}

function renderStaticFields() {
  $$("[data-field]").forEach(input => {
    const value = getPath(state.draft, input.dataset.field);
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else if (value !== undefined && value !== null) {
      if (input.tagName === "SELECT" && ![...input.options].some(option => option.value === String(value))) {
        const option = node("option", null, `${value} 小时`);
        option.value = String(value);
        input.append(option);
      }
      input.value = String(value);
    }
  });
  $("#include-keywords").value = asArray(state.draft.briefing?.include_keywords).join("、");
}

function renderSettings() {
  if (!state.draft) return;
  renderStaticFields();
  renderSecretStatus();
  renderOptionGroup("layout", "layouts", "#layout-options");
  renderOptionGroup("theme", "themes", "#theme-options");
  renderOptionGroup("density", "densities", "#density-options");
  renderTopics();
  renderSources();
  renderWeekdays();
  renderTimes();
  renderChannelDetails();
  updateOverview();
  setDirty(false);
}

function renderScheduler(scheduler) {
  state.scheduler = scheduler && typeof scheduler === "object" ? scheduler : null;
  const status = String(state.scheduler?.status || "").toLowerCase();
  const running = ["running", "active", "started"].includes(status);
  const stopped = ["stopped", "idle", "disabled", "not_running"].includes(status);
  const stopping = status === "stopping";
  const failed = ["error", "failed"].includes(status);
  $("#schedule-dot").classList.toggle("running", running);
  $("#schedule-dot").classList.toggle("error", failed);
  $("#schedule-status").textContent = running ? "定时运行中" : stopped ? "定时未启动" : stopping ? "正在停止" : failed ? "定时状态异常" : "定时状态待确认";
  $("#schedule-message").textContent = String(state.scheduler?.message || (
    running ? "请保持主机在线并让工作台服务持续运行。" : "保存设置后，启动定时才能按计划发送。"
  ));
  $("#start-scheduler").disabled = running || stopping || !state.draft;
  $("#stop-scheduler").disabled = !running || stopping;
  if (state.dirty) {
    $("#save-description").textContent = running || stopping
      ? "请先停止定时，等待当前任务结束，再保存设置。"
      : "保存后，采集预览和定时任务才会使用新设置。";
  }
}

function previewPolicy(html) {
  const policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:; font-src \'none\'; connect-src \'none\'; form-action \'none\'; base-uri \'none\'">';
  if (/<head(?:\s[^>]*)?>/i.test(html)) return html.replace(/<head(?:\s[^>]*)?>/i, match => match + policy);
  return '<!doctype html><html lang="zh-CN"><head>' + policy + '</head><body>' + html + '</body></html>';
}

function showPreview(html, label, note) {
  if (typeof html !== "string" || !html.trim()) throw new Error("预览内容为空。");
  $("#preview-frame").srcdoc = previewPolicy(html);
  $("#preview-placeholder").hidden = true;
  $("#preview-badge").textContent = label;
  $("#preview-note").textContent = note;
}

async function samplePreview() {
  if (!state.draft) return;
  const sequence = ++state.previewSequence;
  if (state.previewController) state.previewController.abort();
  state.previewController = new AbortController();
  try {
    const result = await api("/api/preview/sample", "POST", {
      presentation: clone(state.draft.presentation),
      title: state.draft.briefing?.title || "每日资讯简报",
    }, {signal: state.previewController.signal});
    if (sequence !== state.previewSequence) return;
    showPreview(result.html, "示例预览", "样式切换会更新示例。真实新闻预览使用已保存的来源，不调用 AI。");
  } catch (error) {
    if (error.name === "AbortError" || sequence !== state.previewSequence) return;
    $("#preview-placeholder").textContent = `示例预览暂不可用：${error.message}`;
    $("#preview-placeholder").hidden = false;
  }
}

function queueSamplePreview() {
  clearTimeout(state.previewTimer);
  state.previewTimer = setTimeout(samplePreview, 220);
}

async function livePreview(useAi = false) {
  if (state.dirty) {
    message("请先保存更改，再采集真实新闻；真实预览使用已保存的来源和设置。");
    return;
  }
  if (useAi && !state.draft.ai?.enabled) {
    message("请先在推送与时间设置中启用 AI 并保存。");
    return;
  }
  if (useAi && !window.confirm("这次预览将调用已配置的 AI 服务，可能产生费用；只生成预览，不发送。继续吗？")) return;
  const button = $(useAi ? "#ai-preview" : "#live-preview");
  const label = button.textContent;
  $("#live-preview").disabled = true;
  $("#ai-preview").disabled = true;
  button.textContent = useAi ? "正在采集并整理新闻…" : "正在采集新闻…";
  const sequence = ++state.previewSequence;
  if (state.previewController) state.previewController.abort();
  try {
    const result = await api(useAi ? "/api/preview/ai" : "/api/preview/live", "POST", {});
    if (sequence !== state.previewSequence) return;
    showPreview(result.html, result.ai_used ? "AI 整合" : useAi ? "原文摘要" : "真实新闻",
      result.ai_used ? "已使用 AI 整合和翻译，保留原文来源。本次只预览，未发送。"
        : useAi ? "AI 不可用，已保留原文摘要；详情见日报中的提示。本次未发送。"
          : "真实新闻预览来自当前已保存来源，本次未调用 AI，也未发送。");
    if (asArray(result.warnings).length) message(asArray(result.warnings).join("；"));
    else message("真实新闻预览已生成。", true);
    $("#preview-panel").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    message(`无法生成真实预览：${error.message}`);
  } finally {
    $("#live-preview").disabled = false;
    $("#ai-preview").disabled = false;
    button.textContent = label;
  }
}

function invalidSettings() {
  const title = String(state.draft.briefing?.title || "").trim();
  if (!title) return "请填写日报名称。";
  const max = Number(state.draft.briefing?.max_stories);
  if (!Number.isInteger(max) || max < 1 || max > 30) return "每期事件数应为 1–30。";
  const days = asArray(state.draft.schedule?.weekdays);
  const times = asArray(state.draft.schedule?.times);
  if (!days.length) return "请至少选择一个发送日期。";
  if (!times.length || times.some(time => !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(time))) return "请填写有效的发送时间。";
  if (new Set(times).size !== times.length) return "发送时间不能重复。";
  return "";
}

function settingsPayload() {
  const payload = {revision: state.saved.revision};
  for (const name of ["presentation", "topics", "briefing", "schedule", "ai", "email",
                      "wecom", "feishu", "serverchan", "webhook", "push", "sources"]) {
    if (state.draft[name] !== undefined) payload[name] = clone(state.draft[name]);
  }
  payload.secrets = {...state.secrets};
  return payload;
}

async function saveSettings() {
  if (!state.draft || !state.dirty) return false;
  const invalid = invalidSettings();
  if (invalid) { message(invalid); return false; }
  if (["running", "stopping"].includes(String(state.scheduler?.status || "").toLowerCase())) {
    message("请先停止定时，等待当前任务结束，再保存设置。");
    return false;
  }
  const button = $("#save-settings");
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const result = await api("/api/settings", "POST", settingsPayload());
    if (!result.settings || typeof result.settings !== "object") throw new Error("服务未返回已保存的设置。");
    state.saved = clone(result.settings);
    state.draft = clone(result.settings);
    renderSettings();
    queueSamplePreview();
    message("设置已保存。", true);
    return true;
  } catch (error) {
    message(error.status === 409 ? "设置已在其他页面更改。请重新加载并重新检查你的更改后再保存。" : `保存失败：${error.message}`);
    return false;
  } finally {
    button.textContent = "保存设置";
    button.disabled = !state.dirty;
  }
}

async function schedulerAction(action) {
  if (action === "start" && state.dirty) {
    message("请先保存设置，再启动定时任务。");
    return;
  }
  const button = action === "start" ? $("#start-scheduler") : $("#stop-scheduler");
  button.disabled = true;
  try {
    const result = await api(`/api/schedule/${action}`, "POST", {});
    renderScheduler(result.scheduler);
    const status = String(result.scheduler?.status || "").toLowerCase();
    if (action === "start" && status === "running") {
      message("定时任务已启动，请保持工作台服务和主机在线。", true);
    } else if (action === "stop" && status === "stopping") {
      message("已请求停止；当前任务结束后，定时任务会停止。", true);
    } else if (action === "stop" && status === "stopped") {
      message("定时任务已停止。", true);
    } else {
      message(result.scheduler?.message || "已提交操作，尚未确认定时状态，请查看运行状态。");
    }
  } catch (error) {
    message(`${action === "start" ? "启动" : "停止"}定时失败：${error.message}`);
    renderScheduler(state.scheduler);
  }
}

async function refreshScheduler() {
  try {
    const result = await api("/api/schedule/status");
    renderScheduler(result.scheduler);
  } catch (_) {
    renderScheduler({status: "unknown", message: "无法确认调度状态，请检查工作台服务。"});
  }
}

function dateLabel(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).format(date);
}

function historyId(item) {
  return keyOf(item?.id ?? item?.history_id);
}

function favoriteId(item) {
  return keyOf(item?.id ?? item?.favorite_id);
}

function kindLabel(kind) {
  return ["weekly", "week", "周报"].includes(String(kind).toLowerCase()) ? "周报" : "日报";
}

function renderHistory() {
  const container = $("#history-list");
  clear(container);
  if (!state.history.length) {
    container.append(node("p", "empty-state", "暂无匹配的历史日报。生成真实预览或日报后可在这里查看。"));
    return;
  }
  for (const item of state.history) {
    const id = historyId(item);
    if (!id) continue;
    const row = node("div", "history-row");
    const copy = node("div", "history-row-copy");
    copy.append(node("strong", null, item.title || `${kindLabel(item.kind)} · ${id}`));
    const details = [kindLabel(item.kind), dateLabel(item.created_at ?? item.generated_at)];
    const statusNames = {sent: "接收端已接受", preview: "预览", failed: "投递失败",
                         pending: "待投递", uncertain: "结果待核对"};
    if (statusNames[item.status]) details.push(statusNames[item.status]);
    if (Number.isFinite(Number(item.story_count))) details.push(`${item.story_count} 个事件`);
    copy.append(node("small", null, details.join(" · ")));
    const actions = node("div", "history-actions");
    const open = node("button", "text-link", "查看");
    open.type = "button";
    open.setAttribute("aria-label", `查看 ${item.title || id}`);
    open.addEventListener("click", () => openHistory(id));
    const remove = node("button", "icon-button", "删除");
    remove.type = "button";
    remove.setAttribute("aria-label", `删除 ${item.title || id}`);
    remove.addEventListener("click", () => deleteHistory(id));
    if (item.can_delete === false) {
      remove.disabled = true;
      remove.title = "待处理的投递记录不能删除";
    }
    actions.append(open, remove);
    row.append(copy, actions);
    container.append(row);
  }
}

function renderFavorites() {
  const container = $("#favorites-list");
  clear(container);
  if (!state.favorites.length) {
    container.append(node("p", "empty-state", "还没有收藏文章。打开一份历史日报，点击文章旁的「收藏」即可。"));
    return;
  }
  for (const favorite of state.favorites) {
    const id = favoriteId(favorite);
    if (!id) continue;
    const article = favorite.article && typeof favorite.article === "object" ? favorite.article : favorite;
    const row = node("div", "article-row");
    const copy = node("div", "article-row-copy");
    const title = article.translated_title || article.title || favorite.title || "未命名文章";
    const link = safeLink(article.url || favorite.url);
    if (link) {
      const anchor = node("a", null, title);
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      copy.append(anchor);
    } else {
      copy.append(node("strong", null, title));
    }
    copy.append(node("small", null, [article.source_name || favorite.source_name || "来源文章",
                                    dateLabel(favorite.created_at || favorite.saved_at)].join(" · ")));
    const remove = node("button", "icon-button", "取消收藏");
    remove.type = "button";
    remove.addEventListener("click", () => removeFavorite(id));
    row.append(copy, remove);
    container.append(row);
  }
}

async function loadHistory() {
  try {
    const query = $("#history-search").value.trim();
    const result = await api("/api/history?q=" + encodeURIComponent(query));
    state.history = asArray(result.items);
    state.favorites = asArray(result.favorites);
    renderHistory();
    renderFavorites();
  } catch (error) {
    message(`无法加载历史记录：${error.message}`);
  }
}

function historyArticles(digest, selectedId) {
  const container = $("#history-articles");
  clear(container);
  const unique = new Map();
  for (const story of asArray(digest?.stories)) {
    for (const article of asArray(story?.articles)) {
      const id = keyOf(article?.id);
      if (id && !unique.has(id)) unique.set(id, article);
    }
  }
  if (!unique.size) {
    container.append(node("p", "placeholder", "这份记录中没有可收藏的文章。"));
    return;
  }
  for (const [articleId, article] of unique) {
    const row = node("div", "article-row");
    const copy = node("div", "article-row-copy");
    const title = article.translated_title || article.title || "未命名文章";
    const url = safeLink(article.url);
    if (url) {
      const link = node("a", null, title);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      copy.append(link);
    } else copy.append(node("strong", null, title));
    copy.append(node("small", null, [article.source_name || "来源文章", dateLabel(article.published_at)].join(" · ")));
    const favorite = node("button", "text-link", "☆ 收藏");
    favorite.type = "button";
    favorite.addEventListener("click", async () => {
      favorite.disabled = true;
      try {
        await api("/api/history/favorite", "POST", {history_id: selectedId, article_id: articleId});
        favorite.textContent = "★ 已收藏";
        message("文章已收藏。", true);
        await loadHistory();
      } catch (error) {
        message(`收藏失败：${error.message}`);
      } finally {
        favorite.disabled = false;
      }
    });
    row.append(copy, favorite);
    container.append(row);
  }
}

async function openHistory(id) {
  try {
    const result = await api("/api/history/item?id=" + encodeURIComponent(id));
    state.historyId = id;
    $("#history-detail-title").textContent = result.digest?.title || `${kindLabel(result.kind)} · ${id}`;
    $("#history-detail").hidden = false;
    historyArticles(result.digest, id);
    showPreview(result.html, kindLabel(result.kind) + "记录", "这是已保存的历史内容；样式与生成时的快照一致。");
    $("#history-detail").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    message(`无法打开历史记录：${error.message}`);
  }
}

async function deleteHistory(id) {
  if (!window.confirm("确定删除这条历史记录吗？此操作无法撤销。")) return;
  try {
    await api("/api/history/delete", "POST", {history_id: id});
    if (state.historyId === id) {
      state.historyId = null;
      $("#history-detail").hidden = true;
      queueSamplePreview();
    }
    if (state.weeklyId === id) {
      state.weeklyId = null;
      $("#weekly-send").disabled = true;
    }
    await loadHistory();
    message("历史记录已删除。", true);
  } catch (error) {
    message(`删除失败：${error.message}`);
  }
}

async function removeFavorite(id) {
  if (!window.confirm("确定取消收藏这篇文章吗？")) return;
  try {
    await api("/api/favorites/remove", "POST", {favorite_id: id});
    await loadHistory();
    message("已取消收藏。", true);
  } catch (error) {
    message(`取消收藏失败：${error.message}`);
  }
}

function renderWeeklyMetrics(metrics) {
  const container = $("#weekly-metrics");
  clear(container);
  const names = {stories: "事件", story_count: "事件", articles: "文章", article_count: "文章",
                 sources: "来源", source_count: "来源", days: "天", day_count: "天"};
  if (!metrics || typeof metrics !== "object" || Array.isArray(metrics)) {
    container.hidden = true;
    return;
  }
  for (const [key, value] of Object.entries(metrics)) {
    if (names[key] && (typeof value === "number" || typeof value === "string")) {
      container.append(node("span", null, `${value} ${names[key]}`));
    }
  }
  container.hidden = !container.children.length;
}

async function weeklyPreview() {
  if (state.dirty) {
    message("请先保存更改，再生成周报预览。");
    return;
  }
  const button = $("#weekly-preview");
  button.disabled = true;
  button.textContent = "正在整理…";
  $("#weekly-status").textContent = "正在汇总本地历史记录…";
  try {
    const useAi = $("#weekly-use-ai").checked;
    const result = await api("/api/weekly/preview", "POST", {ai: useAi});
    const id = keyOf(result.history_id);
    if (!id) throw new Error("服务未返回周报记录编号。");
    state.weeklyId = id;
    state.confirmWeeklyUntil = 0;
    $("#weekly-send").textContent = "发送这份周报";
    $("#weekly-send").disabled = false;
    $("#weekly-status").textContent = result.ai_used ? "AI 周报预览已生成；发送前还需要再次确认。"
      : useAi ? "AI 不可用，已生成原文摘要周报；发送前还需要再次确认。" : "免费周报预览已生成；发送前还需要再次确认。";
    renderWeeklyMetrics(result.metrics);
    showPreview(result.html, result.ai_used ? "AI 周报预览" : "原文摘要周报", "周报由本地近 7 天历史内容生成；预览不会自动发送。");
    if (asArray(result.warnings).length) message(asArray(result.warnings).join("；"));
    $("#preview-panel").scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    state.weeklyId = null;
    $("#weekly-send").disabled = true;
    $("#weekly-status").textContent = "周报预览未生成。";
    message(`无法生成周报：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "生成周报预览";
  }
}

async function weeklySend() {
  if (!state.weeklyId) return;
  const button = $("#weekly-send");
  if (Date.now() > state.confirmWeeklyUntil) {
    state.confirmWeeklyUntil = Date.now() + 15000;
    button.textContent = "再次点击确认发送";
    $("#weekly-status").textContent = "将向已启用的渠道发送这份周报；请在 15 秒内再次点击确认。";
    setTimeout(() => {
      if (Date.now() >= state.confirmWeeklyUntil && !button.disabled) {
        state.confirmWeeklyUntil = 0;
        button.textContent = "发送这份周报";
        $("#weekly-status").textContent = "发送前需要再次确认。";
      }
    }, 15100);
    return;
  }
  state.confirmWeeklyUntil = 0;
  button.disabled = true;
  button.textContent = "正在发送…";
  try {
    const result = await api("/api/weekly/send", "POST", {history_id: state.weeklyId});
    if (result.status === "complete") {
      $("#weekly-status").textContent = "所有启用渠道的接收端已接受；请在接收渠道确认实际结果。";
      message("周报已提交给全部接收渠道。", true);
    } else {
      const names = {sent: "接收端已接受", failed: "失败", uncertain: "结果待核对", pending: "待发送", sending: "发送中"};
      const channels = result.channels && typeof result.channels === "object" ? result.channels : {};
      const details = Object.entries(channels).map(([name, status]) =>
        (channelNames[name] || name) + "：" + (names[status] || String(status))).join("；");
      const statusText = result.status === "deferred" ? "本次未投递，尚未到重试时间或已达到自动重试上限。" : "周报尚未全部发送成功。";
      $("#weekly-status").textContent = statusText + (details ? " " + details : "") + " 请查看运行状态并核对接收渠道。";
      message($("#weekly-status").textContent);
    }
  } catch (error) {
    $("#weekly-status").textContent = "发送未完成，请检查渠道配置和运行状态。";
    message(`发送周报失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "发送这份周报";
  }
}

function switchProvider(previous, current) {
  const defaults = {
    deepseek: {base_url: "https://api.deepseek.com", model: "deepseek-v4-pro"},
    qwen: {base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus"},
  };
  if (!defaults[current]) return;
  const old = defaults[previous];
  for (const field of ["base_url", "model"]) {
    const currentValue = state.draft.ai[field];
    if (!currentValue || (old && currentValue === old[field])) {
      state.draft.ai[field] = defaults[current][field];
      const input = $(`[data-field="ai.${field}"]`);
      if (input) input.value = defaults[current][field];
    }
  }
}

function bindInputs() {
  $$("[data-field]").forEach(input => {
    const event = input.type === "checkbox" || input.tagName === "SELECT" ? "change" : "input";
    input.addEventListener(event, () => {
      if (!state.draft) return;
      const path = input.dataset.field;
      const previous = getPath(state.draft, path);
      let value = input.type === "checkbox" ? input.checked : input.value;
      if (input.dataset.type === "number" || input.type === "number") value = input.value === "" ? null : Number(value);
      setPath(state.draft, path, value);
      if (path === "ai.provider") switchProvider(previous, value);
      if (path === "topics.enabled") renderTopics();
      if (path.endsWith(".enabled")) renderChannelDetails();
      setDirty();
      updateOverview();
      if (path === "briefing.title") queueSamplePreview();
    });
  });
  $$("[data-secret]").forEach(input => input.addEventListener("input", () => {
    if (!state.draft) return;
    state.secrets[input.dataset.secret] = input.value;
    setDirty();
  }));
  $("#include-keywords").addEventListener("input", event => {
    if (!state.draft) return;
    state.draft.briefing.include_keywords = event.target.value.split(/[,，、;；\n]/).map(word => word.trim()).filter(Boolean);
    setDirty();
  });
  $("#topic-search").addEventListener("input", () => { if (state.draft) renderTopics(); });
  $("#select-topics").addEventListener("click", () => {
    if (!state.draft?.topics?.enabled) return;
    state.draft.topics.selected = asArray(state.options?.topics).map(topic => topic.id).filter(id => typeof id === "string");
    setDirty();
    renderTopics();
    updateOverview();
  });
  $("#clear-topics").addEventListener("click", () => {
    if (!state.draft?.topics?.enabled) return;
    state.draft.topics.selected = [];
    setDirty();
    renderTopics();
    updateOverview();
  });
  $("#add-time").addEventListener("click", () => {
    if (!state.draft || asArray(state.draft.schedule?.times).length >= 12) return;
    if (!Array.isArray(state.draft.schedule.times)) state.draft.schedule.times = [];
    state.draft.schedule.times.push("08:00");
    setDirty();
    renderTimes();
    updateOverview();
    const last = $("#time-list input:last-of-type");
    if (last) last.focus();
  });
  $("#source-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!state.draft) return;
    const name = $("#source-name").value.trim();
    const category = $("#source-category").value.trim() || "综合";
    const rawUrl = $("#source-url").value.trim();
    const url = safeLink(rawUrl);
    if (!name || !url || !url.startsWith("https://")) {
      message("请输入来源名称和有效的 HTTPS 订阅地址。");
      return;
    }
    if (asArray(state.draft.sources).some(source => source.url === url)) {
      message("这个来源地址已经存在。");
      return;
    }
    if (!Array.isArray(state.draft.sources)) state.draft.sources = [];
    const id = "custom_" + (window.crypto?.randomUUID?.() || (Date.now().toString(36) + Math.random().toString(36).slice(2))).replace(/-/g, "").slice(0, 24);
    state.draft.sources.push({id, name, url, category, weight: 1.0, enabled: true});
    event.target.reset();
    event.target.closest("details").open = false;
    setDirty();
    renderSources();
    message("来源已添加，保存后生效。", true);
  });
}

function bindActions() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => setPage(button.dataset.page)));
  $$("[data-go]").forEach(button => button.addEventListener("click", () => setPage(button.dataset.go)));
  $$(".jump-preview").forEach(button => button.addEventListener("click", () => $("#preview-panel").scrollIntoView({behavior: "smooth", block: "start"})));
  $("#save-settings").addEventListener("click", saveSettings);
  $("#reload-settings").addEventListener("click", async () => {
    if (state.dirty && !window.confirm("重新加载会丢弃未保存的更改，确定继续吗？")) return;
    await loadMeta();
  });
  $("#live-preview").addEventListener("click", () => livePreview(false));
  $("#ai-preview").addEventListener("click", () => livePreview(true));
  $("#start-scheduler").addEventListener("click", () => schedulerAction("start"));
  $("#stop-scheduler").addEventListener("click", () => schedulerAction("stop"));
  $$(".library-tab").forEach((button, index, list) => {
    button.addEventListener("click", () => setLibraryTab(button.dataset.libraryTab));
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const delta = event.key === "ArrowRight" ? 1 : -1;
      const next = list[(index + delta + list.length) % list.length];
      setLibraryTab(next.dataset.libraryTab);
      next.focus();
    });
  });
  $("#history-search").addEventListener("input", () => {
    clearTimeout(state.historyTimer);
    state.historyTimer = setTimeout(loadHistory, 300);
  });
  $("#refresh-history").addEventListener("click", loadHistory);
  $("#close-history-detail").addEventListener("click", () => { $("#history-detail").hidden = true; });
  $("#weekly-preview").addEventListener("click", weeklyPreview);
  $("#weekly-send").addEventListener("click", weeklySend);
  window.addEventListener("beforeunload", event => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
}

async function loadMeta() {
  try {
    const result = await api("/api/meta");
    if (!result.settings || !result.options) throw new Error("工作台配置不完整。");
    state.options = result.options;
    state.saved = clone(result.settings);
    state.draft = clone(result.settings);
    renderSettings();
    renderScheduler(result.scheduler);
    message("");
    await samplePreview();
  } catch (error) {
    message(`无法加载设置：${error.message}`);
    $("#save-indicator").textContent = "加载失败";
    renderScheduler(null);
  }
}

bindInputs();
bindActions();
loadMeta();
setInterval(() => {
  if (document.visibilityState === "visible" && state.draft) refreshScheduler();
}, 15000);
