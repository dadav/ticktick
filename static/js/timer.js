const elements = {
    currentTime: document.getElementById('current-time'),
    statusText: document.getElementById('status-text'),
    btnStart: document.getElementById('btn-start'),
    btnPause: document.getElementById('btn-pause'),
    btnContinue: document.getElementById('btn-continue'),
    btnStop: document.getElementById('btn-stop'),
    btnReset: document.getElementById('btn-reset'),
    summary: document.getElementById('summary'),
    startTime: document.getElementById('start-time'),
    netTime: document.getElementById('net-time'),
    pauseInfo: document.getElementById('pause-info'),
    overtime: document.getElementById('overtime'),
    overtimeRow: document.getElementById('overtime-row'),
    earliestLeave: document.getElementById('earliest-leave'),
    normalLeave: document.getElementById('normal-leave'),
    latestLeave: document.getElementById('latest-leave'),
    remaining: document.getElementById('remaining'),
    remainingMax: document.getElementById('remaining-max'),
    caption: document.getElementById('timer-caption'),
    summaryTitle: document.getElementById('summary-title'),
    notice: document.getElementById('day-notice'),
    sessionTime: document.getElementById('session-time'),
    sessionTimeRow: document.getElementById('session-time-row'),
    lunchDeduction: document.getElementById('lunch-deduction'),
    forecastRows: document.querySelectorAll('.forecast-row'),
    forecastNote: document.getElementById('forecast-note'),
};

const statusLabels = {
    idle: 'Bereit',
    running: 'Läuft',
    paused: 'Pausiert'
};

function formatDuration(seconds) {
    const h = Math.floor(Math.abs(seconds) / 3600);
    const m = Math.floor((Math.abs(seconds) % 3600) / 60);
    const s = Math.abs(seconds) % 60;
    const sign = seconds < 0 ? '-' : '';
    return `${sign}${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatTime(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', hour12: false });
}

function updateUI(data) {
    const { status, session, calculations, day, can_start, start_blocked_reason } = data;
    elements.statusText.textContent = statusLabels[status] || status;
    elements.statusText.className = 'status ' + status;
    elements.btnStart.disabled = !can_start;
    elements.btnStart.title = start_blocked_reason || '';
    elements.btnPause.disabled = status !== 'running';
    elements.btnContinue.disabled = status !== 'paused';
    elements.btnStop.disabled = status === 'idle';
    elements.btnReset.disabled = status === 'idle';
    elements.currentTime.textContent = day ? day.credited_work_formatted : '00:00:00';
    elements.summary.style.display = day ? 'block' : 'none';
    elements.sessionTimeRow.hidden = !session;
    elements.forecastRows.forEach(row => { row.hidden = !session; });
    elements.forecastNote.hidden = status !== 'paused';
    elements.caption.textContent = day ? `Anrechenbare Arbeitszeit · ${day.date}` : 'Anrechenbare Arbeitszeit heute';

    let notice = start_blocked_reason || '';
    if (day && day.cap_exceeded) {
        notice = 'Tagesmaximum überschritten. Aufgezeichnete Zeiten bleiben erhalten.';
    } else if (day && day.target_reached && !notice) {
        notice = 'Tagessoll erreicht.';
    }
    elements.notice.textContent = notice;
    elements.notice.hidden = !notice;
    if (!day) return;

    elements.summaryTitle.textContent = `Arbeitszeit am ${day.date} · ${day.session_count} ${day.session_count === 1 ? 'Eintrag' : 'Einträge'}`;
    elements.startTime.textContent = formatTime(day.start_time);
    elements.netTime.textContent = day.actual_work_formatted;
    elements.sessionTime.textContent = session ? session.net_work_formatted : '00:00:00';
    elements.pauseInfo.textContent = `${day.pause_count} ${day.pause_count === 1 ? 'Pause' : 'Pausen'} (${day.total_pause_formatted})`;
    elements.lunchDeduction.textContent = day.lunch_deduction_formatted;
    elements.overtime.textContent = day.overtime_formatted;
    elements.overtimeRow.classList.toggle('positive', day.overtime_seconds >= 0);
    elements.overtimeRow.classList.toggle('negative', day.overtime_seconds < 0);
    elements.remaining.textContent = day.remaining_for_daily;
    elements.remainingMax.textContent = day.remaining_for_max;
    if (calculations) {
        elements.earliestLeave.textContent = calculations.earliest_reached ? 'Erreicht' : (calculations.earliest_leave ? calculations.earliest_leave + ' Uhr' : '--:--');
        elements.normalLeave.textContent = day.target_reached ? 'Erreicht' : (calculations.normal_leave ? calculations.normal_leave + ' Uhr' : '--:--');
        elements.latestLeave.textContent = calculations.latest_leave ? calculations.latest_leave + ' Uhr' : '--:--';
    }
}

function showToast(message) {
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

// Only toast once per outage instead of every failed poll.
let offline = false;

async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) throw new Error(`Status ${response.status}`);
        const data = await response.json();
        if (data.auto_stopped) {
            const maxDailyLabel = (window.TICKTICK_CONFIG && window.TICKTICK_CONFIG.maxDailyLabel) || '10 Std.';
            alert(`Maximale tägliche Arbeitszeit (${maxDailyLabel}) erreicht. Timer wurde automatisch gestoppt.`);
        }
        offline = false;
        updateUI(data);
    } catch (error) {
        console.error('Fehler beim Abrufen des Status:', error);
        if (!offline) {
            offline = true;
            showToast('Server nicht erreichbar');
        }
    }
}

async function sendAction(action) {
    try {
        const response = await fetch(`/api/${action}`, { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            if (action !== 'stop' && data.status === 'idle') showToast(data.message);
            await fetchStatus();
        } else {
            console.error('Aktion fehlgeschlagen:', data.message);
            showToast(data.message);
            await fetchStatus();
        }
    } catch (error) {
        console.error('Fehler beim Senden der Aktion:', error);
        showToast('Server nicht erreichbar');
    }
}

// Button event listeners
elements.btnStart.addEventListener('click', () => sendAction('start'));
elements.btnPause.addEventListener('click', () => sendAction('pause'));
elements.btnContinue.addEventListener('click', () => sendAction('continue'));
elements.btnStop.addEventListener('click', () => sendAction('stop'));
elements.btnReset.addEventListener('click', () => {
    if (confirm('Möchtest du wirklich verwerfen? Der aktuelle Eintrag wird nicht gespeichert.')) {
        sendAction('reset');
    }
});

// Initial fetch and polling
fetchStatus();
setInterval(fetchStatus, 1000);
