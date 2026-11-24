// 주간보고 작성 자동화 Agent 화면 (바닐라 JS). 서버 API: /api/info, /api/upload, /api/runs ...
const $ = (id) => document.getElementById(id);
const DAYS = ["일", "월", "화", "수", "목", "금", "토"];
const state = { info: null, runId: null, minItems: 5, weeks: null, file: null };

// ---------- 공통 ----------
const isoToText = (iso) => {                       // 2026-11-23 → ‘26.11.23.(월)
  const [y, m, d] = iso.split("-").map(Number);
  return `‘${String(y).slice(2)}.${String(m).padStart(2, "0")}.${String(d).padStart(2, "0")}.(${DAYS[new Date(y, m - 1, d).getDay()]})`;
};
const rangeText = (w) => `${isoToText(w.start)} ~ ${isoToText(w.end)}`;      // 요일 포함: ‘26.11.23.(월) ~ ‘26.11.27.(금)

const CURRENT_BY_STEP = { start: "직전보고 읽기", confirm: "사용자 확인", approve: "사용자 승인", done: null };

function showStep(name) {
  for (const id of ["start", "confirm", "approve", "done"]) $(`step-${id}`).hidden = id !== name;
  state.step = name;
  renderProgress();
}

function banner(message, ok = false) {
  $("banner").hidden = !message;
  $("banner").textContent = message || "";
  $("banner").classList.toggle("ok", ok);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  let data = {};
  try { data = await res.json(); } catch (_) { /* 본문이 JSON이 아니면 빈 값 */ }
  if (!res.ok) {
    const error = new Error((data.errors && data.errors.join("\n")) || data.detail || `요청 실패(${res.status})`);
    error.status = res.status; error.data = data;
    throw error;
  }
  return data;
}
const postJson = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

async function busy(work) {
  $("busy").hidden = false;
  banner("");
  try { return await work(); }
  catch (e) { banner(e.message); }
  finally { $("busy").hidden = true; }
}

// ---------- 1. 직전 보고서 ----------
function renderInfo(info) {
  state.info = info;
  const w = info.weeks;
  $("info").innerHTML = "";
  const lines = [
    ["직전 보고서", `${info.prev_name} (${info.source === "upload" ? "첨부" : "data 폴더"})`],
    ["이번에 작성", info.new_name],
    ["금주 / 차주", `${rangeText(w.this_week)} / ${rangeText(w.next_week)}`],
    ["CSR 접수기간", `${isoToText(w.csr_week.start)} ~ ${isoToText(w.csr_week.end)}`],
  ];
  for (const [k, v] of lines) {
    const div = document.createElement("div");
    div.append(`${k}: `);
    const b = document.createElement("b"); b.textContent = v; div.append(b);
    $("info").append(div);
  }
  $("btn-start").disabled = false;
}

async function loadDefaultInfo() {
  try { renderInfo(await api("/api/info")); }
  catch (e) { $("info").textContent = e.message; $("btn-start").disabled = true; }
}

async function upload() {
  if (!state.file) return;
  const form = new FormData();
  form.append("file", state.file);
  form.append("fallback_date", $("fallback-date").value || "");
  $("busy").hidden = false; banner("");
  try {
    renderInfo(await api("/api/upload", { method: "POST", body: form }));
    $("need-date").hidden = true;
  } catch (e) {
    $("btn-start").disabled = true;
    $("need-date").hidden = !(e.data && e.data.need_date);
    banner(e.message);
  } finally { $("busy").hidden = true; }
}

$("file").addEventListener("change", (ev) => {
  state.file = ev.target.files[0] || null;
  if (!state.file) { $("need-date").hidden = true; banner(""); loadDefaultInfo(); return; }
  $("fallback-date").value = "";
  upload();
});
$("fallback-date").addEventListener("change", upload);

$("btn-start").addEventListener("click", () => busy(async () => {
  const run = await postJson("/api/runs", { prev_name: state.info.prev_name });
  state.runId = run.run_id;
  state.path = run.path || [];
  renderConfirm(run);
}));

