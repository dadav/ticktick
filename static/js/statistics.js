// The API answers PUT/POST/DELETE errors with an HTTP error code and a
// {"detail": "..."} body, so read the detail before touching the DOM.
async function readErrorDetail(response) {
  const body = await response.json().catch(() => ({}));
  // FastAPI request validation reports detail as a list of error objects.
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg).join(", ");
  }
  return body.detail || response.statusText;
}

async function deleteSession(sessionId) {
  if (!confirm("Moechtest du diesen Eintrag wirklich loeschen?")) {
    return;
  }

  try {
    const response = await fetch(`/api/sessions/${sessionId}`, {
      method: "DELETE",
    });

    if (!response.ok) {
      alert("Fehler: " + (await readErrorDetail(response)));
      return;
    }

    await refreshStatistics();
  } catch (error) {
    console.error("Fehler beim Loeschen:", error);
    alert("Fehler beim Loeschen des Eintrags.");
  }
}

async function refreshStatistics() {
  try {
    const response = await fetch("/api/statistics/summary");
    if (!response.ok) throw new Error(`Status ${response.status}`);
    const stats = await response.json();
    renderRecentDays(stats.recent_days);

    // Update weekly statistics
    const weekTotal = document.getElementById("week-total");
    if (weekTotal) {
      weekTotal.textContent = stats.this_week.total_formatted;
    }

    const weekDays = document.getElementById("week-days");
    if (weekDays) {
      weekDays.textContent = stats.this_week.days_worked;
    }

    const weekAvg = document.getElementById("week-avg");
    if (weekAvg) {
      weekAvg.textContent = stats.this_week.avg_per_day_formatted;
    }

    const weekOvertimeRow = document.getElementById("week-overtime-row");
    const weekOvertimeLabel = document.getElementById("week-overtime-label");
    const weekOvertime = document.getElementById("week-overtime");
    if (weekOvertimeRow && weekOvertimeLabel && weekOvertime) {
      weekOvertimeRow.classList.remove("positive", "negative");
      if (stats.this_week.overtime_seconds >= 0) {
        weekOvertimeRow.classList.add("positive");
      } else {
        weekOvertimeRow.classList.add("negative");
      }
      weekOvertimeLabel.textContent = "Überstunden:";
      weekOvertime.textContent = stats.this_week.overtime_formatted;
    }

    // Update weekly average times
    const weekAverageStart = document.getElementById("week-average-start");
    if (weekAverageStart) {
      weekAverageStart.textContent =
        stats.this_week.average_start_time || "--:--";
    }

    const weekAverageEnd = document.getElementById("week-average-end");
    if (weekAverageEnd) {
      weekAverageEnd.textContent = stats.this_week.average_end_time || "--:--";
    }

    // Update monthly statistics
    const monthTotal = document.getElementById("month-total");
    if (monthTotal) {
      monthTotal.textContent = stats.this_month.total_formatted;
    }

    const monthDays = document.getElementById("month-days");
    if (monthDays) {
      monthDays.textContent = stats.this_month.days_worked;
    }

    const monthAvg = document.getElementById("month-avg");
    if (monthAvg) {
      monthAvg.textContent = stats.this_month.avg_per_day_formatted;
    }

    // Update monthly overtime
    const monthOvertimeRow = document.getElementById("month-overtime-row");
    const monthOvertimeLabel = document.getElementById("month-overtime-label");
    const monthOvertime = document.getElementById("month-overtime");
    if (monthOvertimeRow && monthOvertimeLabel && monthOvertime) {
      monthOvertimeRow.classList.remove("positive", "negative");
      if (stats.this_month.overtime_seconds >= 0) {
        monthOvertimeRow.classList.add("positive");
      } else {
        monthOvertimeRow.classList.add("negative");
      }
      monthOvertimeLabel.textContent = "Überstunden:";
      monthOvertime.textContent = stats.this_month.overtime_formatted;
    }

    // Update monthly average times
    const monthAverageStart = document.getElementById("month-average-start");
    if (monthAverageStart) {
      monthAverageStart.textContent =
        stats.this_month.average_start_time || "--:--";
    }

    const monthAverageEnd = document.getElementById("month-average-end");
    if (monthAverageEnd) {
      monthAverageEnd.textContent =
        stats.this_month.average_end_time || "--:--";
    }
  } catch (error) {
    console.error("Fehler beim Aktualisieren der Statistiken:", error);
  }
}

function textElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function renderRecentDays(days) {
  const list = document.querySelector('.sessions-list');
  list.replaceChildren();
  if (days.length === 0) {
    list.append(textElement('p', 'summary-note', 'Noch keine abgeschlossenen Einträge.'));
    return;
  }
  for (const { day, sessions } of days) {
    const group = document.createElement('section');
    group.className = 'workday';
    group.dataset.day = day.date;
    const header = document.createElement('div');
    header.className = 'workday-header';
    header.append(textElement('h3', 'workday-date', day.date));
    header.append(textElement('span', 'workday-total', `${day.credited_work_formatted} angerechnet`));
    header.append(textElement('span', `workday-overtime ${day.overtime_seconds >= 0 ? 'positive' : 'negative'}`, `${day.overtime_formatted} Überstunden`));
    group.append(header);
    group.append(textElement('p', 'summary-note', `Erfasst: ${day.actual_work_formatted} · Pausen inkl. Unterbrechungen: ${day.total_pause_formatted} · Automatischer Abzug: ${day.lunch_deduction_formatted}`));
    if (day.cap_exceeded) group.append(textElement('p', 'day-notice', 'Tagesmaximum überschritten'));
    sessions.forEach((session, index) => {
      const row = document.createElement('div');
      row.className = 'session-item';
      row.dataset.sessionId = session.id;
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'session-open';
      open.setAttribute('aria-label', `Eintrag ${index + 1} vom ${day.date} bearbeiten`);
      open.append(textElement('span', 'date', `Eintrag ${index + 1}`));
      open.append(textElement('span', 'times', `${session.start_time} - ${session.end_time || '--:--'}`));
      open.append(textElement('span', 'duration', session.net_work_formatted));
      open.addEventListener('click', () => showSessionDetails(session.id));
      const remove = textElement('button', 'btn-delete', '✕');
      remove.type = 'button';
      remove.title = 'Eintrag löschen';
      remove.setAttribute('aria-label', 'Eintrag löschen');
      remove.addEventListener('click', () => deleteSession(session.id));
      row.append(open, remove);
      group.append(row);
    });
    list.append(group);
  }
}

const initialStatistics = document.getElementById('initial-statistics');
if (initialStatistics) renderRecentDays(JSON.parse(initialStatistics.textContent).recent_days);

// Modal functions for session details
async function showSessionDetails(sessionId) {
  const modal = document.getElementById("session-modal");
  const modalBody = document.getElementById("modal-body");

  // Show loading state
  modalBody.innerHTML =
    '<p style="text-align: center; padding: 1rem;">Laden...</p>';
  modal.classList.add("open");
  document.body.style.overflow = "hidden";

  try {
    const response = await fetch(`/api/sessions/${sessionId}`);
    if (!response.ok) {
      throw new Error("Session nicht gefunden");
    }
    const session = await response.json();
    modalBody.innerHTML = renderSessionDetails(session);
  } catch (error) {
    console.error("Fehler beim Laden der Session-Details:", error);
    modalBody.innerHTML =
      '<p style="color: var(--color-danger); text-align: center; padding: 1rem;">Fehler beim Laden der Details.</p>';
  }
}

function renderSessionDetails(session) {
  const overtimeClass = session.overtime_seconds >= 0 ? "positive" : "negative";
  const overtimeLabel = "Überstunden des gesamten Tages";
  const isCompleted = session.status === "completed";

  let html = `
        <div class="detail-section">
            <div class="detail-row">
                <span class="label">Datum:</span>
                <span class="value">${session.date}</span>
            </div>
            <div class="detail-row">
                <span class="label">Startzeit:</span>
                <span class="value" id="detail-start-time">${session.start_time}</span>
            </div>
            <div class="detail-row">
                <span class="label">Endzeit:</span>
                <span class="value" id="detail-end-time">${session.end_time || "--:--"}</span>
            </div>
            <div class="detail-row">
                <span class="label">Bruttoarbeitszeit:</span>
                <span class="value">${session.gross_work_formatted}</span>
            </div>
            <div class="detail-row">
                <span class="label">Pausenzeit (gesamt):</span>
                <span class="value">${session.total_pause_formatted}</span>
            </div>
            <div class="detail-row">
                <span class="label">Erfasste Arbeitszeit:</span>
                <span class="value">${session.net_work_formatted}</span>
            </div>
            <div class="detail-row ${overtimeClass}">
                <span class="label">${overtimeLabel}:</span>
                <span class="value">${session.overtime_formatted}</span>
            </div>
    `;

  if (isCompleted) {
    html += `
            <div class="detail-row" style="margin-top: 0.5rem;">
                <span></span>
                <button class="btn-edit" onclick="editSession(${session.id}, '${session.start_time}', '${session.end_time || ""}')">&#9998; Bearbeiten</button>
            </div>
    `;
  }

  html += `</div>`;

  if (session.pauses && session.pauses.length > 0) {
    html += `
            <div class="detail-section">
                <h4>Pausen (${session.pause_count})</h4>
                <div class="pause-list">
        `;

    session.pauses.forEach((pause, index) => {
      html += `
                <div class="pause-item">
                    <span class="pause-number">${index + 1}.</span>
                    <span class="pause-times">${pause.pause_start} - ${pause.pause_end || "--:--"}</span>
                    <span class="pause-duration">${pause.duration_formatted}</span>
                </div>
            `;
    });

    html += `
                </div>
            </div>
        `;
  } else {
    html += `
            <div class="detail-section">
                <h4>Pausen</h4>
                <p style="color: var(--color-text-muted); font-size: 0.9rem;">Keine Pausen aufgezeichnet.</p>
            </div>
        `;
  }

  return html;
}

