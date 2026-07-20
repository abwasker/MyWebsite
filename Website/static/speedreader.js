(function() {
  const STORAGE_KEY = "speedReader.settings.v1";
  let words = [];
  let index = 0;
  let playing = false;
  let timer = null;
  let mode = "spritz";
  let wpm = 400;

  const textInput = document.getElementById("textInput");
  const loadBtn   = document.getElementById("loadBtn");
  const wpmSlider = document.getElementById("wpm");
  const wpmValue  = document.getElementById("wpmValue");
  const stage     = document.getElementById("stage");
  const playBtn   = document.getElementById("playBtn");
  const restartBtn= document.getElementById("restartBtn");
  const back10Btn = document.getElementById("back10Btn");
  const fwd10Btn  = document.getElementById("fwd10Btn");
  const progress  = document.getElementById("progress");
  const progressFill = document.getElementById("progressFill");
  const positionEl   = document.getElementById("position");
  const statusEl     = document.getElementById("status");
  const wordCountEl  = document.getElementById("wordCount");
  const modeToggle   = document.getElementById("modeToggle");

  function loadSettings() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const s = JSON.parse(raw);
      if (typeof s.wpm === "number") wpm = clampWpm(s.wpm);
      if (s.mode === "spritz" || s.mode === "context") mode = s.mode;
    } catch (e) {}
  }
  function saveSettings() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ wpm, mode })); }
    catch (e) {}
  }
  function clampWpm(v) {
    v = Number(v) || 400;
    return Math.max(50, Math.min(1500, Math.round(v / 10) * 10));
  }
  function orpIndex(word) {
    const len = word.length;
    if (len <= 1) return 0;
    if (len <= 5) return 1;
    if (len <= 9) return 2;
    if (len <= 13) return 3;
    return 4;
  }
  function tokenize(text) {
    return text.replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
  }
  function delayMultiplier(word) {
    if (!word) return 1;
    // Length-based slowdown: gentle ramp that grows with word length.
    // 1-5 chars: 1.00x   (no slowdown)
    // 6 chars : 1.05x
    // 8 chars : 1.15x
    // 10 chars: 1.25x
    // 12 chars: 1.35x
    // 15 chars: 1.50x  (capped)
    const len = word.replace(/[^A-Za-z0-9'’-]/g, "").length || word.length;
    let mult = 1 + Math.max(0, len - 5) * 0.05;
    if (mult > 1.5) mult = 1.5;
    // Punctuation pauses stack on top of length slowdown.
    if (/[.!?]$/.test(word)) mult *= 2.0;        // sentence end
    else if (/[,;:]$/.test(word)) mult *= 1.4;   // phrase end
    return mult;
  }
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function render() {
    const word = words[index] || "";
    if (!word) {
      stage.innerHTML = '<div class="word-spritz"><span class="pre"></span><span class="orp">·</span><span class="post"></span></div>';
      updateProgress();
      return;
    }
    if (mode === "spritz") {
      const i = orpIndex(word);
      const pre  = word.slice(0, i);
      const orp  = word.charAt(i);
      const post = word.slice(i + 1);
      stage.innerHTML =
        '<div class="word-spritz">' +
          '<span class="pre">'  + escapeHtml(pre)  + '</span>' +
          '<span class="orp">'  + escapeHtml(orp)  + '</span>' +
          '<span class="post">' + escapeHtml(post) + '</span>' +
        '</div>';
    } else {
      const prev = words[index - 1] || "";
      const next = words[index + 1] || "";
      stage.innerHTML =
        '<div class="word-ctx">' +
          '<span class="side left">'  + escapeHtml(prev) + '</span>' +
          '<span class="center">'     + escapeHtml(word) + '</span>' +
          '<span class="side right">' + escapeHtml(next) + '</span>' +
        '</div>';
    }
    updateProgress();
  }
  function updateProgress() {
    const total = words.length || 0;
    const cur = total ? Math.min(index + 1, total) : 0;
    positionEl.textContent = cur + " / " + total;
    const pct = total ? (index / Math.max(total - 1, 1)) * 100 : 0;
    progressFill.style.width = pct + "%";
    statusEl.textContent = playing ? "Playing" : (total ? "Paused" : "Ready");
    statusEl.classList.toggle("playing", playing);
    wordCountEl.textContent = total + " word" + (total === 1 ? "" : "s") + " loaded";
  }
  function updateSliderFill() {
    const pct = ((wpm - 50) / (1500 - 50)) * 100;
    wpmSlider.style.setProperty("--fill", pct + "%");
  }
  function scheduleNext() {
    clearTimeout(timer);
    if (!playing) return;
    if (index >= words.length) { stop(); return; }
    const baseMs = 60000 / wpm;
    const ms = baseMs * delayMultiplier(words[index]);
    timer = setTimeout(() => {
      index++;
      if (index >= words.length) {
        index = words.length - 1;
        render();
        stop();
        return;
      }
      render();
      scheduleNext();
    }, ms);
  }
  function play() {
    if (!words.length) return;
    if (index >= words.length - 1) index = 0;
    playing = true;
    playBtn.textContent = "⏸ Pause";
    render();
    scheduleNext();
  }
  function pause() {
    playing = false;
    clearTimeout(timer);
    playBtn.textContent = "▶ Play";
    updateProgress();
  }
  function stop() { pause(); }
  function togglePlay() { playing ? pause() : play(); }
  function restart() { pause(); index = 0; render(); }
  function skip(n) {
    if (!words.length) return;
    index = Math.max(0, Math.min(words.length - 1, index + n));
    render();
  }
  function loadText() {
    const text = textInput.value.trim();
    if (!text) { words = []; index = 0; render(); return; }
    words = tokenize(text);
    index = 0;
    pause();
    render();
  }
  function setMode(m) {
    mode = m;
    [...modeToggle.querySelectorAll("button")].forEach(b => {
      b.classList.toggle("active", b.dataset.mode === mode);
    });
    saveSettings();
    render();
  }

  loadBtn.addEventListener("click", loadText);
  textInput.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") loadText();
  });
  wpmSlider.addEventListener("input", (e) => {
    wpm = clampWpm(e.target.value);
    wpmValue.textContent = wpm;
    updateSliderFill();
    saveSettings();
    if (playing) scheduleNext();
  });
  modeToggle.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mode]");
    if (!btn) return;
    setMode(btn.dataset.mode);
  });
  playBtn.addEventListener("click", togglePlay);
  restartBtn.addEventListener("click", restart);
  back10Btn.addEventListener("click", () => skip(-10));
  fwd10Btn.addEventListener("click",  () => skip(10));
  progress.addEventListener("click", (e) => {
    if (!words.length) return;
    const rect = progress.getBoundingClientRect();
    const pct = (e.clientX - rect.left) / rect.width;
    index = Math.max(0, Math.min(words.length - 1, Math.round(pct * (words.length - 1))));
    render();
  });
  document.addEventListener("keydown", (e) => {
    if (document.activeElement === textInput) return;
    if (e.key === " ") { e.preventDefault(); togglePlay(); }
    else if (e.key === "r" || e.key === "R") { restart(); }
    else if (e.key === "ArrowLeft")  { skip(-10); }
    else if (e.key === "ArrowRight") { skip(10); }
    else if (e.key === "ArrowUp") {
      e.preventDefault();
      wpm = clampWpm(wpm + 25);
      wpmSlider.value = wpm; wpmValue.textContent = wpm;
      updateSliderFill(); saveSettings();
      if (playing) scheduleNext();
    }
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      wpm = clampWpm(wpm - 25);
      wpmSlider.value = wpm; wpmValue.textContent = wpm;
      updateSliderFill(); saveSettings();
      if (playing) scheduleNext();
    }
  });

  loadSettings();
  wpmSlider.value = wpm;
  wpmValue.textContent = wpm;
  updateSliderFill();
  setMode(mode);

  textInput.value =
    "Welcome to your speed reader. Paste any text in here, click Load text, " +
    "and then press Play. Use the dial to set words per minute. Try Spritz " +
    "mode for the red focus letter that keeps your eyes still, or switch to " +
    "Word and Context mode to see the previous and next words faded on either " +
    "side. Most readers feel comfortable around three hundred words per minute, " +
    "but with practice you can push past five hundred without losing comprehension.";
  loadText();
})();
