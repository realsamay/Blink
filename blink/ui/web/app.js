// ==============================================================================
// BLINK WEB CLIENT — APPLE FACE ID STATE MACHINE & HUD TELEMETRY
// ==============================================================================

const TOTAL_TICKS = 64;
const POSES = [
  { id: "center", title: "Look straight at the camera", sub: "Keep your face level in the circle" },
  { id: "left", title: "Turn your head slightly LEFT", sub: "A gentle turn towards your left" },
  { id: "right", title: "Turn your head slightly RIGHT", sub: "A gentle turn towards your right" },
  { id: "up", title: "Tilt your head slightly UP", sub: "Tilt your chin up gently" },
  { id: "down", title: "Tilt your head slightly DOWN", sub: "Tilt your chin down gently" },
];

let activeTab = "tab-diagnostics";
let currentPoseIdx = 0;
let litTicksCount = 0;
let isEnrolling = false;

// ------------------------------------------------------------------------------
// Initialization & Tab Navigation
// ------------------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initApertureRing();
  initDynamicIsland();
  initSettingsControls();
  fetchStatus();
  setInterval(fetchTelemetry, 1000);
});

function initTabs() {
  const menuItems = document.querySelectorAll(".menu-item");
  const panes = document.querySelectorAll(".tab-pane");

  menuItems.forEach(item => {
    item.addEventListener("click", () => {
      const targetTab = item.dataset.tab;
      menuItems.forEach(m => m.classList.remove("active"));
      panes.forEach(p => p.classList.remove("active"));

      item.classList.add("active");
      const targetPane = document.getElementById(targetTab);
      if (targetPane) targetPane.classList.add("active");
      activeTab = targetTab;
    });
  });
}

// ------------------------------------------------------------------------------
// 64-Tick Apple Face ID Circular Aperture Generator
// ------------------------------------------------------------------------------
function initApertureRing() {
  const svg = document.getElementById("apertureRingSvg");
  if (!svg) return;
  svg.innerHTML = "";

  const cx = 160;
  const cy = 160;
  const rInner = 115;
  const rOuter = 132;

  for (let i = 0; i < TOTAL_TICKS; i++) {
    const angle = (i / TOTAL_TICKS) * (2 * Math.PI);
    const x1 = cx + rInner * Math.cos(angle);
    const y1 = cy + rInner * Math.sin(angle);
    const x2 = cx + rOuter * Math.cos(angle);
    const y2 = cy + rOuter * Math.sin(angle);

    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", x1);
    line.setAttribute("y1", y1);
    line.setAttribute("x2", x2);
    line.setAttribute("y2", y2);
    line.setAttribute("stroke", "rgba(255, 255, 255, 0.15)");
    line.setAttribute("stroke-width", "3");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("id", `tick-${i}`);
    line.style.transition = "stroke 0.25s ease, stroke-width 0.25s ease";

    svg.appendChild(line);
  }
}

function updateApertureProgress(litCount) {
  for (let i = 0; i < TOTAL_TICKS; i++) {
    const tick = document.getElementById(`tick-${i}`);
    if (!tick) continue;
    if (i < litCount) {
      tick.setAttribute("stroke", "#34c759");
      tick.setAttribute("stroke-width", "4");
    } else {
      tick.setAttribute("stroke", "rgba(255, 255, 255, 0.15)");
      tick.setAttribute("stroke-width", "3");
    }
  }
}

// ------------------------------------------------------------------------------
// Dynamic Island Lockscreen Card Simulator
// ------------------------------------------------------------------------------
function initDynamicIsland() {
  const btnTest = document.getElementById("btnTestNotch");
  const overlay = document.getElementById("dynamicIslandOverlay");
  const video = document.getElementById("islandVideo");
  const glyph = document.getElementById("islandGlyph");
  const statusText = document.getElementById("islandStatusText");

  if (!btnTest || !overlay) return;

  btnTest.addEventListener("click", () => {
    // Reveal Dynamic Island
    overlay.classList.add("active");
    statusText.textContent = "Face ID";

    // Attempt video playback
    video.src = "/idleanimation.mp4";
    video.play().then(() => {
      glyph.style.display = "none";
    }).catch(() => {
      // Procedural glyph fallback
      glyph.style.display = "flex";
    });

    // Simulate scanning -> success cycle
    setTimeout(() => {
      statusText.textContent = "Unlocked";
      statusText.style.color = "#34c759";
      video.src = "/unlockanimation.mp4";
      video.play().catch(() => {});

      setTimeout(() => {
        // Dismiss smoothly
        overlay.classList.remove("active");
        setTimeout(() => {
          statusText.textContent = "Face ID";
          statusText.style.color = "#ffffff";
        }, 500);
      }, 1200);
    }, 1800);
  });
}