// ---------- 2. 내용 입력 ----------
function renderConfirm(run) {
  state.minItems = run.min_items;
  state.weeks = run.weeks;
  $("csr-period").textContent = `접수기간 ${isoToText(run.weeks.csr_week.start)} ~ ${isoToText(run.weeks.csr_week.end)}`;
  $("week-this").textContent = rangeText(run.weeks.this_week);
  $("week-next").textContent = rangeText(run.weeks.next_week);
  for (const col of ["this", "next"]) {
    const box = $(`cands-${col}`);
    box.innerHTML = "";
    const list = run.candidates.filter((c) => c.column === col);
    box.insertAdjacentHTML("beforeend", `<div class="col-title">직전 보고서에서 이어지는 건</div>`);
    if (!list.length) box.insertAdjacentHTML("beforeend", `<div class="empty">후보가 없습니다.</div>`);
    for (const c of list) box.append(candidateEl(c, col));
    const gone = (run.excluded || []).filter((e) => e.column === col);
    if (gone.length) {
      const note = document.createElement("div");
      note.className = "empty";
      note.textContent = "완료(예정)일이 지나 자동 제외: " + gone.map((e) => `${e.title} (${e.end})`).join(", ");
      box.append(note);
    }
  }
  showStep("confirm");
  state.formReady = true;
  if (run.error) banner(run.error);
  update();
}

function candidateEl(c, col) {
  const div = document.createElement("div");
  div.className = "cand";
  div.dataset.col = col; div.dataset.title = c.title; div.dataset.raw = c.raw ? "1" : "";
  const head = document.createElement("div");
  head.className = "cand-head";
  head.innerHTML = `<input type="checkbox" class="sel" checked><span class="title"></span><span class="badge"></span>
    <select class="mark" title="(계속) 표시"><option value="auto">(계속) 자동</option><option value="yes">(계속) 표시</option><option value="no">(계속) 없음</option></select>`;
  head.querySelector(".title").textContent = c.title;
  head.querySelector(".badge").textContent = c.raw ? `${c.page}쪽 · 원문 복사` : `${c.page}쪽`;
  div.append(head);
  if (col === "this" && !c.raw) {      // 금주 이월 건: 개발내용 1줄 필수(굵게 표시)
    const label = document.createElement("label");
    label.className = "addline";
    label.append("개발내용 1줄 추가 (필수, 굵게 표시됩니다)");
    const input = document.createElement("input");
    input.type = "text"; input.className = "addline-input"; input.placeholder = "이번 주 진행 상황 등 한 줄";
    label.append(input); div.append(label);
  }
  return div;
}

const PHASE_KINDS = ["분석/설계", "개발/테스트", "본적용 및 모니터링"];
const CUSTOM = "__custom";
const CUTOVER = "본적용 및 모니터링";          // 종료일 없이 시작일 하루로 쓴다
const HANGUL = /[가-힣]/;
const CODE_RE = /^[A-Za-z0-9_-]+$/;
const weekOf = (col) => state.weeks[col === "this" ? "this_week" : "next_week"];
let itemSeq = 0;

function syncCutover(row) {                  // 본적용 및 모니터링을 고르면 종료일 입력을 숨긴다
  const cut = row.querySelector(".ph-kind").value === CUTOVER;
  row.querySelector(".ph-end").closest("label").hidden = cut;
  row.querySelector(".ph-open").closest("label").hidden = cut;
  row.classList.toggle("cutover", cut);
}

function phaseEl(col, used = []) {          // 시작일·종료일 기본값은 금주/차주 해당 주(월~금)
  const week = weekOf(col);
  const first = PHASE_KINDS.find((k) => !used.includes(k)) || CUSTOM;   // 아직 안 쓴 첫 단계, 다 썼으면 직접입력
  const row = document.createElement("div");
  row.className = "phase";
  row.innerHTML = `<label><span class="lbl">단계</span><select class="ph-kind" aria-label="단계">${PHASE_KINDS.map((k) => `<option value="${k}">${k}</option>`).join("")}<option value="${CUSTOM}">직접입력</option></select>
      <input type="text" class="ph-name" lang="ko" autocomplete="off" aria-label="단계 이름" placeholder="단계 이름 직접 입력(한글)" hidden></label>
    <label><span class="lbl">시작일</span><input type="date" class="ph-start" aria-label="시작일"></label><label><span class="lbl">종료일</span><input type="date" class="ph-end" aria-label="종료일"></label>
    <label class="check"><input type="checkbox" class="ph-open"> 종료일 없음</label>
    <button type="button" class="btn btn-danger btn-sm rm-phase">삭제</button>`;
  row.querySelector(".ph-kind").value = first;
  row.querySelector(".ph-name").hidden = first !== CUSTOM;
  row.querySelector(".ph-start").value = week.start;
  row.querySelector(".ph-end").value = week.end;
  syncCutover(row);
  return row;
}

