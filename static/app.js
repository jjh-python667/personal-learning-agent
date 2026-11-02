// 网页端只负责显示和发送请求。密码验证、模型调用和数据保存都在后端。
function locationUnit(x) {
  return x.location_kind === "section" ? "片段" : "页";
}
function sourceLabel(s) {
  return esc(s.title) + " 第" + s.page + locationUnit(s);
}
const $ = (s) => document.querySelector(s);
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let state = {},
  page = "chat",
  busy = false;
const pages = {
  chat: ["✦", "学习对话", "一起学点什么？"],
  tasks: ["▷", "学习任务", "页窗推进与暂停继续"],
  library: ["▤", "资料库", "让知识有处可寻"],
  quiz: ["✓", "互动复习", "把理解变成掌握"],
  memory: ["◷", "学习记录", "记录每一点进步"],
  reminders: ["♧", "复习提醒", "和未来的自己约个时间"],
  activity: ["≡", "执行记录", "每个操作都有迹可循"],
};
const fmt = (s) =>
  new Date(s).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
const empty = (text, symbol = "✧") =>
  `<div class="empty"><div class="symbol">${symbol}</div>${text}</div>`;
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").style.display = "block";
  clearTimeout(window.toastTimer);
  window.toastTimer = setTimeout(
    () => ($("#toast").style.display = "none"),
    5500,
  );
}
async function api(path, method = "GET", body) {
  const opts = { method };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch("/api" + path, opts);
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {}
    throw Error(
      typeof data?.detail === "string"
        ? data.detail
        : `请求失败（${response.status}）`,
    );
  }
  return response.json();
}
async function refresh(background = false) {
  try {
    state = await api("/state");
    $("#model").textContent = state.configured
      ? `DeepSeek · ${state.model}`
      : "DeepSeek 尚未配置";
    $("#notice").innerHTML = state.configured
      ? ""
      : '<div class="banner">还没有配置 DeepSeek API Key。你可以先上传资料、设置提醒；AI 对话需在服务器 .env 填写 Key 后重启。</div>';
    renderDue();
    if (!background) render();
  } catch (e) {
    toast(e.message);
  }
}
function renderDue() {
  const due = (state.reminders || []).filter((x) => x.status === "due");
  $("#due").innerHTML = due
    .map(
      (x) =>
        `<div class="banner due row spread"><span>⏰ ${esc(x.title)} · ${fmt(x.due)}</span><button class="small outline" data-action="done" data-id="${x.id}">已处理</button></div>`,
    )
    .join("");
}
function render() {
  if (!state.documents) return;
  state.tasks = state.tasks || [];
  $("#heading").textContent = pages[page][2];
  $("#nav").innerHTML = Object.entries(pages)
    .map(
      ([id, x]) =>
        `<button data-page="${id}" class="${page === id ? "active" : ""}">${x[0]}　${x[1]}</button>`,
    )
    .join("");
  const view = $("#view");
  if (page === "chat") {
    view.innerHTML = `<div class="grid"><div class="card"><span class="metric-icon">▤</span><div class="metric-label">已保存资料</div><div class="metric">${state.documents.length}<small class="subtle"> 份</small></div></div><div class="card"><span class="metric-icon">◷</span><div class="metric-label">已确认学习记录</div><div class="metric">${state.memories.filter((x) => x.status === "confirmed").length}<small class="subtle"> 条</small></div></div><div class="card"><span class="metric-icon">✓</span><div class="metric-label">待复习错题</div><div class="metric">${state.quizzes.filter((x) => x.correct === 0).length}<small class="subtle"> 道</small></div></div></div>${
      (state.tasks || []).filter((t) =>
        ["active", "waiting_answer", "paused"].includes(t.status),
      ).length
        ? `<div class="card"><div class="row spread"><strong>学习任务</strong><button class="soft small" data-page="tasks">全部</button></div>${(
            state.tasks || []
          )
            .filter((t) =>
              ["active", "waiting_answer", "paused"].includes(t.status),
            )
            .slice(0, 3)
            .map(
              (t) =>
                `<div class="row spread" style="margin-top:8px"><span class="subtle">#${t.id} · 已覆盖${t.covered_through} · ${{ active: "进行中", waiting_answer: "待答题", paused: "暂停" }[t.status]}</span><span class="row">${t.status !== "paused" ? `<button class="outline small" data-action="pauseTask" data-id="${t.id}">暂停</button>` : ""}<button class="soft small" data-action="resumeTask" data-id="${t.id}">继续</button></span></div>`,
            )
            .join("")}</div>`
        : ""
    }<div class="card"><div id="chatbox" class="chatbox">${state.messages.length ? state.messages.map((m) => `<div class="bubble ${m.role}"><div class="who">${m.role === "user" ? "你" : "学习搭子"} · ${fmt(m.created)}</div>${esc(m.content)}</div>`).join("") : `<div class="welcome"><div class="spark">✦</div><h2>从一个问题，或一份资料开始。</h2><div class="subtle">一起拆解概念、练一道题，也把学过的知识留住。</div><div class="chips"><button class="soft" data-prompt="列出我的学习资料">看看资料库</button><button class="soft" data-prompt="根据我的计算机网络资料出一道复习题">练一道题</button><button class="soft" data-prompt="查询我上次的学习进度">回顾学习进度</button></div></div>`}</div><form id="chatForm" class="composer"><textarea id="chatInput" placeholder="发来一个问题，或者说：保存下面这份学习资料……" maxlength="14000" required></textarea><div class="row"><span class="subtle" id="chatStatus">${busy ? "正在执行，请稍候…" : "Enter 发送 · Shift + Enter 换行"}</span><button id="send" ${busy ? "disabled" : ""}>${busy ? "处理中…" : "发送 ↗"}</button></div></form></div>`;
    $("#chatForm").onsubmit = send;
    $("#chatInput").onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        $("#chatForm").requestSubmit();
      }
    };
    $("#chatbox").scrollTop = $("#chatbox").scrollHeight;
  } else if (page === "tasks") {
    const statusLabel = {
      active: "进行中",
      waiting_answer: "等待答题",
      paused: "已暂停",
      completed: "已完成",
      failed: "失败",
    };
    const list = state.tasks || [];
    view.innerHTML = `<div class="card subtle">状态机按页窗推进：读→讲解→出题。暂停保留游标；继续时若无待答题则自动推进下一块。</div>${list.map((t) => `<div class="card row spread"><div><h3>#${t.id} · ${esc(t.document_title || t.title)}</h3><span class="subtle">已覆盖第${t.covered_through}${t.location_kind === "section" ? "片段" : "页"} · 游标第${t.cursor_page} · 每次${t.pages_per_turn}</span> <span class="tag ${t.status === "completed" ? "good" : t.status === "failed" ? "bad" : ""}">${statusLabel[t.status] || esc(t.status)}</span></div><div class="row">${["active", "waiting_answer"].includes(t.status) ? `<button class="outline small" data-action="pauseTask" data-id="${t.id}">暂停</button>` : ""}${["paused", "active", "waiting_answer"].includes(t.status) ? `<button class="soft small" data-action="resumeTask" data-id="${t.id}">继续</button>` : ""}</div></div>`).join("") || empty("还没有学习任务。对话说：带我学完编号1的PDF，每次两页。")}`;
  } else if (page === "library") {
    view.innerHTML = `<div class="two"><form id="textForm" class="card"><h2>保存文字资料</h2><input id="docTitle" placeholder="资料标题，例如：计算机网络第三章" maxlength="120" required><label>学习内容</label><textarea id="docContent" style="min-height:180px" placeholder="粘贴笔记或课程资料。这里的文字作为资料保存。" required maxlength="200000"></textarea><div class="toolbar" style="margin-top:14px;margin-bottom:0"><button>保存资料</button></div></form><div class="card"><h2>上传 Word / PDF / TXT / Markdown</h2><p class="subtle">支持.docx（旧版.doc请先另存为.docx），每份最多12MB，PDF最多120页。扫描件暂不支持文字识别；部分扫描页面可能无法提取。</p><input id="file" type="file" accept=".docx,.pdf,.txt,.md"><button id="upload" class="soft">＋ 选择文件</button><p class="subtle">Word导入正文和表格文字，按片段引用；不保留排版、图片和原始页码。PDF保留原始页码。原始文件不落盘，仅保存提取后的文字。</p></div></div><h2>我的资料 <span class="tag">${state.documents.length}</span></h2>${state.documents.map((d) => `<div class="card row spread"><div><h3>${esc(d.title)}</h3><span class="subtle">#${d.id} · ${d.page_count} ${locationUnit(d)} · ${d.characters.toLocaleString()} 字符</span></div><div class="row"><button class="soft small" data-action="read" data-id="${d.id}">查看</button><button class="outline small" data-action="summarize" data-id="${d.id}">整理重点</button><button class="danger small" data-action="deleteDoc" data-id="${d.id}">删除</button></div></div>`).join("") || empty("还没有资料，上传第一份笔记吧。")}`;
    $("#textForm").onsubmit = async (e) => {
      e.preventDefault();
      await action(
        () =>
          api("/documents", "POST", {
            title: $("#docTitle").value,
            content: $("#docContent").value,
          }),
        "资料已保存",
      );
    };
    $("#upload").onclick = () => $("#file").click();
    $("#file").onchange = async () => {
      if (!$("#file").files.length) return;
      const f = new FormData();
      f.append("file", $("#file").files[0]);
      $("#upload").disabled = true;
      try {
        const r = await api("/upload", "POST", f);
        toast(
          `资料已保存，${r.pages}${locationUnit(r)}${r.empty_pages ? `，其中${r.empty_pages}页未提取到文字` : ""}`,
        );
        await refresh();
      } catch (e) {
        toast(e.message);
        $("#upload").disabled = false;
      }
    };
  } else if (page === "memory") {
    view.innerHTML = `<div class="card subtle">学习进度先由 Agent 提出，只有你点击「确认」后才进入正式记忆。资料保存、提醒和错题记录独立管理。</div>${state.memories.map((m) => `<div class="card"><div class="row spread"><span class="tag ${m.status === "confirmed" ? "good" : ""}">${{ pending: "待你确认", confirmed: "已确认", rejected: "已拒绝" }[m.status]}</span><span class="subtle">${fmt(m.created)}</span></div><p class="content">${esc(m.content)}</p><div class="row">${m.status === "pending" ? `<button class="small" data-action="confirm" data-id="${m.id}">确认记录</button><button class="outline small" data-action="reject" data-id="${m.id}">不记录</button>` : ""}<button class="danger small" data-action="deleteMemory" data-id="${m.id}">删除</button></div></div>`).join("") || empty("还没有学习记录，可以在对话中告诉搭子你学到了哪里。")}`;
  } else if (page === "reminders") {
    view.innerHTML = `<form id="reminderForm" class="card"><h2>约一次复习</h2><div class="two"><div><label>复习什么</label><input id="reminderTitle" required maxlength="300" placeholder="例如：复习TCP三次握手"></div><div><label>北京时间（UTC+08:00）</label><input id="reminderDue" type="datetime-local" required></div></div><div class="toolbar" style="margin-top:16px;margin-bottom:0"><button>设置提醒</button></div><p class="subtle">网页每10秒检查一次到期提醒。关闭网页期间不发送通知，再次打开后补显到期事项；QQ 官方通道不主动推送。</p></form>${state.reminders.map((r) => `<div class="card row spread"><div><h3>${esc(r.title)}</h3><span class="subtle">${fmt(r.due)} · ${{ pending: "等待提醒", due: "已到时间", done: "已处理", cancelled: "已取消" }[r.status]}</span></div>${["pending", "due"].includes(r.status) ? `<div class="row"><button class="small soft" data-action="done" data-id="${r.id}">已处理</button><button class="small outline" data-action="cancelReminder" data-id="${r.id}">取消</button></div>` : ""}</div>`).join("") || empty("还没有约定的复习时间。")}`;
    $("#reminderForm").onsubmit = async (e) => {
      e.preventDefault();
      await action(
        () =>
          api("/reminders", "POST", {
            title: $("#reminderTitle").value,
            due: $("#reminderDue").value + ":00+08:00",
          }),
        "提醒已保存",
      );
    };
  } else if (page === "quiz") {
    const q = state.quizzes.find((x) => x.answer === null);
    const wrong = state.quizzes.filter((x) => x.correct === 0);
    view.innerHTML = `<div class="card"><div class="row spread"><h2>一次一道，认真理解</h2><button class="soft" data-prompt="请先列出资料，再根据我最近保存的资料选择一个主题，出一道复习题">生成复习题</button></div>${q ? `<span class="tag">第 ${q.id} 题</span><p class="content">${esc(q.question)}</p><div class="source">来源：${q.sources.map(sourceLabel).join("；")}</div><form id="answerForm"><label>你的回答</label><textarea id="quizAnswer" required maxlength="5000" placeholder="不用追求完美，先说说你的理解。"></textarea><div class="row" style="margin-top:12px"><button id="grade">提交并讲解</button><button type="button" class="outline" data-action="skip" data-id="${q.id}">跳过这题</button></div></form>` : empty("当前没有待回答题目。先上传资料，再生成一道题。")}</div><h2>错题本 <span class="tag">${wrong.length}</span></h2>${wrong.map((q) => `<div class="card"><h3>${esc(q.question)}</h3><p class="subtle content">你的回答：${esc(q.answer)}</p><p class="content">${esc(q.explanation)}</p><div class="source">来源：${q.sources.map(sourceLabel).join("；")}</div></div>`).join("") || empty("还没有错题。答题后会在这里保留需要巩固的内容。")}<h2>最近答题</h2>${state.quizzes
      .filter((x) => x.answer !== null)
      .slice(0, 6)
      .map(
        (q) =>
          `<details class="card"><summary>${esc(q.question)} <span class="tag ${q.correct === 1 ? "good" : q.correct === 0 ? "bad" : ""}">${q.correct === 1 ? "正确" : q.correct === 0 ? "需要巩固" : "已跳过"}</span></summary><p class="content">${esc(q.explanation)}</p><p class="subtle content">参考答案：${esc(q.expected)}</p></details>`,
      )
      .join("")}`;
    if (q)
      $("#answerForm").onsubmit = async (e) => {
        e.preventDefault();
        $("#grade").disabled = true;
        try {
          const r = await api(`/quizzes/${q.id}/answer`, "POST", {
            answer: $("#quizAnswer").value,
          });
          toast(
            r.correct
              ? "答对了！看看讲解巩固一下。"
              : "已批改，讲解和答案已保存到错题本。",
          );
          await refresh();
        } catch (e) {
          toast(e.message);
          $("#grade").disabled = false;
        }
      };
  } else if (page === "activity") {
    view.innerHTML = `<div class="card subtle">这里只记录实际任务与工具调用。失败或中断不会自动重放已经完成的保存操作；请检查结果后再继续。</div>${
      state.runs
        .map(
          (r) =>
            `<div class="card"><div class="row spread"><span class="tag ${r.status === "completed" ? "good" : ""}">${{ working: "正在执行", completed: "已完成", failed: "执行失败", interrupted: "重启后中断" }[r.status] || esc(r.status)}</span><span class="subtle">${fmt(r.created)}</span></div><p class="content">${esc(r.request)}</p><div class="trace">${
              JSON.parse(r.trace)
                .map((t) => `${t.ok ? "✓" : "×"} ${esc(t.tool)}`)
                .join(" → ") || "没有工具调用"
            }</div></div>`,
        )
        .join("") || empty("还没有执行记录。")
    }`;
  }
}
async function action(fn, message) {
  try {
    await fn();
    if (message) toast(message);
    await refresh();
  } catch (e) {
    toast(e.message);
  }
}
async function send(e) {
  e?.preventDefault();
  if (busy) return;
  const text = $("#chatInput")?.value.trim();
  if (!text) return;
  busy = true;
  $("#send").disabled = true;
  $("#chatStatus").textContent = "正在检索与执行，请稍候…";
  try {
    const r = await api("/chat", "POST", { text });
    toast(r.trace.length ? `完成了 ${r.trace.length} 次工具调用` : "已回复");
    await refresh();
  } catch (e) {
    toast(e.message);
    await refresh();
  } finally {
    busy = false;
    if (page === "chat") {
      render();
      $("#chatInput")?.focus();
    }
  }
}
document.addEventListener("click", async (e) => {
  const el = e.target.closest("button");
  if (!el) return;
  if (el.dataset.page) {
    page = el.dataset.page;
    render();
    return;
  }
  if (el.dataset.prompt) {
    page = "chat";
    render();
    $("#chatInput").value = el.dataset.prompt;
    $("#chatInput").focus();
    return;
  }
  const id = el.dataset.id;
  switch (el.dataset.action) {
    case "read":
      try {
        const d = await api("/documents/" + id);
        $("#documentTitle").textContent = d.title;
        $("#documentText").textContent = d.pages
          .map((t, i) => `第 ${i + 1} ${locationUnit(d)}\n\n${t}`)
          .join("\n\n────────\n\n");
        $("#documentDialog").showModal();
      } catch (e) {
        toast(e.message);
      }
      break;
    case "summarize":
      page = "chat";
      render();
      $("#chatInput").value =
        `请整理资料编号${id}的重点，先确认资料的位置类型和页数或片段数，再读取内容。明确说明实际覆盖的页码或片段编号，如果没有读完，不要声称完成全文总结。`;
      $("#chatInput").focus();
      break;
    case "deleteDoc":
      if (confirm("删除这份资料？已有错题中的来源摘录仍会保留。"))
        await action(() => api("/documents/" + id, "DELETE"), "资料已删除");
      break;
    case "confirm":
    case "reject":
      await action(
        () => api("/memories/" + id, "POST", { action: el.dataset.action }),
        "记录已更新",
      );
      break;
    case "deleteMemory":
      if (confirm("删除这条学习记录？"))
        await action(
          () => api("/memories/" + id, "POST", { action: "delete" }),
          "记录已删除",
        );
      break;
    case "done":
    case "cancelReminder":
      await action(
        () =>
          api("/reminders/" + id, "POST", {
            action: el.dataset.action === "done" ? "done" : "cancelled",
          }),
        "提醒已更新",
      );
      break;
    case "skip":
      await action(() => api("/quizzes/" + id + "/skip", "POST"), "已跳过");
      break;
    case "pauseTask":
      await action(() => api("/tasks/" + id + "/pause", "POST"), "任务已暂停");
      break;
    case "resumeTask":
      try {
        const r = await api("/tasks/" + id + "/resume", "POST");
        toast(r.advanced ? "已继续并推进一块" : "任务已继续");
        await refresh();
      } catch (e) {
        toast(e.message);
      }
      break;
  }
});
$("#refresh").onclick = () => refresh();
$("#closeDialog").onclick = () => $("#documentDialog").close();
refresh();
setInterval(() => refresh(true), 10000);
