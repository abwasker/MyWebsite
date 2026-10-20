/* Interval Ladder — ported from the standalone artifact (scope Phase 2).
 *
 * WHAT CHANGED FROM THE ARTIFACT, and why:
 *
 * 1. 🔴 The Claude artifact runtime is GONE. The original wrote progress to
 *    `claude.use("db")` — Anthropic's storage, keyed to a Claude account. That
 *    does not exist here. Progress now goes to this site's own endpoints.
 *
 * 2. 🔴 Progress is saved ONLY for a signed-in user (scope §2.5). Anonymous
 *    visitors practise with statistics held in memory, which reset on refresh.
 *    ⚠️ The page must NOT invite them to sign in — public signup is closed, so
 *    for almost every visitor that is a dead end (scope §2.1).
 *
 * 3. 🔴 `localStorage` is NOT used. Settings go to `sessionStorage` instead, so
 *    a stray refresh mid-practice does not reset the naming system, but nothing
 *    durable is left on the visitor's device. Progress never touches browser
 *    storage at all.
 *
 * 4. **Attempts are queued and batched**, each carrying a `client_id` UUID. The
 *    batch stays in the queue until the server confirms, so a lost response just
 *    retries — the server's unique constraint on (user, client_id) absorbs the
 *    duplicate. That is the designed path, not an error case.
 *
 * 5. **"Sing it" statistics are kept SEPARATE** from the clicked modes (scope
 *    §6.3). Clicked accuracy is observed; sung accuracy is self-reported and
 *    will drift optimistic. One combined figure would be quietly part
 *    self-assessment.
 *
 * 6. ⚠️ The ison drone still keeps sounding until switched off. That is
 *    intended (scope §2.6) — do NOT add a visibilitychange/pagehide handler.
 *
 * The music data, tuning maths and audio synthesis are ported UNCHANGED. They
 * were verified correct during scoping (§2.3) and a tidying rewrite is pure
 * downside risk.
 */