const colName = (col) => (col === "this" ? "금주" : "차주");

function titleOf(el) {                       // 프로그램명(프로그램코드) 구분 — 서버(graph._title_of)와 같은 규칙
  const name = el.querySelector(".program-name").value.trim();
  if (!name) return "";
  const code = el.querySelector(".program-code").value.trim();
  const kind = el.querySelector(".work-type").value;
  return code ? `${name}(${code}) ${kind}` : `${name} ${kind}`;
}

function addItem(col) {
  const seq = ++itemSeq;                     // 라디오 그룹 이름이 건마다 달라야 서로 영향을 주지 않는다
  const el = document.createElement("div");
  el.className = "item";
  el.dataset.col = col;
  el.innerHTML = `<div class="item-head"><b class="item-title"></b><button type="button" class="btn btn-danger btn-sm rm-item">건 삭제</button></div>
    <div class="inline">들어갈 칸:
      <label><input type="radio" name="col-${seq}" class="col" value="this"> 금주</label>
      <label><input type="radio" name="col-${seq}" class="col" value="next"> 차주</label>
    </div>
    <div class="grid3">
      <label>프로그램명 <span class="opt">(한글)</span><input type="text" class="program-name" lang="ko" autocomplete="off" placeholder="예: 매입원가산입 등록"></label>
      <label>프로그램코드 <span class="opt">(선택)</span><input type="text" class="program-code" autocomplete="off" placeholder="예: 70-2013"></label>
      <label>구분<select class="work-type"><option value="개선 개발">개선 개발</option><option value="신규 개발">신규 개발</option></select></label>
    </div>
    <div class="title-preview"></div>
    <label>사유 <span class="opt">(선택 · 비우면 AI가 작성합니다)</span><textarea class="reason" lang="ko"></textarea></label>
    <label>내용 <span class="opt">(선택 · 줄바꿈 = 항목 구분 · 비우면 AI가 작성합니다)</span><textarea class="content" lang="ko"></textarea></label>
    <div class="grid2">
      <label>요청부서 <span class="req">*</span><input type="text" class="requester" lang="ko" autocomplete="off" placeholder="예: 농협유통 IT지원팀 (한글로 입력)"></label>
      <label>완료(예정)일 <span class="opt">(기본: 차주 종료일)</span><input type="date" class="due"></label>
    </div>
    <div class="col-title">주요일정 <small class="sched-hint"></small></div><div class="phases"></div>
    <button type="button" class="btn btn-add btn-sm add-phase">+ 단계 추가</button>
    <label style="margin-top:10px">(계속) 표시<select class="mark"><option value="auto">자동</option><option value="yes">표시</option><option value="no">없음</option></select></label>`;
  el.querySelector(`input.col[value="${col}"]`).checked = true;
  el.querySelector(".due").value = state.weeks.next_week.end;       // 완료(예정)일 기본값 = 차주 종료일
  el.querySelector(".phases").append(phaseEl(col));
  $(`new-${col}`).append(el);
  update();
}

function moveItem(el, col) {                 // 금주/차주를 바꾸면 그 목록으로 옮기고, 기본값이던 일정 날짜는 그 주로 바꾼다
  const old = weekOf(el.dataset.col), next = weekOf(col);
  el.querySelectorAll(".phase").forEach((p) => {
    const s = p.querySelector(".ph-start"), e = p.querySelector(".ph-end");
    if (s.value === old.start) s.value = next.start;
    if (e.value === old.end) e.value = next.end;
  });
  el.dataset.col = col;
  $(`new-${col}`).append(el);
}

$("btn-add-this").addEventListener("click", () => addItem("this"));
$("btn-add-next").addEventListener("click", () => addItem("next"));
$("step-confirm").addEventListener("click", (ev) => {
  const t = ev.target;
  if (t.classList.contains("rm-item")) t.closest(".item").remove();
  else if (t.classList.contains("add-phase")) {
    const item = t.closest(".item");
    const used = [...item.querySelectorAll(".ph-kind")].map((s) => s.value);
    item.querySelector(".phases").append(phaseEl(item.dataset.col, used));
  }
  else if (t.classList.contains("rm-phase")) t.closest(".phase").remove();
  else return;
  update();
});
$("step-confirm").addEventListener("input", update);
$("step-confirm").addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.classList.contains("ph-kind")) {            // 직접입력을 고르면 이름 입력칸을 보여 준다
    const name = t.closest(".phase").querySelector(".ph-name");
    name.hidden = t.value !== CUSTOM;
    syncCutover(t.closest(".phase"));
    if (!name.hidden) name.focus();
  } else if (t.classList.contains("col")) moveItem(t.closest(".item"), t.value);
  update();
});

