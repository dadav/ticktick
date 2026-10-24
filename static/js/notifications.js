// Start reminder and milestone settings card. Depends on showToast from timer.js.
const notifyElements = {
    unconfigured: document.getElementById('notify-unconfigured'),
    enabled: document.getElementById('notify-enabled'),
    minWork: document.getElementById('notify-min-work'),
    dailyTarget: document.getElementById('notify-daily-target'),
    dailyMax: document.getElementById('notify-daily-max'),
    windowLabel: document.getElementById('notify-window'),
    pausedUntil: document.getElementById('notify-paused-until'),
    clearPause: document.getElementById('notify-clear-pause'),
    status: document.getElementById('notify-status'),
    test: document.getElementById('notify-test'),
};

const notifyErrorMessages = {
    'Gotify is not configured': 'Gotify ist nicht eingerichtet.',
    'Gotify request failed': 'Gotify ist nicht erreichbar oder hat die Nachricht abgelehnt.',
};

async function readNotifyError(response) {
    const body = await response.json().catch(() => ({}));
    if (typeof body.detail === 'string' && Object.hasOwn(notifyErrorMessages, body.detail)) {
        return notifyErrorMessages[body.detail];
    }
    console.error('Unbekannter API-Fehler', { status: response.status, detail: body.detail });
    return 'Die Anfrage konnte nicht verarbeitet werden. Bitte erneut versuchen.';
}

function formatGermanDate(isoDate) {
    const [year, month, day] = isoDate.split('-');
    return `${day}.${month}.${year}`;
}

function anyMilestoneEnabled(settings) {
    return settings.notify_min_work || settings.notify_daily_target || settings.notify_daily_max;
}

function describeNotifySettings(settings) {
    if (!settings.configured) return 'Inaktiv: Gotify nicht eingerichtet.';
    if (!settings.enabled && !anyMilestoneEnabled(settings)) return 'Aus.';
    if (settings.paused_until) return `Pausiert bis einschließlich ${formatGermanDate(settings.paused_until)}.`;
    if (!settings.enabled) return 'Aktiv. Nur Arbeitszeit-Benachrichtigungen, keine Start-Erinnerung.';
    if (settings.today_holiday) return `Aktiv. Heute keine Erinnerung (Feiertag: ${settings.today_holiday}).`;
    if (!settings.today_is_workday) return 'Aktiv. Heute keine Erinnerung (kein Arbeitstag).';
    return `Aktiv. Erinnerung zwischen ${settings.reminder_time} und ${settings.reminder_cutoff} Uhr, falls noch nicht gestartet.`;
}

function renderNotifySettings(settings) {
    const disabled = !settings.configured;
    notifyElements.unconfigured.hidden = settings.configured;
    notifyElements.enabled.checked = settings.enabled;
    notifyElements.minWork.checked = settings.notify_min_work;
    notifyElements.dailyTarget.checked = settings.notify_daily_target;
    notifyElements.dailyMax.checked = settings.notify_daily_max;
    notifyElements.windowLabel.textContent = settings.reminder_time;
    notifyElements.pausedUntil.value = settings.paused_until || '';
    notifyElements.enabled.disabled = disabled;
    notifyElements.minWork.disabled = disabled;
    notifyElements.dailyTarget.disabled = disabled;
    notifyElements.dailyMax.disabled = disabled;
    notifyElements.pausedUntil.disabled = disabled || !(settings.enabled || anyMilestoneEnabled(settings));
    notifyElements.clearPause.disabled = disabled || !settings.paused_until;
    notifyElements.test.disabled = disabled;
    notifyElements.status.textContent = describeNotifySettings(settings);
}

async function loadNotifySettings() {
    try {
        const response = await fetch('/api/notifications');
        if (!response.ok) {
            showToast(await readNotifyError(response));
            return;
        }
        renderNotifySettings(await response.json());
    } catch (error) {
        console.error('Fehler beim Laden der Benachrichtigungen:', error);
        showToast('Server nicht erreichbar');
    }
}

async function saveNotifySettings(pausedUntil) {
    const payload = {
        enabled: notifyElements.enabled.checked,
        paused_until: pausedUntil || null,
        notify_min_work: notifyElements.minWork.checked,
        notify_daily_target: notifyElements.dailyTarget.checked,
        notify_daily_max: notifyElements.dailyMax.checked,
    };
    try {
        const response = await fetch('/api/notifications', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            showToast(await readNotifyError(response));
            await loadNotifySettings();
            return;
        }
        renderNotifySettings(await response.json());
    } catch (error) {
        console.error('Fehler beim Speichern der Benachrichtigungen:', error);
        showToast('Server nicht erreichbar');
    }
}

async function sendNotifyTest() {
    notifyElements.test.disabled = true;
    try {
        const response = await fetch('/api/notifications/test', { method: 'POST' });
        if (!response.ok) {
            showToast(await readNotifyError(response));
            return;
        }
        showToast((await response.json()).message);
    } catch (error) {
        console.error('Fehler beim Senden der Testbenachrichtigung:', error);
        showToast('Server nicht erreichbar');
    } finally {
        notifyElements.test.disabled = false;
    }
}

for (const checkbox of [notifyElements.enabled, notifyElements.minWork, notifyElements.dailyTarget, notifyElements.dailyMax]) {
    checkbox.addEventListener('change', () => saveNotifySettings(notifyElements.pausedUntil.value));
}
notifyElements.pausedUntil.addEventListener('change', () => saveNotifySettings(notifyElements.pausedUntil.value));
notifyElements.clearPause.addEventListener('click', () => saveNotifySettings(null));
notifyElements.test.addEventListener('click', sendNotifyTest);

loadNotifySettings();