(function () {
"use strict";

const CFG = JSON.parse(document.getElementById("il-config").textContent);

// ---------- Music data (verbatim from the artifact — see §2.3) ----------
const DEG = [
  { sol: "Do", num: "1", byz: "Ni", gr: "Νη" },
  { sol: "Re", num: "2", byz: "Pa", gr: "Πα" },
  { sol: "Mi", num: "3", byz: "Vou", gr: "Βου" },
  { sol: "Fa", num: "4", byz: "Ga", gr: "Γα" },
  { sol: "Sol", num: "5", byz: "Di", gr: "Δι" },
  { sol: "La", num: "6", byz: "Ke", gr: "Κε" },
  { sol: "Ti", num: "7", byz: "Zo", gr: "Ζω" },
  { sol: "Do′", num: "1′", byz: "Ni′", gr: "Νη′" },
];
const WEST = [0, 200, 400, 500, 700, 900, 1100, 1200];
const MORIA = [0, 12, 22, 30, 42, 54, 64, 72];
const centsOf = (d, t) => (t === "byz" ? (MORIA[d] * 1200) / 72 : WEST[d]);
const SIZE = ["Unison", "2nd", "3rd", "4th", "5th", "6th", "7th", "Octave"];
const WNAME = {
  "0:0": "unison", "1:1": "minor 2nd", "1:2": "major 2nd", "2:3": "minor 3rd",
  "2:4": "major 3rd", "3:5": "perfect 4th", "3:6": "augmented 4th (tritone)",
  "4:6": "diminished 5th", "4:7": "perfect 5th", "5:8": "minor 6th",
  "5:9": "major 6th", "6:10": "minor 7th", "6:11": "major 7th", "7:12": "octave",
};
const G = {
  ison: "\u{1D046}", oligon: "\u{1D047}", kentima: "\u{1D04F}", ypsili: "\u{1D050}",
  apos: "\u{1D051}", elafron: "\u{1D055}", chamili: "\u{1D056}",
};
const NEUME = {
  0: [[G.ison], "Ison"],
  1: [[G.oligon], "Oligon"],
  2: [[G.oligon, G.kentima], "Oligon + kentima"],
  3: [[G.oligon, G.kentima], "Oligon + kentima"],
  4: [[G.oligon, G.ypsili], "Oligon + ypsili"],
  5: [[G.oligon, G.ypsili], "Oligon + ypsili"],
  6: [[G.oligon, G.ypsili], "Oligon + ypsili"],
  7: [[G.oligon, G.ypsili, G.kentima], "Oligon + ypsili + kentima"],
  "-1": [[G.apos], "Apostrophos"],
  "-2": [[G.elafron], "Elaphron"],
  "-3": [[G.elafron, G.apos], "Elaphron + apostrophos"],
  "-4": [[G.chamili], "Chamili"],
  "-5": [[G.chamili, G.apos], "Chamili + apostrophos"],
  "-6": [[G.chamili, G.elafron], "Chamili + elaphron"],
  "-7": [[G.chamili, G.elafron, G.apos], "Chamili + elaphron + apostrophos"],
};
const neumeHTML = (s) => `<span class="neume" aria-label="${NEUME[s][1]}">${NEUME[s][0].join("")}</span>`;
const westName = (a, b) => WNAME[Math.abs(b - a) + ":" + Math.abs(WEST[b] - WEST[a]) / 100] || "";
const moriaBetween = (a, b) => Math.abs(MORIA[b] - MORIA[a]);
const sizeWord = (s) => (s === 0 ? "Unison" : (s > 0 ? "up " : "down ") + (Math.abs(s) === 7 ? "an octave" : "a " + SIZE[Math.abs(s)]));
const stepsWord = (s) => (s === 0 ? "same note" : `${Math.abs(s)} step${Math.abs(s) > 1 ? "s" : ""} ${s > 0 ? "up" : "down"}`);
const PITCHES = [["G2", 43], ["A2", 45], ["C3", 48], ["D3", 50], ["E3", 52], ["F3", 53], ["G3", 55], ["A3", 57], ["C4", 60], ["D4", 62]];

// ---------- Settings: sessionStorage, NOT localStorage ----------
// ⚠️ Storage access THROWS outright in some privacy modes, so every read and
// write is guarded. A missing store degrades to defaults, never to an error.
const SS = {
  get(k, d) {
    try { const v = sessionStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; }
  },
  set(k, v) {
    try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* ignore */ }
  },
};
const S = Object.assign(
  { sys: "sol", tab: "notes", sizes: [1, 2, 3, 4, 5], dir: "up", tuning: "auto", midi: 48, play: "mel", focus: true },
  SS.get("il.settings", {})
);
const saveSettings = () => SS.set("il.settings", S);

// ---------- Statistics: in memory for everyone, server for the signed in ----------
const blankStats = () => ({ modes: { notes: {}, intervals: {}, sing: {} } });
let stats = blankStats();

let queue = [];          // attempts awaiting confirmation
let flushTimer = null;
let flushing = false;
let saveState = CFG.authenticated ? "idle" : "anonymous";

function newId() {
  try { return crypto.randomUUID(); } catch (e) { /* fall through */ }
  // Secure-context fallback. Only needs to be unique per user, not unguessable.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function record(mode, s, ok) {
  const m = stats.modes[mode] || (stats.modes[mode] = {});
  const k = String(s);
  const r = m[k] || (m[k] = { c: 0, n: 0 });
  r.n++;
  if (ok) r.c++;

  if (!CFG.authenticated) return;
  queue.push({ client_id: newId(), mode: mode, interval_steps: s, correct: !!ok });
  clearTimeout(flushTimer);
  flushTimer = setTimeout(flush, 1200);
  setStatus();
}

async function flush() {
  if (flushing || !queue.length || !CFG.authenticated) return;
  flushing = true;
  const batch = queue.slice(0, 200);
  try {
    const res = await fetch(CFG.attemptsUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrfToken },
      body: JSON.stringify({ attempts: batch }),
    });
    if (res.ok) {
      // 🔴 Only drop them once the server has them. A lost response leaves the
      // batch queued and it is re-sent; client_id makes that harmless.
      queue = queue.slice(batch.length);
      saveState = "saved";
    } else {
      saveState = res.status === 403 ? "signed-out" : "error";
    }
  } catch (e) {
    saveState = "offline";
  }
  flushing = false;
  setStatus();
  if (queue.length && saveState === "saved") { clearTimeout(flushTimer); flushTimer = setTimeout(flush, 1200); }
}

function setStatus() {
  const el = document.getElementById("saveStatus");
  if (!el) return;
  if (!CFG.authenticated) { el.textContent = "Practice here is not saved."; return; }
  const msgs = {
    idle: "Signed in — your practice is saved.",
    saved: "Saved to your account.",
    offline: "Offline — saves will retry.",
    error: "Could not save just now; it will retry.",
    "signed-out": "Your session ended. Sign in again to keep saving.",
  };
  el.textContent = (queue.length ? "Saving… " : "") + (msgs[saveState] || msgs.idle);
}

