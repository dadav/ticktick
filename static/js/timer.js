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
    lunchInfo: document.getElementById('lunch-info'),
    overtime: document.getElementById('overtime'),
    overtimeRow: document.getElementById('overtime-row'),
    earliestLeave: document.getElementById('earliest-leave'),
    normalLeave: document.getElementById('normal-leave'),
    latestLeave: document.getElementById('latest-leave'),
    remaining: document.getElementById('remaining'),
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
    const { status, session, calculations } = data;

    // Update status
    elements.statusText.textContent = statusLabels[status] || status;
    elements.statusText.className = 'status ' + status;

    // Update buttons
    elements.btnStart.disabled = status !== 'idle';
    elements.btnPause.disabled = status !== 'running';
    elements.btnContinue.disabled = status !== 'paused';
    elements.btnStop.disabled = status === 'idle';
    elements.btnReset.disabled = status === 'idle';

    if (session) {
        elements.currentTime.textContent = session.net_work_formatted;
        elements.summary.style.display = 'block';
        elements.startTime.textContent = formatTime(session.start_time);
        elements.netTime.textContent = session.net_work_formatted;

        const pauseLabel = session.pause_count === 1 ? 'Pause' : 'Pausen';
        elements.pauseInfo.textContent = `${session.pause_count} ${pauseLabel} (${formatDuration(session.total_pause_seconds)})`;

        if (calculations) {
            if (calculations.lunch_break_applies) {
                elements.lunchInfo.textContent = 'Abgezogen (30 Min.)';
            } else if (calculations.lunch_break_at) {
                elements.lunchInfo.textContent = `Ab ${calculations.lunch_break_at} Uhr`;
            }

            // Update overtime display
            elements.overtime.textContent = calculations.overtime_formatted;
            if (calculations.overtime_seconds >= 0) {
                elements.overtimeRow.classList.add('positive');
                elements.overtimeRow.classList.remove('negative');
            } else {
                elements.overtimeRow.classList.add('negative');
                elements.overtimeRow.classList.remove('positive');
            }

            elements.earliestLeave.textContent = calculations.earliest_leave + ' Uhr';
            elements.normalLeave.textContent = calculations.normal_leave + ' Uhr';
            elements.latestLeave.textContent = calculations.latest_leave + ' Uhr';
            elements.remaining.textContent = calculations.remaining_for_daily;
        }
    } else {
        elements.currentTime.textContent = '00:00:00';
        elements.summary.style.display = 'none';
    }
}

async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        updateUI(data);
    } catch (error) {
        console.error('Fehler beim Abrufen des Status:', error);
    }
}

async function sendAction(action) {
    try {
        const response = await fetch(`/api/${action}`, { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            fetchStatus();
        } else {
            console.error('Aktion fehlgeschlagen:', data.message);
        }
    } catch (error) {
        console.error('Fehler beim Senden der Aktion:', error);
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