const markValue = (v) => (v === "yes" ? true : v === "no" ? false : null);
const num = (id) => ($(id).value === "" ? NaN : Number($(id).value));

function collect() {
  const select = { this: [], next: [] }, continued = {}, add_line = {}, missing = [];
  document.querySelectorAll(".cand").forEach((div) => {
    if (!div.querySelector(".sel").checked) return;
    const { col, title } = div.dataset;
    select[col].push(title);
    const mark = markValue(div.querySelector(".mark").value);
    if (mark !== null) continued[title] = mark;
    const line = div.querySelector(".addline-input");
    if (line) { if (line.value.trim()) add_line[title] = line.value.trim(); else missing.push(title); }
  });
  const items = [];
  document.querySelectorAll("#new-this .item, #new-next .item").forEach((el) => {
    const phases = [...el.querySelectorAll(".phase")].map((p) => {
      const open = p.querySelector(".ph-open").checked;
      const start = p.querySelector(".ph-start").value || null, end = p.querySelector(".ph-end").value || null;
      const kind = p.querySelector(".ph-kind").value;
      const name = kind === CUSTOM ? p.querySelector(".ph-name").value.trim() : kind;
      if (name.startsWith("본적용")) return { name, start, end: start, open_ended: false };     // 종료일 없이 시작일 하루
      return { name, start, end: open ? null : (end || start), open_ended: open };
    });
    items.push({ columns: [el.dataset.col],       // 건이 놓인 목록(금주/차주)이 곧 들어갈 칸
      program_name: el.querySelector(".program-name").value.trim(), program_code: el.querySelector(".program-code").value.trim(),
      work_type: el.querySelector(".work-type").value, reason: el.querySelector(".reason").value,
      content: el.querySelector(".content").value, requester: el.querySelector(".requester").value,
      due: el.querySelector(".due").value || null, phases, continued: markValue(el.querySelector(".mark").value) });
  });
  const csr = { requested: num("csr-requested"), handled: num("csr-handled"), calls: num("csr-calls"),
    outages: num("csr-outages"), on_time: num("csr-on-time") };
  return { answer: { select, new: items, csr, continued, add_line }, missing };
}

const needsHangul = (label, text) => (text.trim() && !HANGUL.test(text) ? [`${label} 한글로 입력하세요(한글이 한 글자 이상 있어야 합니다).`] : []);

function validate({ answer, missing }) {      // 서버(validation.py)와 같은 규칙
  const errors = [];
  answer.new.forEach((n, i) => {
    const add = (list) => list.forEach((m) => errors.push(`신규 건 ${i + 1}: ${m}`));
    if (!(n.program_name.trim() || n.reason.trim() || n.content.trim())) add(["프로그램명, 사유, 내용 중 하나 이상은 입력하세요(비워 둔 항목은 AI가 작성합니다)."]);
    add(needsHangul("프로그램명은", n.program_name)); add(needsHangul("사유는", n.reason)); add(needsHangul("내용은", n.content));
    if (n.program_code && !CODE_RE.test(n.program_code)) add(["프로그램코드는 영문·숫자·하이픈(-)·밑줄(_)만 쓸 수 있습니다(예: 70-2048)."]);
    if (!n.requester.trim()) add(["요청부서를 입력하세요."]); else add(needsHangul("요청부서는", n.requester));
    if (!n.phases.length) add(["주요일정 단계를 하나 이상 입력하세요."]);
    n.phases.forEach((ph) => {
      if (!ph.name) add(["주요일정 단계 이름을 입력하세요."]); else add(needsHangul("주요일정 단계 이름은", ph.name));
      if (!ph.start) add([`'${ph.name}' 시작일을 입력하세요.`]);
      else if (!ph.name.startsWith("본적용") && !ph.open_ended && ph.end && ph.end < ph.start) add([`'${ph.name}' 종료일이 시작일보다 빠릅니다.`]);
    });
  });
  const c = answer.csr;
  if (Object.values(c).some(Number.isNaN)) errors.push("CSR 숫자 5개를 모두 입력하세요.");
  else {
    if (Object.values(c).some((v) => v < 0)) errors.push("CSR 숫자는 0 이상이어야 합니다.");
    if (c.handled > c.requested) errors.push("처리건수가 요청건수보다 많습니다.");
  }
  const count = answer.select.this.length + answer.select.next.length + answer.new.reduce((s, n) => s + n.columns.length, 0);
  if (count < state.minItems) errors.push(`현재 ${count}건 · 금주+차주 합계 ${state.minItems}건 이상이어야 합니다. ${state.minItems - count}건 부족합니다. 신규 건을 추가하세요.`);
  missing.forEach((t) => errors.push(`금주 '${t}': 개발내용 1줄을 입력하세요.`));
  return { errors, count };
}

