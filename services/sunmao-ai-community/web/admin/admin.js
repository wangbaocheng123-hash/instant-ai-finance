"use strict";

const VIEW_LABELS = {
  exchange: "交流区",
  sizes: "尺寸型",
  joints: "榫卯108道",
};
const DEVICE_LABELS = {
  desktop: "电脑",
  mobile: "手机",
  tablet: "平板",
  bot: "自动程序",
};
const NUMBER_FORMAT = new Intl.NumberFormat("zh-CN");
const TIME_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function showOnly(id) {
  ["loginView", "setupView", "dashboardView"].forEach((viewId) => {
    document.querySelector(`#${viewId}`).hidden = viewId !== id;
  });
}

function formatNumber(value) {
  return NUMBER_FORMAT.format(Number(value) || 0);
}

function formatTime(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : TIME_FORMAT.format(parsed);
}

function sourceLabel(value) {
  return value === "direct" ? "直接访问" : value;
}

function setText(id, value) {
  document.querySelector(`#${id}`).textContent = value;
}

function renderSummary(summary) {
  const values = {
    todayVisitors: summary.today_visitors,
    todayVisits: summary.today_visits,
    todayPageviews: summary.today_pageviews,
    onlineNow: summary.online_now,
    totalVisitors: summary.total_visitors,
    totalVisits: summary.total_visits,
    totalPageviews: summary.total_pageviews,
  };
  Object.entries(values).forEach(([id, value]) => setText(id, formatNumber(value)));
}

function renderTrend(trend) {
  const chart = document.querySelector("#trendChart");
  const rows = Array.isArray(trend) ? trend : [];
  const maximum = Math.max(1, ...rows.map((item) => Number(item.pageviews) || 0));
  const fragment = document.createDocumentFragment();
  rows.forEach((item) => {
    const day = document.createElement("div");
    day.className = "trend-day";
    const bars = document.createElement("div");
    bars.className = "trend-bars";
    bars.title = `${item.day}：${formatNumber(item.visitors)} 位访客，${formatNumber(item.pageviews)} 次浏览`;
    const visitorBar = document.createElement("i");
    visitorBar.className = "trend-bar";
    visitorBar.style.height = `${Math.max(2, (Number(item.visitors) / maximum) * 100)}%`;
    const pageviewBar = document.createElement("i");
    pageviewBar.className = "trend-bar pageviews";
    pageviewBar.style.height = `${Math.max(2, (Number(item.pageviews) / maximum) * 100)}%`;
    const label = document.createElement("time");
    label.dateTime = item.day;
    label.textContent = item.day.slice(5).replace("-", "/");
    bars.append(visitorBar, pageviewBar);
    day.append(bars, label);
    fragment.append(day);
  });
  chart.replaceChildren(fragment);
}

