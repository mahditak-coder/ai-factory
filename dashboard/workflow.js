/* AF NEXT — mobile-first human-gated studio workspace. No auto-approval. */
(function () {
  const $ = (id) => document.getElementById(id);
  let st = null, selected = null, es = null, poll = null;
  let project = new URLSearchParams(location.search).get("project") || "";

  function err(msg) { $("err").textContent = String(msg || "").slice(0, 240); }
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = String(text);
    return n;
  }
  async function api(path, opt) {
    const r = await fetch(path, opt || {});
    let d; try { d = await r.json(); } catch (_) { d = { ok: false }; }
    if (!r.ok || d.ok === false) throw new Error(d.error || d.note || ("HTTP " + r.status));
    return d;
  }
  function post(path, body) {
    return api(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
  }
  function enc(s) { return encodeURIComponent(s); }
  function artifactUrl(rel) {
    return "/api/workflow/artifact?project=" + enc(project) + "&path=" + enc(rel);
  }
  function label(s) {
    return ({ RUNNING:"در حال اجرا", AWAITING_USER_REVIEW:"منتظر تأیید شما",
      AWAITING_USER_ANSWER:"منتظر پاسخ شما", COMPLETED:"تکمیل شده",
      REJECTED:"رد شده — نیاز به اصلاح", FAILED:"شکست", PENDING:"در انتظار",
      APPROVED:"تأیید شده", REVISION:"قابل اصلاح" })[s] || s;
  }

  async function loadProjects() {
    const { projects } = await api("/api/projects");
    $("projectSel").innerHTML = '<option value="">— انتخاب پروژه —</option>' +
      projects.map((p) => '<option value="' + String(p.name).replace(/"/g, "") + '">' +
        String(p.name).replace(/</g, "&lt;") + "</option>").join("");
    if (project) $("projectSel").value = project;
  }

  async function loadState() {
    if (!project) return;
    const r = await api("/api/workflow/state?project=" + enc(project));
    apply(r.result);
  }

  async function loadCritique() {
    if (!project) return;
    let d;
    try { d = await api("/api/workflow/critique?project=" + enc(project)); }
    catch (_) { return; }
    const box = $("critiqueBox");
    if (!box) return;
    box.replaceChildren();
    const rows = d.entries || [];
    $("critiqueCard").hidden = rows.length === 0;
    for (const e of rows) {
      const line = el("div", "wf-meta");
      const title = el("strong", "", "[" + e.kind + "] " + (e.id || ""));
      line.appendChild(title);
      line.appendChild(el("span", "", "  •  چرخه " + (e.cycle || 1) + "/" + 3));
      if (e.severity) line.appendChild(el("span", "", "  •  " + e.severity));
      if (e.status) line.appendChild(el("span", "", "  •  " + e.status));
      box.appendChild(line);
      for (const f of (e.findings || [])) {
        const item = el("div", "wf-meta");
        item.appendChild(el("span", "", f.id + " — " + (f.owner || "") +
          " — " + (f.required_correction || "")));
        box.appendChild(item);
      }
      if (e.selected_option) {
        box.appendChild(el("div", "wf-meta", "انتخاب: " + e.selected_option));
        for (const r of (e.rejected_options || []))
          box.appendChild(el("div", "wf-meta",
            "ردشده: " + (r.option || "") + " — " + (r.reason || "")));
      }
      if (e.reason) box.appendChild(el("div", "wf-meta", e.reason));
    }
  }

  function watch() {
    if (es) es.close();
    if (poll) { clearInterval(poll); poll = null; }
    es = new EventSource("/api/workflow/events?project=" + enc(project));
    es.onmessage = (e) => { try { apply(JSON.parse(e.data)); } catch (_) {} };
    es.onerror = () => {
      if (es) es.close();
      es = null;
      if (!poll) poll = setInterval(() => loadState().catch(() => {}), 3000);
    };
  }

  function apply(next) {
    st = next;
    loadCritique().catch(() => {});
    if (!st) { $("stagebar").hidden = true; $("timelineCard").hidden = true;
      $("detailCard").hidden = true; return; }
    $("stagebar").hidden = false; $("timelineCard").hidden = false;
    $("detailCard").hidden = true;
    const pill = $("statusPill");
    pill.textContent = label(st.status);
    pill.dataset.s = st.status;
    $("stageMeta").textContent = "ویرایش " + st.rev + " · " + st.stages.length + " مرحله";
    const pause = $("pauseNote");
    pause.hidden = !st.pause_reason;
    pause.textContent = st.pause_reason === "user-review"
      ? "اجرای متخصص بعدی تا تأیید شما متوقف است."
      : st.pause_reason === "user-answer" ? "تا پاسخ شما هیچ مرحلهای اجرا نمیشود." : "";
    if (!selected || !st.stages.some((s) => s.id === selected)) selected = st.current_stage;
    renderTimeline();
    renderDetail();
  }

  function renderTimeline() {
    const ol = $("timeline"); ol.innerHTML = "";
    st.stages.forEach((s) => {
      const li = el("li", s.id === selected ? "active" : "");
      li.appendChild(el("span", "wf-dot")); li.dataset.s = s.status;
      li.querySelector(".wf-dot").dataset.s = s.status;
      const box = el("div");
      box.appendChild(el("div", "", s.title));
      box.appendChild(el("div", "wf-meta", label(s.status) +
        (s.versions.length ? " · v" + s.versions.length : "")));
      li.appendChild(box);
      li.onclick = () => { selected = s.id; renderTimeline(); renderDetail(); };
      ol.appendChild(li);
    });
    const cur = st.stages.find((s) => s.id === st.current_stage);
    $("deps").textContent = cur ? "مرحلهٔactive: " + cur.title +
      (cur.deps.length ? " · وابسته به: " + cur.deps.join("، ") : "") : "";
  }

  async function renderArtifact(art, container) {
    const box = el("div", "wf-art");
    const rel = art.path || "";
    const ext = rel.split(".").pop().toLowerCase();
    if (ext === "svg" || ext === "png" || ext === "jpg") {
      const img = el("img"); img.src = artifactUrl(rel);
      img.alt = "Artifact " + rel;
      img.onclick = () => { $("lightboxImg").src = img.src; $("lightbox").hidden = false; };
      box.appendChild(img);
    } else if (ext === "wav" || ext === "mp3" || ext === "ogg") {
      const a = el("audio"); a.controls = true; a.src = artifactUrl(rel);
      box.appendChild(a);
    } else {
      try {
        const r = await fetch(artifactUrl(rel));
        const txt = (await r.text()).slice(0, 6000);
        box.appendChild(el("pre", "", txt));
      } catch (e) { box.appendChild(el("pre", "", "خواندن Artifact ممکن نشد")); }
      const link = el("a", "", "open file ↗"); link.href = artifactUrl(rel);
      link.target = "_blank"; box.appendChild(link);
    }
    box.appendChild(el("div", "wf-sha", (art.kind || "") + " · " + rel + " · sha256 " +
      (art.sha256 || "—")));
    container.appendChild(box);
  }

  async function renderDetail() {
    const s = st.stages.find((x) => x.id === selected);
    if (!s) return;
    $("detailCard").hidden = false;
    $("stageTitle").textContent = s.title;
    $("stageInfo").textContent = "نقش: " + s.role + " · وضعیت: " + label(s.status) +
      (s.error ? " · خطا: " + s.error : "") +
      (s.note ? " · الزامات اصلاح کاربر: " + s.note.slice(0, 500) : "");
    const wrap = $("artifacts"); wrap.innerHTML = "";
    if (!s.versions.length) wrap.appendChild(el("div", "wf-meta", "هنوز Artifactای ثبت نشده است."));
    for (const v of s.versions) {
      const card = el("div", "wf-version");
      card.appendChild(el("h3", "", "نسخه " + v.version + " — " + v.at +
        (v.invalidated ? " (باطل شده)" : "")));
      for (const a of v.artifacts || []) await renderArtifact(a, card);
      wrap.appendChild(card);
    }
    renderQuestion(s);
    renderActions(s);
    const h = $("historyBox"); h.innerHTML = "";
    st.history.slice(-12).reverse().forEach((x) =>
      h.appendChild(el("div", "", x.at + " · " + x.action +
        (x.stage ? " · " + x.stage : "") + (x.detail ? " · " + x.detail : ""))));
  }

  function renderQuestion(s) {
    const box = $("questionBox"); box.innerHTML = "";
    const q = s.question;
    if (!q || st.status !== "AWAITING_USER_ANSWER" || q.answered) {
      box.hidden = true; return;
    }
    box.hidden = false;
    box.appendChild(el("h3", "", q.text));
    let chosen = null;
    (q.options || []).forEach((opt) => {
      const b = el("button", "", opt);
      b.onclick = () => { chosen = opt;
        box.querySelectorAll("button.opt").forEach((x) => x.classList.remove("sel"));
        b.classList.add("sel"); };
      b.className = "opt"; box.appendChild(b);
    });
    let other = null;
    if (q.allow_other) {
      other = el("input"); other.placeholder = "سایر / توضیح دلخواه";
      box.appendChild(other);
    }
    const send = el("button", "wf-primary", "ثبت پاسخ");
    send.onclick = async () => {
      const val = chosen || (other && other.value.trim()) || "";
      if (!val) { err("یک گزینه انتخاب کنید"); return; }
      try { await post("/api/workflow/answer", { project, stage: s.id,
        answer: val, question_id: q.id }); err(""); await loadState(); }
      catch (e) { err(e.message); }
    };
    box.appendChild(send);
  }

  function renderActions(s) {
    const bar = $("actions"); bar.innerHTML = "";
    if (st.status === "RUNNING" && s.id === st.current_stage &&
        ["PENDING", "REVISION"].includes(s.status)) {
      const run = el("button", "approve", "اجرای این مرحله");
      run.onclick = async () => {
        try { await post("/api/workflow/run", { project, stage: s.id,
          token: "ui-" + crypto.randomUUID() }); err(""); await loadState(); }
        catch (e) { err(e.message); }
      };
      bar.appendChild(run);
    }
    const note = el("textarea"); note.placeholder = "توضیح رد یا اصلاح (اختیاری)";
    bar.appendChild(note);
    if (st.status === "AWAITING_USER_REVIEW" && s.id === st.current_stage) {
      const ok = el("button", "approve", "تأیید و ادامه");
      ok.onclick = async () => {
        try { await post("/api/workflow/approve", { project, stage: s.id,
          version: s.versions.length, approval_id: "ap-" + crypto.randomUUID() });
          err(""); await loadState(); }
        catch (e) { err(e.message); }
      };
      bar.appendChild(ok);
      const no = el("button", "reject", "رد خروجی");
      no.onclick = async () => {
        try { await post("/api/workflow/reject", { project, stage: s.id,
          note: note.value }); err(""); await loadState(); }
        catch (e) { err(e.message); }
      };
      bar.appendChild(no);
      const rev = el("button", "", "درخواست اصلاح");
      rev.onclick = async () => {
        try { await post("/api/workflow/revise", { project, stage: s.id,
          note: note.value }); err(""); await loadState(); }
        catch (e) { err(e.message); }
      };
      bar.appendChild(rev);
    }
    if (st.status === "REJECTED" && s.id === st.current_stage) {
      const rev = el("button", "approve", "اصلاح و تولید نسخهٔ جدید");
      rev.onclick = async () => {
        try { await post("/api/workflow/revise", { project, stage: s.id,
          note: note.value }); err(""); await loadState(); }
        catch (e) { err(e.message); }
      };
      bar.appendChild(rev);
    }
  }

  $("btnStart").onclick = async () => {
    project = $("projectSel").value;
    if (!project) { err("پروژهای انتخاب کنید"); return; }
    try {
      await post("/api/workflow/start", { project,
        product_type: $("typeSel").value, goal: $("goalInput").value });
      err(""); history.replaceState(null, "", "/workflow.html?project=" + enc(project));
      watch(); await loadState();
    } catch (e) { err(e.message); }
  };
  $("projectSel").onchange = () => {
    project = $("projectSel").value; selected = null;
    history.replaceState(null, "", project ? "/workflow.html?project=" + enc(project)
                                           : "/workflow.html");
    if (project) { watch(); loadState().catch((e) => err(e.message)); }
    else apply(null);
  };
  $("closeLightbox").onclick = () => { $("lightbox").hidden = true; };

  loadProjects().then(() => {
    if (project) { watch(); return loadState().catch((e) => err(e.message)); }
  }).catch((e) => err(e.message));
})();