function update() {
  document.querySelectorAll("#new-this .item, #new-next .item").forEach((el, i) => {
    el.querySelector(".item-title").textContent = `신규 건 ${i + 1} · ${colName(el.dataset.col)}`;
    el.querySelector(".sched-hint").textContent = `${colName(el.dataset.col)} 일정으로 채워집니다. 필요하면 고치세요.`;
    const title = titleOf(el);
    el.querySelector(".title-preview").textContent = title ? `타이틀: ${title}` : "타이틀: 프로그램명을 비우면 AI가 사유를 보고 '프로그램명(프로그램코드) 개선 개발/신규 개발' 형식으로 만듭니다.";
  });
  const data = collect();
  const { errors, count } = validate(data);
  const ok = count >= state.minItems;
  $("count").textContent = `현재 ${count}건 (금주+차주 합계, 최소 ${state.minItems}건)`;
  $("count").className = `count ${ok ? "ok" : "bad"}`;
  $("errors").innerHTML = "";
  errors.forEach((e) => { const li = document.createElement("li"); li.textContent = e; $("errors").append(li); });
  $("btn-generate").disabled = errors.length > 0;
}

$("btn-generate").addEventListener("click", () => busy(async () => {
  try { handleStage(await postJson(`/api/runs/${state.runId}/confirm`, collect().answer)); }
  catch (e) { if (e.status === 422) { $("errors").innerHTML = ""; (e.data.errors || []).forEach((m) => { const li = document.createElement("li"); li.textContent = m; $("errors").append(li); }); } throw e; }
}));

// ---------- 3. 미리보기·승인 ----------
// 건별 미리보기: 신규 등록분은 색·글꼴·표시를 달리해 정확히 작성됐는지 쉽게 확인하게 한다
function pvLines(lines, added) {
  const box = document.createElement("div");
  box.className = "pv-lines";
  lines.forEach((line) => {
    const div = document.createElement("div");
    div.className = "pv-line" + (added.has(line) ? " pv-added" : "");
    div.textContent = line;
    box.append(div);
  });
  return box;
}

function pvRow(label, node) {
  const row = document.createElement("div");
  row.className = "pv-row";
  const name = document.createElement("span");
  name.className = "pv-label";
  name.textContent = label;
  row.append(name, node);
  return row;
}

function pvItemEl(it) {
  const el = document.createElement("div");
  el.className = `pv-item${it.new ? " new" : ""} ${it.column}`;
  const head = document.createElement("div");
  head.className = "pv-head";
  if (it.new) {
    const tag = document.createElement("span");
    tag.className = "pv-new";
    tag.textContent = "신규 · 내용 확인";
    head.append(tag);
  }
  const title = document.createElement("b");
  title.className = "pv-title";
  title.textContent = `○ ${it.title}${it.continued ? " (계속)" : ""}`;
  head.append(title);
  el.append(head);
  if (it.raw) {
    el.append(pvRow("", Object.assign(document.createElement("div"), { textContent: "형식이 다른 건: 직전 보고서 내용을 그대로 복사합니다." })));
    return el;
  }
  const added = new Set(it.added || []);
  el.append(pvRow("사유", pvLines(it.reason.length ? it.reason : ["(없음)"], new Set())));
  el.append(pvRow(it.content_label || "개발내용", pvLines(it.content.length ? it.content : ["(없음)"], added)));
  el.append(pvRow(it.schedule_label || "주요일정", pvLines(it.phases.length ? it.phases : ["(없음)"], new Set())));
  el.append(pvRow("요청부서", pvLines([it.requester || "(없음)"], new Set())));
  el.append(pvRow("완료(예정)일", pvLines([it.due || "(없음)"], new Set())));
  return el;
}