function renderRanking(id, rows, labelKey, valueKey, labelFormatter = (value) => value) {
  const container = document.querySelector(`#${id}`);
  const values = Array.isArray(rows) ? rows : [];
  if (!values.length) {
    const empty = document.createElement("p");
    empty.className = "empty-inline";
    empty.textContent = "暂无数据";
    container.replaceChildren(empty);
    return;
  }
  const maximum = Math.max(1, ...values.map((item) => Number(item[valueKey]) || 0));
  const fragment = document.createDocumentFragment();
  values.forEach((item) => {
    const row = document.createElement("div");
    row.className = "rank-item";
    const label = document.createElement("strong");
    label.textContent = labelFormatter(item[labelKey]) || "未知";
    label.title = label.textContent;
    const value = document.createElement("span");
    value.textContent = formatNumber(item[valueKey]);
    const track = document.createElement("div");
    track.className = "rank-track";
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(2, (Number(item[valueKey]) / maximum) * 100)}%`;
    track.append(fill);
    row.append(label, value, track);
    fragment.append(row);
  });
  container.replaceChildren(fragment);
}

function tableCell(value) {
  const cell = document.createElement("td");
  cell.textContent = value;
  return cell;
}

function renderVisitors(visitors) {
  const body = document.querySelector("#visitorRows");
  const empty = document.querySelector("#visitorEmpty");
  const rows = Array.isArray(visitors) ? visitors : [];
  const fragment = document.createDocumentFragment();
  rows.forEach((visitor) => {
    const row = document.createElement("tr");
    row.append(
      tableCell(visitor.visitor_label || "匿名访客"),
      tableCell(formatTime(visitor.last_seen_at)),
      tableCell(`${formatNumber(visitor.visits)} / ${formatNumber(visitor.pageviews)}`),
      tableCell(VIEW_LABELS[visitor.last_view] || visitor.last_view || "—"),
      tableCell(sourceLabel(visitor.referrer_host || "direct")),
      tableCell(DEVICE_LABELS[visitor.device_type] || "未知"),
    );
    fragment.append(row);
  });
  body.replaceChildren(fragment);
  empty.hidden = rows.length > 0;
}

function renderDashboard(payload) {
  renderSummary(payload.summary || {});
  renderTrend(payload.trend);
  renderRanking("pageList", payload.pages, "view", "pageviews", (value) => VIEW_LABELS[value] || value);
  renderRanking("sourceList", payload.sources, "source", "visits", sourceLabel);
  renderRanking("deviceList", payload.devices, "device_type", "visits", (value) => DEVICE_LABELS[value] || value);
  renderVisitors(payload.recent_visitors);
  setText("updatedAt", `更新于 ${formatTime(payload.generated_at)} · 数据按北京时间统计`);
}

async function loadDashboard() {
  const status = document.querySelector("#dashboardStatus");
  const refreshButton = document.querySelector("#refreshButton");
  const days = document.querySelector("#periodSelect").value;
  status.textContent = "";
  refreshButton.disabled = true;
  refreshButton.textContent = "刷新中…";
  try {
    const response = await fetch(`/api/admin/analytics?days=${encodeURIComponent(days)}&limit=50`, { cache: "no-store" });
    if (response.status === 401) {
      showOnly("loginView");
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderDashboard(await response.json());
  } catch (_error) {
    status.textContent = "数据暂时没有读取成功，请稍后刷新。";
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新";
  }
}

async function checkSession() {
  try {
    const response = await fetch("/api/admin/session", { cache: "no-store" });
    const payload = await response.json().catch(() => ({}));
    if (payload.configured === false) {
      showOnly("setupView");
    } else if (payload.authenticated) {
      showOnly("dashboardView");
      await loadDashboard();
    } else {
      showOnly("loginView");
    }
  } catch (_error) {
    showOnly("loginView");
    setText("loginStatus", "后台暂时无法连接，请稍后刷新。 ");
  }
}

async function login(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = document.querySelector("#loginButton");
  const status = document.querySelector("#loginStatus");
  button.disabled = true;
  button.textContent = "正在验证…";
  status.textContent = "";
  try {
    const response = await fetch("/api/admin/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: form.elements.password.value }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (payload.error === "rate_limited") throw new Error(`尝试过于频繁，请等待 ${payload.retry_after || 2} 秒。`);
      if (payload.error === "invalid_credentials") throw new Error("后台密码不正确。");
      throw new Error("暂时无法登录，请稍后重试。");
    }
    form.reset();
    showOnly("dashboardView");
    await loadDashboard();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "进入后台";
  }
}

async function logout() {
  try {
    await fetch("/api/admin/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } finally {
    showOnly("loginView");
  }
}

function initialize() {
  document.querySelector("#loginForm").addEventListener("submit", login);
  document.querySelector("#logoutButton").addEventListener("click", logout);
  document.querySelector("#refreshButton").addEventListener("click", loadDashboard);
  document.querySelector("#periodSelect").addEventListener("change", loadDashboard);
  setInterval(() => {
    if (!document.hidden && !document.querySelector("#dashboardView").hidden) loadDashboard();
  }, 60_000);
  checkSession();
}

initialize();