// ---------- Audio (verbatim) ----------
let ctx = null, master = null, drone = null;
function ac() {
  if (!ctx) {
    const C = window.AudioContext || window.webkitAudioContext;
    ctx = new C();
    master = ctx.createGain();
    master.gain.value = 0.55;
    master.connect(ctx.destination);
  }
  if (ctx.state === "suspended") ctx.resume();
  return ctx;
}
const baseHz = () => 440 * Math.pow(2, (S.midi - 69) / 12);
const freq = (d, t) => baseHz() * Math.pow(2, centsOf(d, t) / 1200);

function tone(f, t0, dur, vol) {
  const c = ac(), g = c.createGain(), lp = c.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = Math.min(f * 5, 9000);
  const o1 = c.createOscillator(); o1.type = "triangle"; o1.frequency.value = f;
  const o2 = c.createOscillator(); o2.type = "sine"; o2.frequency.value = f * 2;
  const g2 = c.createGain(); g2.gain.value = 0.22;
  o1.connect(lp); o2.connect(g2); g2.connect(lp); lp.connect(g); g.connect(master);
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(vol, t0 + 0.05);
  g.gain.linearRampToValueAtTime(vol * 0.75, t0 + 0.3);
  g.gain.setValueAtTime(vol * 0.75, t0 + dur - 0.15);
  g.gain.linearRampToValueAtTime(0, t0 + dur);
  o1.start(t0); o2.start(t0); o1.stop(t0 + dur + 0.05); o2.stop(t0 + dur + 0.05);
}
function playDegs(list, t, together) {
  const c = ac();
  let t0 = c.currentTime + 0.06;
  if (together) { list.forEach((d) => tone(freq(d, t), t0, 1.6, 0.28)); return; }
  list.forEach((d, i) => tone(freq(d, t), t0 + i * 0.85, 0.8, 0.36));
}
function setDrone(on) {
  // ⚠️ Intentionally has no visibilitychange/pagehide handler — an ison is a
  // held drone you sing against, and cutting it on tab blur would break the
  // practice it exists for (scope §2.6).
  if (drone) {
    const c = ac();
    drone.g.gain.linearRampToValueAtTime(0, c.currentTime + 0.3);
    const o = drone.o;
    setTimeout(() => { try { o.forEach((x) => x.stop()); } catch (e) {} }, 400);
    drone = null;
  }
  if (on) {
    const c = ac(), g = c.createGain(), lp = c.createBiquadFilter();
    lp.type = "lowpass"; lp.frequency.value = 700;
    const f = baseHz() / 2;
    const o1 = c.createOscillator(), o2 = c.createOscillator();
    o1.type = "sawtooth"; o2.type = "triangle";
    o1.frequency.value = f; o2.frequency.value = f * 1.002;
    o1.connect(lp); o2.connect(lp); lp.connect(g); g.connect(master);
    g.gain.setValueAtTime(0, c.currentTime);
    g.gain.linearRampToValueAtTime(0.07, c.currentTime + 0.6);
    o1.start(); o2.start();
    drone = { g, o: [o1, o2] };
  }
  document.getElementById("droneBtn").setAttribute("aria-pressed", String(!!on));
}

// ---------- Question state (verbatim) ----------
let Q = null, lastSys = null;
const effTuning = (sys) => (S.tuning === "auto" ? (sys === "byz" ? "byz" : "west") : S.tuning);
const lab = (d, sys) => DEG[d][sys];

function candidates() {
  const out = [];
  const sizes = S.sizes.length ? S.sizes : [1, 2];
  sizes.forEach((z) => {
    if (z === 0) { out.push(0); return; }
    if (S.dir !== "down") out.push(z);
    if (S.dir !== "up") out.push(-z);
  });
  return out;
}
function weightOf(mode, s) {
  if (!S.focus) return 1;
  const r = (stats.modes[mode] || {})[String(s)];
  const acc = r ? (r.c + 1) / (r.n + 2) : 0.5;
  return 1 + 3 * (1 - acc);
}
function newQuestion(silent) {
  const mode = S.tab, cs = candidates();
  const ws = cs.map((s) => weightOf(mode, s));
  let x = Math.random() * ws.reduce((a, b) => a + b, 0), s = cs[0];
  for (let i = 0; i < cs.length; i++) { x -= ws[i]; if (x <= 0) { s = cs[i]; break; } }
  const lo = s >= 0 ? 0 : -s, hi = s >= 0 ? 7 - s : 7;
  const a = lo + Math.floor(Math.random() * (hi - lo + 1));
  let sys = S.sys;
  if (sys === "rotate") {
    const opts = ["sol", "num", "byz"].filter((o) => o !== lastSys);
    sys = opts[Math.floor(Math.random() * opts.length)];
  }
  lastSys = sys;
  Q = { mode, a, b: a + s, s, sys, tuning: effTuning(sys), done: false, picked: null, revealed: false };
  renderPractice();
  if (silent === true) return;
  if (mode !== "sing") setTimeout(replay, 150); else playDegs([Q.a], Q.tuning, false);
}
function replay() {
  if (!Q) return;
  if (Q.mode === "sing" && !Q.revealed) { playDegs([Q.a], Q.tuning, false); return; }
  playDegs([Q.a, Q.b], Q.tuning, S.play === "harm" && Q.mode === "intervals");
}