function renderPreviewItems(items) {
  $("pv-legend").innerHTML = `<span class="pv-key new">신규 등록 — 정확히 작성됐는지 확인하세요</span>
    <span class="pv-key added">직접 추가한 개발내용 줄</span><span class="pv-key old">이어지는 건 — 직전 보고서 내용</span>`;
  const box = $("pv-items");
  box.innerHTML = "";
  for (const [col, label] of [["this", "금주 계획사항"], ["next", "차주 계획사항"]]) {
    const list = items.filter((i) => i.column === col);
    const head = document.createElement("div");
    head.className = `pv-col-head ${col}`;
    head.textContent = `${label} · ${list.length}건 (신규 ${list.filter((i) => i.new).length}건)`;
    box.append(head);
    list.forEach((it) => box.append(pvItemEl(it)));
  }
}

function renderApprove(data) {
  renderPreviewItems(data.items || []);
  $("preview").textContent = data.preview;
  $("overwrite").checked = false;
  $("overwrite-row").hidden = !data.existing;
  $("reject-reason").value = "";
  showStep("approve");
  updateApprove(data.existing);
}
function updateApprove(existing = !$("overwrite-row").hidden) {
  $("btn-approve").disabled = existing && !$("overwrite").checked;
  $("btn-reject").disabled = !$("reject-reason").value.trim();
}
$("overwrite").addEventListener("change", () => updateApprove());
$("reject-reason").addEventListener("input", () => updateApprove());
$("btn-approve").addEventListener("click", () => busy(async () =>
  handleStage(await postJson(`/api/runs/${state.runId}/approve`, { answer: $("overwrite").checked ? "승인 덮어쓰기" : "승인" }))));
$("btn-reject").addEventListener("click", () => busy(async () =>
  handleStage(await postJson(`/api/runs/${state.runId}/approve`, { answer: `반려: ${$("reject-reason").value.trim()}` }))));

// ---------- 4. 완료 ----------
function renderDone(data) {
  state.doneNode = data.saved ? "저장" : "사람 전담";
  $("result").textContent = data.result;
  $("saved-note").hidden = !data.saved;
  $("download").hidden = !data.saved;
  if (data.saved) $("download").href = `/api/runs/${state.runId}/download`;
  showStep("done");
}
$("btn-restart").addEventListener("click", () => location.reload());

function returnToConfirm(data) {       // 반려: 입력해 둔 내용을 그대로 두고 2단계로 돌아가 수정하게 한다
  showStep("confirm");
  banner(data.reject_reason ? `반려 사유: ${data.reject_reason} — 내용을 수정한 뒤 다시 생성하세요.` : (data.error || ""));
  window.scrollTo(0, 0);
  update();
}

function handleStage(data) {
  state.path = data.path || state.path || [];
  if (data.stage === "approve") renderApprove(data);
  else if (data.stage === "done") renderDone(data);
  else if (state.formReady) returnToConfirm(data);
  else renderConfirm(data);
}

// ---------- 하단 Agent Workflow (서버가 실제 LangGraph 정의에서 노드·연결을 만들어 준다) ----------
const KIND_CLASS = { "도구": "tool", "코드": "code", "LLM": "llm", "사람": "human", "가드레일": "guard", "이관": "handoff" };
const KIND_COLOR = { tool: "#1f6feb", code: "#6b7782", llm: "#8e44ad", human: "#e67e22", guard: "#2e7d32", handoff: "#c62828" };
// 흐름도 배치(열, 행): 위 줄은 왼쪽→오른쪽, 아래 줄은 오른쪽→왼쪽으로 이어지는 ㄹ자 배치
const WF_LAYOUT = { "직전보고 읽기": [0, 0], "주차 계산": [1, 0], "이월 후보": [2, 0], "사용자 확인": [3, 0], "초안": [4, 0],
  "AI 점검": [4, 1], "계속 표시": [3, 1], "검증": [2, 1], "사용자 승인": [1, 1], "저장": [0, 1], "사람 전담": [1.5, 2] };
const WF = { W: 156, H: 70, GX: 200, GY: 135, PAD: 28 };
const SVG_NS = "http://www.w3.org/2000/svg";
const wfInfo = {};

function svgEl(tag, attrs = {}, text) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

function showDetail(id) {
  const n = wfInfo[id];
  $("wf-detail").innerHTML = "";
  if (!n) return;
  const kind = KIND_CLASS[n.kind] || "code";
  const tag = document.createElement("span");
  tag.className = `wf-chip kind-${kind}`; tag.textContent = n.kind;
  const name = document.createElement("b"); name.textContent = ` ${n.id} `;
  $("wf-detail").append(tag, name, document.createTextNode(`— ${n.desc}`));
}