function editSession(sessionId, currentStart, currentEnd) {
  const startEl = document.getElementById("detail-start-time");
  const endEl = document.getElementById("detail-end-time");

  startEl.innerHTML = `<input type="text" id="edit-start-time" value="${currentStart}" pattern="[0-2][0-9]:[0-5][0-9]" placeholder="HH:MM" maxlength="5" />`;
  endEl.innerHTML = `<input type="text" id="edit-end-time" value="${currentEnd}" pattern="[0-2][0-9]:[0-5][0-9]" placeholder="HH:MM" maxlength="5" />`;

  // Replace edit button with save/cancel
  const editRow = startEl.closest(".detail-section").querySelector(".detail-row:last-child");
  editRow.innerHTML = `
    <span></span>
    <span class="edit-actions">
      <button class="btn-edit btn-save" onclick="saveSessionEdit(${sessionId})">Speichern</button>
      <button class="btn-edit btn-cancel" onclick="cancelEdit(${sessionId})">Abbrechen</button>
    </span>
  `;
}

async function saveSessionEdit(sessionId) {
  const startInput = document.getElementById("edit-start-time");
  const endInput = document.getElementById("edit-end-time");

  const body = {};
  if (startInput && startInput.value) body.start_time = startInput.value;
  if (endInput && endInput.value) body.end_time = endInput.value;

  try {
    const response = await fetch(`/api/sessions/${sessionId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      alert("Fehler: " + (await readErrorDetail(response)));
      return;
    }

    // Re-fetch and re-render the details
    await showSessionDetails(sessionId);
    await refreshStatistics();
  } catch (error) {
    console.error("Fehler beim Speichern:", error);
    alert("Fehler beim Speichern der Aenderungen.");
  }
}

async function cancelEdit(sessionId) {
  await showSessionDetails(sessionId);
}

function showAddSessionForm() {
  const modal = document.getElementById("session-modal");
  const modalBody = document.getElementById("modal-body");
  const today = new Date().toISOString().slice(0, 10);

  modalBody.innerHTML = `
    <div class="detail-section">
      <div class="detail-row">
        <span class="label">Datum:</span>
        <span class="value"><input type="date" id="add-date" value="${today}" max="${today}" /></span>
      </div>
      <div class="detail-row">
        <span class="label">Startzeit:</span>
        <span class="value"><input type="time" id="add-start" value="08:00" /></span>
      </div>
      <div class="detail-row">
        <span class="label">Endzeit:</span>
        <span class="value"><input type="time" id="add-end" value="16:30" /></span>
      </div>
      <div class="detail-row" style="margin-top: 0.5rem;">
        <span></span>
        <span class="edit-actions">
          <button class="btn-edit btn-save" onclick="saveNewSession()">Speichern</button>
          <button class="btn-edit btn-cancel" onclick="closeModal()">Abbrechen</button>
        </span>
      </div>
    </div>
  `;

  modal.classList.add("open");
  document.body.style.overflow = "hidden";
}

async function saveNewSession() {
  const body = {
    date: document.getElementById("add-date").value,
    start_time: document.getElementById("add-start").value,
    end_time: document.getElementById("add-end").value,
  };

  try {
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      alert("Fehler: " + (await readErrorDetail(response)));
      return;
    }

    closeModal();
    await refreshStatistics();
  } catch (error) {
    console.error("Fehler beim Anlegen:", error);
    alert("Fehler beim Anlegen des Eintrags.");
  }
}

function closeModal() {
  const modal = document.getElementById("session-modal");
  modal.classList.remove("open");
  document.body.style.overflow = "";
}

function closeModalOnBackdrop(event) {
  if (event.target.id === "session-modal") {
    closeModal();
  }
}

// Close modal on Escape key
document.addEventListener("keydown", function (event) {
  if (event.key === "Escape") {
    closeModal();
  }
});