// ---------- Ladder ----------
function renderLadder() {
  const el = document.getElementById("ladder");
  const sys = Q ? Q.sys : S.sys === "rotate" ? "byz" : S.sys;
  const t = Q ? Q.tuning : effTuning(sys);
  const W = 118, H = 420, P = 22, x0 = 34;
  const y = (d) => P + (1 - centsOf(d, t) / 1200) * (H - 2 * P);
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Scale ladder, ${t === "byz" ? "moria" : "equal temperament"} spacing">`;
  s += `<line x1="${x0}" y1="${y(7)}" x2="${x0}" y2="${y(0)}" stroke="var(--line)" stroke-width="2"/>`;
  for (let d = 0; d < 7; d++) {
    const gap = t === "byz" ? MORIA[d + 1] - MORIA[d] : WEST[d + 1] - WEST[d] === 200 ? "W" : "H";
    s += `<text x="${x0 - 12}" y="${(y(d) + y(d + 1)) / 2 + 4}" text-anchor="end" font-family="ui-monospace, Menlo, monospace" font-size="11" fill="var(--muted)">${gap}</text>`;
  }
  if (Q && (Q.done || Q.revealed) && Q.s !== 0) {
    const ya = y(Q.a), yb = y(Q.b), cx = x0 + (Math.abs(Q.s) > 2 ? 30 : 22);
    s += `<path d="M ${x0} ${ya} C ${cx} ${ya}, ${cx} ${yb}, ${x0 + 2} ${yb}" fill="none" stroke="var(--cinnabar)" stroke-width="2" stroke-dasharray="4 3"/>`;
  }
  for (let d = 0; d < 8; d++) {
    const yy = y(d);
    const isA = Q && d === Q.a;
    const isB = Q && (Q.done || Q.revealed) && d === Q.b;
    const isWrong = Q && Q.picked != null && Q.mode === "notes" && d === Q.picked && Q.picked !== Q.b;
    s += `<g class="rung" data-d="${d}" style="cursor:pointer"><line x1="${x0 - 8}" y1="${yy}" x2="${x0 + 8}" y2="${yy}" stroke="var(--ink)" stroke-width="1.5"/>`;
    s += `<circle cx="${x0}" cy="${yy}" r="7" fill="${isB ? "var(--lapis)" : "var(--surface)"}" stroke="${isWrong ? "var(--bad)" : isA || isB ? "var(--lapis)" : "var(--line)"}" stroke-width="${isA || isB || isWrong ? 2.5 : 1.5}"/>`;
    s += `<text x="${x0 + 16}" y="${yy + 5}" font-family="${sys === "byz" ? "Cormorant Garamond, Georgia, serif" : "Manrope, sans-serif"}" font-size="15" font-weight="${isA || isB ? 700 : 400}" fill="${isA || isB ? "var(--lapis)" : "var(--ink)"}">${lab(d, sys)}</text>`;
    if (sys === "byz") s += `<text x="${x0 + 16}" y="${yy + 17}" font-family="Cormorant Garamond, Georgia, serif" font-size="10" fill="var(--muted)">${DEG[d].gr}</text>`;
    s += `</g>`;
  }
  s += `</svg>`;
  el.innerHTML = s;
  el.querySelectorAll(".rung").forEach((g) => g.addEventListener("click", () => playDegs([+g.dataset.d], t, false)));
}