function renderProgress() {                  // 서버가 알려 준 실제 실행 기록(state.path)으로 완료·진행 중·대기를 그린다
  const path = state.path || [];
  const current = CURRENT_BY_STEP[state.step] ?? null;
  const visited = new Set(path);
  const seq = current ? [...path, current] : path;
  const traversed = new Set();
  for (let i = 1; i < seq.length; i++) traversed.add(`${seq[i - 1]}→${seq[i]}`);
  document.querySelectorAll(".wf-g").forEach((g) => {
    const id = g.dataset.id;
    g.classList.toggle("active", id === current);
    g.classList.toggle("done", visited.has(id) && id !== current);
    g.classList.toggle("pending", !visited.has(id) && id !== current);
  });
  document.querySelectorAll(".wf-edge, .wf-label").forEach((el) => {
    const on = traversed.has(`${el.dataset.from}→${el.dataset.to}`);
    el.classList.toggle("done", on);
    el.classList.toggle("dim", !on);
    if (el.classList.contains("wf-edge")) el.setAttribute("marker-end", `url(#${on ? "arrow-done" : el.dataset.marker})`);
  });
  const total = Object.keys(wfInfo).length;
  const last = path[path.length - 1];
  $("wf-summary").textContent = total
    ? (current ? `현재 단계: ${current}` : `종료: ${last || "-"}`) + ` · 지나온 노드 ${visited.size}/${total}`
    : "";
  if (current || last) showDetail(current || last);
}