// ------------------------------------------------------------------------------
// Settings Controls & Synchronization
// ------------------------------------------------------------------------------
function initSettingsControls() {
  const slider = document.getElementById("thresholdSlider");
  const badge = document.getElementById("thresholdVal");
  const toggle = document.getElementById("toggleFaceUnlock");
  const timeoutSelect = document.getElementById("timeoutSelect");
  const segBtns = document.querySelectorAll(".seg-btn");

  if (slider && badge) {
    slider.addEventListener("input", (e) => {
      badge.textContent = `${e.target.value}%`;
    });
    slider.addEventListener("change", (e) => {
      saveSettings({ match_threshold: parseFloat(e.target.value) / 100.0 });
    });
  }

  if (toggle) {
    toggle.addEventListener("change", (e) => {
      saveSettings({ is_face_unlock_enabled: e.target.checked });
    });
  }

  if (timeoutSelect) {
    timeoutSelect.addEventListener("change", (e) => {
      saveSettings({ face_detection_seconds: parseInt(e.target.value) });
    });
  }

  segBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      segBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      saveSettings({ liveness_mode: btn.dataset.val });
    });
  });

  // Password set button
  const btnPwd = document.getElementById("btnSetPassword");
  if (btnPwd) {
    btnPwd.addEventListener("click", () => {
      const pwd = prompt("Enter your desktop unlock password:");
      if (pwd) {
        fetch("/api/password", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pwd })
        }).then(r => r.json()).then(res => {
          if (res.ok) alert("Password securely encrypted with AES-256-GCM.");
        });
      }
    });
  }

  // Enrollment modal triggers
  const btnOpen = document.getElementById("btnOpenEnroll");
  const btnClose = document.getElementById("btnCloseEnroll");
  const modal = document.getElementById("enrollModal");

  if (btnOpen && modal) {
    btnOpen.addEventListener("click", () => {
      modal.classList.remove("hidden");
      startEnrollmentFlow();
    });
  }

  if (btnClose && modal) {
    btnClose.addEventListener("click", () => {
      modal.classList.add("hidden");
      isEnrolling = false;
    });
  }
}

function saveSettings(partial) {
  fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(partial)
  }).then(r => r.json()).then(data => {
    console.log("[Settings] Updated:", data);
  });
}

function fetchStatus() {
  fetch("/api/status")
    .then(r => r.json())
    .then(status => {
      const toggle = document.getElementById("toggleFaceUnlock");
      if (toggle) toggle.checked = status.is_face_unlock_enabled;

      const slider = document.getElementById("thresholdSlider");
      const badge = document.getElementById("thresholdVal");
      if (slider && badge) {
        const pct = Math.round(status.match_threshold * 100);
        slider.value = pct;
        badge.textContent = `${pct}%`;
      }

      renderProfiles(status.identities || []);
    })
    .catch(err => console.error("Error fetching status:", err));
}

function fetchTelemetry() {
  fetch("/api/telemetry")
    .then(r => r.json())
    .then(tel => {
      const elLatency = document.getElementById("valLatency");
      if (elLatency) elLatency.textContent = `${tel.latency_ms.toFixed(1)} ms`;

      const elFps = document.getElementById("valFramerate");
      if (elFps) elFps.textContent = `${tel.fps} FPS`;

      const elLive = document.getElementById("valLiveness");
      if (elLive) elLive.textContent = `${tel.liveness_status} (${tel.liveness_score.toFixed(2)})`;

      const elPose = document.getElementById("hudPose");
      if (elPose && tel.pose) {
        elPose.textContent = `Yaw: ${tel.pose.yaw.toFixed(1)}° • Pitch: ${tel.pose.pitch.toFixed(1)}° • Roll: ${tel.pose.roll.toFixed(1)}°`;
      }
    })
    .catch(() => {});
}