// ---------- Practice ----------
function sysName(sys) { return { sol: "solfège", num: "numbers", byz: "Byzantine names" }[sys]; }
function noteSpan(d, sys) {
  return `<span class="big">${lab(d, sys)}</span>${sys === "byz" ? ` <span class="gr" style="font-family:var(--f-display);color:var(--muted)">${DEG[d].gr}</span>` : ""}`;
}
function renderPractice() {
  const pr = document.getElementById("prompt"), an = document.getElementById("answers");
  const fb = document.getElementById("feedback"), nx = document.getElementById("nextBtn");
  if (!Q) { pr.innerHTML = ""; an.innerHTML = ""; return; }
  const sys = Q.sys, tun = Q.tuning === "byz" ? "moria tuning" : "equal temperament";
  const tag = `<p class="label">${S.sys === "rotate" ? "This round: " : ""}${sysName(sys)} · ${tun}</p>`;

  if (Q.mode === "notes") {
    pr.innerHTML = `${tag}<h3>Start on ${noteSpan(Q.a, sys)}. Which note comes next?</h3><p>Listen to both notes, then pick the second one.</p>`;
    an.innerHTML = `<div class="row">${DEG.map((g, d) => {
      let c = "";
      if (Q.done) { if (d === Q.b) c = " ok"; else if (d === Q.picked) c = " no"; }
      return `<button class="ans${c}" data-d="${d}" ${Q.done ? "disabled" : ""}><span class="main">${lab(d, sys)}</span>${sys === "byz" ? `<span class="gr">${g.gr}</span>` : `<span class="sub">${d + 1 > 7 ? "8" : d + 1}</span>`}</button>`;
    }).join("")}</div>`;
    an.querySelectorAll(".ans").forEach((b) => b.addEventListener("click", () => answer(+b.dataset.d)));
  } else if (Q.mode === "intervals") {
    pr.innerHTML = `${tag}<h3>How far apart are the two notes?</h3><p>${S.play === "harm" ? "Both notes sound together." : "The first note is " + noteSpan(Q.a, sys) + "."}</p>`;
    const cs = candidates();
    const ups = cs.filter((s) => s > 0).sort((a, b) => a - b);
    const downs = cs.filter((s) => s < 0).sort((a, b) => b - a);
    const uni = cs.filter((s) => s === 0);
    const btn = (s) => {
      let c = "";
      if (Q.done) { if (s === Q.s) c = " ok"; else if (s === Q.picked) c = " no"; }
      const inner = sys === "byz"
        ? `${neumeHTML(s)}<span class="sub">${s === 0 ? "0" : (s > 0 ? "+" : "−") + Math.abs(s)} step${Math.abs(s) === 1 ? "" : "s"}</span>`
        : `<span class="main">${s === 0 ? "Unison" : SIZE[Math.abs(s)]}</span><span class="sub">${s === 0 ? "same" : s > 0 ? "up" : "down"}</span>`;
      return `<button class="ans${c}" data-s="${s}" ${Q.done ? "disabled" : ""}>${inner}</button>`;
    };
    let h = "";
    if (uni.length) h += `<div class="row">${uni.map(btn).join("")}</div>`;
    if (ups.length) h += `${downs.length ? '<span class="rowlabel">Up</span>' : ""}<div class="row">${ups.map(btn).join("")}</div>`;
    if (downs.length) h += `${ups.length ? '<span class="rowlabel">Down</span>' : ""}<div class="row">${downs.map(btn).join("")}</div>`;
    an.innerHTML = h;
    an.querySelectorAll(".ans").forEach((b) => b.addEventListener("click", () => answer(+b.dataset.s)));
  } else {
    const how = sys === "byz"
      ? `${neumeHTML(Q.s)} <span style="font-size:.7em;color:var(--muted)">${NEUME[Q.s][1]} · ${stepsWord(Q.s)}</span>`
      : `<span class="big">${sizeWord(Q.s)}</span>`;
    pr.innerHTML = `${tag}<h3>From ${noteSpan(Q.a, sys)}, sing ${how}</h3><p>Hear the starting note, sing the target, then reveal it to check yourself.</p>`;
    an.innerHTML = Q.revealed
      ? Q.done ? "" : `<span class="rowlabel">Did you land on ${lab(Q.b, sys)}?</span><div class="selfgrade"><button class="btn" id="gotIt">Got it</button><button class="btn" id="missed">Missed it</button></div>`
      : `<div class="controls"><button class="btn primary" id="revealBtn">Reveal the target note</button></div>`;
    const rb = document.getElementById("revealBtn");
    if (rb) rb.addEventListener("click", () => {
      Q.revealed = true; renderPractice();
      playDegs([Q.b], Q.tuning, false);
      setTimeout(() => playDegs([Q.a, Q.b], Q.tuning, false), 1100);
    });
    const g = document.getElementById("gotIt"); if (g) g.addEventListener("click", () => answer(true));
    const m = document.getElementById("missed"); if (m) m.addEventListener("click", () => answer(false));
  }

  if (Q.done) {
    const ok = Q.correct, w = westName(Q.a, Q.b), mo = moriaBetween(Q.a, Q.b);
    const head = Q.mode === "sing" ? (ok ? "Nice." : "Logged as a miss.") : ok ? "Correct." : "Not quite.";
    fb.className = "feedback " + (ok ? "ok" : "no");
    fb.hidden = false;
    fb.innerHTML = `<strong>${head} ${lab(Q.a, sys)} → ${lab(Q.b, sys)}</strong>
      <span>${Q.s === 0 ? "Unison" : w.charAt(0).toUpperCase() + w.slice(1) + " " + (Q.s > 0 ? "up" : "down")} · ${stepsWord(Q.s)} · ${mo} moria ${neumeHTML(Q.s)}</span>
      <span class="cross">${DEG[Q.a].sol}→${DEG[Q.b].sol} · ${DEG[Q.a].num}→${DEG[Q.b].num} · ${DEG[Q.a].byz}→${DEG[Q.b].byz} (${DEG[Q.a].gr}→${DEG[Q.b].gr})</span>`;
    nx.hidden = false;
  } else { fb.hidden = true; nx.hidden = true; }
  renderLadder();
}