function buildDiagram(wf) {
  const { W, H, GX, GY, PAD } = WF;
  const pos = {};
  let extra = 3;                                  // 배치표에 없는 노드는 아래 줄에 차례로 놓는다
  wf.nodes.forEach((n, i) => {
    const [c, r] = WF_LAYOUT[n.id] || [i % 5, extra++ > 0 ? 3 : 3];
    pos[n.id] = { x: PAD + c * GX, y: PAD + r * GY };
  });
  const rows = Math.max(...Object.values(pos).map((p) => p.y)) + H + PAD + 10;
  const width = PAD * 2 + GX * 4 + W;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${rows}`, role: "img", "aria-label": "에이전트 workflow 흐름도" });
  const defs = svgEl("defs");
  for (const [id, color] of [["arrow", "#6b7782"], ["arrow-back", "#e67e22"], ["arrow-stop", "#c62828"], ["arrow-done", "#1f6feb"]]) {
    const m = svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
    m.append(svgEl("path", { d: "M0,0 L10,5 L0,10 z", fill: color }));
    defs.append(m);
  }
  svg.append(defs);

  const label = (x, y, text, color) => {         // 화살표 옆 짧은 글(흰 바탕)
    const w = text.length * 12 + 14;
    const g = svgEl("g");
    g.append(svgEl("rect", { x: x - w / 2, y: y - 11, width: w, height: 20, rx: 10, fill: "#fff", stroke: color, "stroke-width": 1 }));
    g.append(svgEl("text", { x, y: y + 4, "text-anchor": "middle", "font-size": 12, fill: color }, text));
    return g;
  };
  const backEnds = {};                            // 같은 노드로 돌아오는 화살표가 겹치지 않게 끝점을 나눈다
  const labels = [];                              // 라벨은 모든 선을 그린 뒤에 얹어 다른 선에 가려지지 않게 한다
  wf.edges.forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const cond = e.conditional, toStop = wf.nodes.find((n) => n.id === e.to).kind === "이관";
    const color = toStop ? "#c62828" : (b.y < a.y ? "#e67e22" : "#6b7782");
    const marker = toStop ? "arrow-stop" : (b.y < a.y ? "arrow-back" : "arrow");
    let d, mid;
    if (a.y === b.y) {                            // 같은 줄: 옆으로
      const right = b.x > a.x;
      const x1 = right ? a.x + W : a.x, x2 = right ? b.x : b.x + W, y = a.y + H / 2;
      d = `M${x1},${y} L${x2},${y}`; mid = [(x1 + x2) / 2, y - 16];
    } else if (b.y > a.y) {                       // 아래 줄로
      const dx = toStop ? (b.x > a.x ? -26 : 26) : 0;
      const shift = (b.x === a.x && !toStop) ? 34 : 0;       // 초안 → 계속 표시: 위로 돌아오는 선과 겹치지 않게 오른쪽으로
      const x1 = a.x + W / 2 + shift, x2 = b.x + W / 2 + dx + shift;
      d = `M${x1},${a.y + H} L${x2},${b.y}`; mid = [(x1 + x2) / 2 + (toStop ? (b.x > a.x ? -26 : 26) : 22), (a.y + H + b.y) / 2];
    } else {                                      // 위 줄로 돌아가는 선(재작성·반려)
      const n = (backEnds[e.to] = (backEnds[e.to] || 0) + 1);
      const x1 = a.x + W / 2, y1 = a.y, x2 = b.x + W / 2 - 14 - (n - 1) * 34, y2 = b.y + H;
      d = `M${x1},${y1} C${x1},${y1 - 55} ${x2},${y2 + 55} ${x2},${y2}`;
      const t = n === 1 ? 0.5 : 0.36, u = 1 - t;      // 곡선 위에서 서로 다른 지점에 라벨을 둔다
      const bez = (p0, p1, p2, p3) => u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
      mid = [bez(x1, x1, x2, x2), bez(y1, y1 - 55, y2 + 55, y2)];
    }
    svg.append(svgEl("path", { d, fill: "none", stroke: color, "stroke-width": 2, "marker-end": `url(#${marker})`,
      "stroke-dasharray": cond ? "7 5" : "none", class: "wf-edge", "data-from": e.from, "data-to": e.to, "data-marker": marker }));
    if (e.label) {
      const tag = label(mid[0], mid[1], e.label, color);
      tag.setAttribute("class", "wf-label"); tag.setAttribute("data-from", e.from); tag.setAttribute("data-to", e.to);
      labels.push(tag);
    }
  });
  labels.forEach((g) => svg.append(g));

  wf.nodes.forEach((n, i) => {
    const p = pos[n.id], kind = KIND_CLASS[n.kind] || "code", color = KIND_COLOR[kind];
    const g = svgEl("g", { class: "wf-g", "data-id": n.id, transform: `translate(${p.x},${p.y})`, tabindex: 0 });
    g.append(svgEl("title", {}, n.desc));
    g.append(svgEl("rect", { class: "wf-box", width: W, height: H, rx: 10, fill: "#fff", stroke: color, "stroke-width": 2,
      "stroke-dasharray": n.terminal ? "6 4" : "none" }));
    g.append(svgEl("rect", { width: W, height: 8, rx: 4, fill: color }));
    g.append(svgEl("text", { x: 14, y: 30, "font-size": 12, fill: "#6b7782" }, String(i + 1)));
    g.append(svgEl("text", { x: W / 2, y: 40, "text-anchor": "middle", "font-size": 15, "font-weight": 700, fill: "#1f2933" }, n.id));
    g.append(svgEl("rect", { x: W / 2 - 30, y: 48, width: 60, height: 17, rx: 8.5, fill: color }));
    g.append(svgEl("text", { x: W / 2, y: 60, "text-anchor": "middle", "font-size": 11, fill: "#fff" }, n.kind));
    g.append(svgEl("circle", { class: "wf-check", cx: W - 14, cy: 24, r: 9, fill: "#2e7d32" }));
    g.append(svgEl("text", { class: "wf-check", x: W - 14, y: 28.5, "text-anchor": "middle", "font-size": 12, "font-weight": 700, fill: "#fff" }, "✓"));
    g.addEventListener("mouseenter", () => showDetail(n.id));
    g.addEventListener("click", () => showDetail(n.id));
    svg.append(g);
  });
  return svg;
}

async function loadWorkflow() {
  try {
    const wf = await api("/api/workflow");
    wf.nodes.forEach((n) => { wfInfo[n.id] = n; });
    $("wf-diagram").innerHTML = "";
    $("wf-diagram").append(buildDiagram(wf));
    $("wf-legend").innerHTML = Object.entries(KIND_CLASS).map(([k, c]) => `<span class="wf-chip kind-${c}">${k}</span>`).join("")
      + `<span class="wf-key"><i class="solid"></i>순서대로 <i class="dash"></i>조건 분기 · <b class="wf-done-mark">✓</b>완료 · 파란 테두리 진행 중 · 흐림 대기</span>`;
    renderProgress();
  } catch (e) {
    $("wf-diagram").textContent = `workflow를 불러오지 못했습니다(${e.message}). 서버를 다시 시작한 뒤 새로고침하세요.`;
  }
}

$("wf-toggle").addEventListener("click", () => {
  $("wf-body").hidden = !$("wf-body").hidden;
  $("wf-toggle").textContent = $("wf-body").hidden ? "펼치기" : "접기";
});

// ---------- 시작 ----------
showStep("start");
loadDefaultInfo();
loadWorkflow();