function renderProfiles(identities) {
  const container = document.getElementById("profilesList");
  const countEl = document.getElementById("profileCount");
  if (!container) return;

  if (countEl) countEl.textContent = identities.length;
  container.innerHTML = "";

  if (identities.length === 0) {
    container.innerHTML = `<div style="text-align:center;padding:24px;color:rgba(255,255,255,0.4);font-size:13px;">No facial identities enrolled yet. Click "Enroll New Face" above.</div>`;
    return;
  }

  identities.forEach(ident => {
    const item = document.createElement("div");
    item.className = "profile-item";
    const initial = (ident.name || "U")[0].toUpperCase();
    const sampleCount = (ident.samples || []).length;

    item.innerHTML = `
      <div class="profile-meta">
        <div class="profile-avatar">${initial}</div>
        <div>
          <div class="profile-name">${escapeHtml(ident.name)}</div>
          <div class="profile-details">${sampleCount} poses enrolled • Hardware AES-GCM</div>
        </div>
      </div>
      <button class="btn-delete" title="Delete Profile" data-id="${ident.id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
      </button>
    `;

    item.querySelector(".btn-delete").addEventListener("click", () => {
      if (confirm(`Delete Face ID profile for "${ident.name}"?`)) {
        deleteProfile(ident.id);
      }
    });

    container.appendChild(item);
  });
}

function deleteProfile(id) {
  fetch("/api/identities/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id })
  }).then(r => r.json()).then(res => {
    if (res.ok) fetchStatus();
  });
}

// ------------------------------------------------------------------------------
// Apple Face ID 5-Pose Circular Enrollment Flow
// ------------------------------------------------------------------------------
function startEnrollmentFlow() {
  currentPoseIdx = 0;
  litTicksCount = 0;
  isEnrolling = true;
  updateApertureProgress(0);

  const saveBtn = document.getElementById("btnSaveEnrollment");
  if (saveBtn) saveBtn.disabled = true;

  advancePose(0);
}

function advancePose(idx) {
  if (idx >= POSES.length) {
    completeAllPoses();
    return;
  }

  currentPoseIdx = idx;
  const pose = POSES[idx];
  const instrTitle = document.getElementById("poseInstruction");
  if (instrTitle) instrTitle.textContent = `${pose.title} (${pose.sub})`;

  // Update badges
  POSES.forEach((p, i) => {
    const badge = document.getElementById(`badge-${p.id}`);
    if (!badge) return;
    badge.classList.remove("active", "completed");
    if (i < idx) badge.classList.add("completed");
    else if (i === idx) badge.classList.add("active");
  });

  // Smoothly increment circular aperture ticks
  const targetTicks = Math.round(((idx + 1) / POSES.length) * TOTAL_TICKS);
  animateTicksTo(targetTicks, () => {
    // Hold for 1.2s then advance to next pose
    setTimeout(() => {
      if (isEnrolling) advancePose(idx + 1);
    }, 1200);
  });
}

function animateTicksTo(target, onDone) {
  const step = () => {
    if (litTicksCount < target) {
      litTicksCount++;
      updateApertureProgress(litTicksCount);
      setTimeout(step, 20);
    } else {
      if (onDone) onDone();
    }
  };
  step();
}

function completeAllPoses() {
  const instrTitle = document.getElementById("poseInstruction");
  if (instrTitle) instrTitle.textContent = "All poses registered successfully!";

  POSES.forEach(p => {
    const badge = document.getElementById(`badge-${p.id}`);
    if (badge) badge.classList.add("completed");
  });

  const celebration = document.getElementById("enrollTickCelebration");
  if (celebration) celebration.classList.remove("hidden");

  const saveBtn = document.getElementById("btnSaveEnrollment");
  if (saveBtn) saveBtn.disabled = false;

  saveBtn.onclick = () => {
    const nameInput = document.getElementById("enrollProfileName");
    const name = (nameInput && nameInput.value.trim()) || "My Face";

    fetch("/api/enroll/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name })
    }).then(r => r.json()).then(res => {
      if (res.ok) {
        document.getElementById("enrollModal").classList.add("hidden");
        if (celebration) celebration.classList.add("hidden");
        fetchStatus();
      }
    });
  };
}

function escapeHtml(text) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' };
  return (text || "").replace(/[&<>"']/g, m => map[m]);
}