function answer(v) {
  if (!Q || Q.done) return;
  if (Q.mode === "notes") { Q.picked = v; Q.correct = v === Q.b; }
  else if (Q.mode === "intervals") { Q.picked = v; Q.correct = v === Q.s; }
  else { Q.correct = !!v; }
  Q.done = true;
  record(Q.mode, Q.s, Q.correct);
  renderPractice();
  if (Q.mode !== "sing" && !Q.correct) setTimeout(() => playDegs([Q.a, Q.b], Q.tuning, false), 250);
  document.getElementById("nextBtn").focus({ preventScroll: true });
}

// ---------- Chart ----------
function renderChart() {
  const v = document.getElementById("chartView");
  const rows = DEG.map((g, d) => {
    const step = d < 7 ? `${WEST[d + 1] - WEST[d] === 200 ? "W" : "H"} · ${MORIA[d + 1] - MORIA[d]}` : "—";
    return `<tr><td>${g.sol}</td><td>${g.num}</td><td>${g.byz}<span class="gr">${g.gr}</span></td><td class="mono">${WEST[d]}¢</td><td class="mono">${MORIA[d]} · ${((MORIA[d] * 1200) / 72).toFixed(1)}¢</td><td class="mono">${step}</td>
    <td><button class="play" data-d="${d}" data-t="west">▶ Western</button> <button class="play" data-d="${d}" data-t="byz">▶ Byzantine</button></td></tr>`;
  }).join("");
  let irows = "";
  for (let k = 0; k <= 7; k++) {
    irows += `<tr><td>${k === 0 ? "Unison" : SIZE[k]}</td><td class="mono">${k}</td><td>${neumeHTML(k) + " " + NEUME[k][1]}</td><td>${k === 0 ? "" : neumeHTML(-k) + " " + NEUME[-k][1]}</td></tr>`;
  }
  v.innerHTML = `<div class="card">
    <h3>Three names, one scale</h3>
    <p>With movable Do, Do is always the tonic, so Do = 1 = Ni in any key. The columns line up note for note. What differs is the tuning: Byzantine chant divides the octave into 72 moria, and the diatonic scale steps are 12, 10, 8, 12, 12, 10, 8.</p>
    <div class="tablewrap"><table>
      <thead><tr><th>Solfège</th><th>Number</th><th>Byzantine</th><th>Western</th><th>Moria from Ni</th><th>Next step (W/H · moria)</th><th>Listen</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
    <div class="note">Vou sits about 33 cents lower than Mi, and Zo about 33 cents lower than Ti. Ga and Di match Fa and Sol exactly. Compare them:
      <button class="play" id="cmpVou">Mi then Vou</button> <button class="play" id="cmpZo">Ti then Zo</button></div>
  </div>
  <div class="card">
    <h3>Intervals and their neumes</h3>
    <p>Byzantine notation writes the movement, not the pitch. Each neume says how many steps to go up or down from the note you are on.</p>
    <div class="tablewrap"><table>
      <thead><tr><th>Interval</th><th>Steps</th><th>Ascending</th><th>Descending</th></tr></thead>
      <tbody>${irows}</tbody></table></div>
    <div class="note">For larger leaps upward, the same parts (oligon with kentima or ypsili) are written in different positions to mean different step counts. This chart lists the parts only, so check your method book for exact placement.</div>
  </div>`;
  v.querySelectorAll("button.play[data-d]").forEach((b) => b.addEventListener("click", () => playDegs([+b.dataset.d], b.dataset.t, false)));
  document.getElementById("cmpVou").addEventListener("click", () => { const c = ac(), t = c.currentTime + 0.05; tone(freq(2, "west"), t, 0.9, 0.36); tone(freq(2, "byz"), t + 1, 0.9, 0.36); });
  document.getElementById("cmpZo").addEventListener("click", () => { const c = ac(), t = c.currentTime + 0.05; tone(freq(6, "west"), t, 0.9, 0.36); tone(freq(6, "byz"), t + 1, 0.9, 0.36); });
}

// ---------- Progress ----------
let progMode = "clicked";
let serverStats = null;

/* 🔴 "Sing it" is kept SEPARATE from the clicked modes (scope §6.3). Clicked
 * accuracy is observed; sung accuracy is self-reported and drifts optimistic.
 * A single blended figure would be quietly part self-assessment. */
const CLICKED = ["notes", "intervals"];

function statsSource() {
  // Signed in: the server holds every past session. Anonymous: this visit only.
  if (CFG.authenticated && serverStats) return serverStats;
  return stats.modes;
}
function aggregate(modes) {
  const src = statsSource();
  const agg = {};
  let C = 0, N = 0;
  modes.forEach((m) => Object.entries(src[m] || {}).forEach(([k, r]) => {
    const a = agg[k] || (agg[k] = { c: 0, n: 0 });
    a.c += r.c; a.n += r.n; C += r.c; N += r.n;
  }));
  return { agg, C, N };
}

async function renderProgress() {
  if (CFG.authenticated) {
    // Send anything pending first, so the page does not show stale totals.
    await flush();
    try {
      const res = await fetch(CFG.progressUrl, { headers: { "Accept": "application/json" } });
      if (res.ok) {
        const data = await res.json();
        const modes = { notes: {}, intervals: {}, sing: {} };
        data.by_interval.forEach((r) => {
          (modes[r.mode] || (modes[r.mode] = {}))[String(r.interval_steps)] = { c: r.c, n: r.n };
        });
        serverStats = modes;
      }
    } catch (e) { /* fall back to in-memory */ }
  }
  paintProgress();
}

function paintProgress() {
  const v = document.getElementById("progressView");
  const clicked = aggregate(CLICKED);
  const sung = aggregate(["sing"]);
  const shown = aggregate(progMode === "clicked" ? CLICKED : [progMode]);

  const keys = [];
  for (let s = 7; s >= -7; s--) if (shown.agg[String(s)]) keys.push(s);
  const bars = keys.map((s) => {
    const r = shown.agg[String(s)], p = Math.round((100 * r.c) / r.n);
    const col = p >= 80 ? "var(--good)" : p >= 55 ? "var(--lapis)" : "var(--bad)";
    return `<div class="bar"><span class="nm">${neumeHTML(s)}${s === 0 ? "Unison" : SIZE[Math.abs(s)] + " " + (s > 0 ? "up" : "down")}</span><span class="track"><span class="fill" style="display:block;width:${p}%;background:${col}"></span></span><span class="num">${p}% · ${r.c}/${r.n}</span></div>`;
  }).join("");

  const weak = keys.filter((s) => shown.agg[String(s)].n >= 4)
    .sort((x, y) => shown.agg[x].c / shown.agg[x].n - shown.agg[y].c / shown.agg[y].n).slice(0, 3);

  const pct = (g) => (g.N ? Math.round((100 * g.C) / g.N) + "%" : "—");
  const chips = [["clicked", "Clicked answers"], ["notes", "Hear the note"], ["intervals", "Name the interval"], ["sing", "Sing it"]];

  v.innerHTML = `<div class="card">
    <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;align-items:center"><h3>Your progress</h3>
      <div class="chips" id="progChips">${chips.map(([k, l]) => `<button data-m="${k}" aria-pressed="${progMode === k}">${l}</button>`).join("")}</div></div>

    <div class="stats">
      <div class="statgroup"><span class="gl">Clicked answers</span>
        <div class="stat"><div class="v">${pct(clicked)}</div><div class="k">${clicked.N} answered</div></div>
        <span class="gn">Graded by the app.</span></div>
      <div class="statgroup"><span class="gl">Sung · self-graded</span>
        <div class="stat"><div class="v">${pct(sung)}</div><div class="k">${sung.N} answered</div></div>
        <span class="gn">You marked these yourself, so they are a different kind of number — kept apart on purpose.</span></div>
    </div>

    ${weak.length ? `<p>Needs the most work: <strong>${weak.map((s) => (s === 0 ? "unison" : SIZE[Math.abs(s)] + " " + (s > 0 ? "up" : "down"))).join(", ")}</strong>. With “Favor weak intervals” on, these come up more often.</p>` : ""}

    <h4>Accuracy by interval</h4>
    ${bars ? `<div class="bars">${bars}</div>` : `<p class="hint">No answers yet. Each question you answer in the practice tabs shows up here, grouped by interval and direction.</p>`}
    <p class="status" id="saveStatus"></p>
  </div>`;
  setStatus();
  v.querySelectorAll("#progChips button").forEach((b) => b.addEventListener("click", () => { progMode = b.dataset.m; paintProgress(); }));
}

// ---------- Settings UI ----------
function syncControls() {
  document.querySelectorAll("#sysSeg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sys === S.sys)));
  document.querySelectorAll("#tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === S.tab)));
  document.querySelectorAll("#sizeChips button").forEach((b) => b.setAttribute("aria-pressed", String(S.sizes.includes(+b.dataset.z))));
  document.querySelectorAll("#dirChips button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.dir === S.dir)));
  document.querySelectorAll("#playChips button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.play === S.play)));
  document.getElementById("focusBtn").setAttribute("aria-pressed", String(S.focus));
  document.getElementById("tuningSel").value = S.tuning;
  document.getElementById("pitchSel").value = String(S.midi);
  const practice = ["notes", "intervals", "sing"].includes(S.tab);
  document.getElementById("practiceView").hidden = !practice;
  document.getElementById("settingsBox").hidden = !practice;
  document.getElementById("chartView").hidden = S.tab !== "chart";
  document.getElementById("progressView").hidden = S.tab !== "progress";
  document.getElementById("keys").hidden = S.tab === "sing";
}

document.getElementById("sizeChips").innerHTML = [0, 1, 2, 3, 4, 5, 6, 7].map((z) => `<button data-z="${z}">${z === 0 ? "Unison" : SIZE[z]}</button>`).join("");
document.getElementById("pitchSel").innerHTML = PITCHES.map(([n, m]) => `<option value="${m}">${n}</option>`).join("");

function changed(fresh) {
  saveSettings();
  syncControls();
  if (fresh && ["notes", "intervals", "sing"].includes(S.tab)) newQuestion();
}
document.getElementById("sysSeg").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; S.sys = b.dataset.sys; lastSys = null; changed(true); });
document.getElementById("tabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  S.tab = b.dataset.tab; saveSettings(); syncControls();
  if (S.tab === "chart") renderChart();
  else if (S.tab === "progress") renderProgress();
  else newQuestion();
});
document.getElementById("sizeChips").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const z = +b.dataset.z;
  if (S.sizes.includes(z)) { if (S.sizes.length > 1) S.sizes = S.sizes.filter((x) => x !== z); }
  else S.sizes = [...S.sizes, z].sort((a, b) => a - b);
  changed(true);
});
document.getElementById("dirChips").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; S.dir = b.dataset.dir; changed(true); });
document.getElementById("playChips").addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; S.play = b.dataset.play; changed(false); });
document.getElementById("focusBtn").addEventListener("click", () => { S.focus = !S.focus; changed(false); });
document.getElementById("tuningSel").addEventListener("change", (e) => { S.tuning = e.target.value; if (Q) Q.tuning = effTuning(Q.sys); changed(false); renderLadder(); });
document.getElementById("pitchSel").addEventListener("change", (e) => { S.midi = +e.target.value; const on = !!drone; changed(false); if (on) setDrone(true); });
document.getElementById("replayBtn").addEventListener("click", replay);
document.getElementById("tonicBtn").addEventListener("click", () => playDegs([0], Q ? Q.tuning : "west", false));
document.getElementById("droneBtn").addEventListener("click", () => setDrone(!drone));
document.getElementById("nextBtn").addEventListener("click", () => newQuestion());

document.addEventListener("keydown", (e) => {
  if (!["notes", "intervals", "sing"].includes(S.tab)) return;
  const tg = e.target.tagName;
  if (tg === "SELECT" || tg === "INPUT") return;
  if (e.key === " " && tg !== "BUTTON") { e.preventDefault(); replay(); }
  else if (e.key === "Enter" && Q && Q.done && tg !== "BUTTON") { e.preventDefault(); newQuestion(); }
  else if (Q && !Q.done && Q.mode === "notes" && /^[1-8]$/.test(e.key)) { answer(+e.key - 1); }
});

// A last chance to persist a pending batch when the page goes away.
// 🔴 `fetch` with keepalive, NOT navigator.sendBeacon: a beacon cannot set
// request headers, so it could not send X-CSRFToken and Django would reject it
// — silently, from the page's point of view, which is the worst outcome.
// ⚠️ This is about SAVING. It is not a drone handler; the drone keeps sounding
// until switched off, by design (§2.6).
window.addEventListener("pagehide", () => {
  if (!CFG.authenticated || !queue.length) return;
  try {
    fetch(CFG.attemptsUrl, {
      method: "POST",
      keepalive: true,
      headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrfToken },
      body: JSON.stringify({ attempts: queue.slice(0, 200) }),
    });
  } catch (e) { /* nothing more to try */ }
});

// ---------- Boot ----------
syncControls();
if (S.tab === "chart") renderChart();
else if (S.tab === "progress") renderProgress();
else newQuestion(true); // no autoplay: audio needs a click
})();
