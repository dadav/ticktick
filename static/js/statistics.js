async function deleteSession(sessionId) {
    if (!confirm('Moechtest du diesen Eintrag wirklich loeschen?')) {
        return;
    }

    try {
        const response = await fetch(`/api/sessions/${sessionId}`, {
            method: 'DELETE'
        });
        const data = await response.json();

        if (data.success) {
            // Remove the session item from the DOM
            const sessionItem = document.querySelector(`[data-session-id="${sessionId}"]`);
            if (sessionItem) {
                sessionItem.style.transition = 'opacity 0.3s';
                sessionItem.style.opacity = '0';
                setTimeout(() => {
                    sessionItem.remove();
                    checkEmptyList();
                }, 300);
            }
            // Refresh statistics after successful deletion
            await refreshStatistics();
        } else {
            alert('Fehler: ' + data.message);
        }
    } catch (error) {
        console.error('Fehler beim Loeschen:', error);
        alert('Fehler beim Loeschen des Eintrags.');
    }
}

async function refreshStatistics() {
    try {
        const response = await fetch('/api/statistics/summary');
        const stats = await response.json();

        // Update weekly statistics
        const weekTotal = document.getElementById('week-total');
        if (weekTotal) {
            weekTotal.textContent = `${stats.this_week.total_formatted} / ${stats.this_week.target_formatted}`;
        }

        const weekProgress = document.getElementById('week-progress');
        if (weekProgress) {
            const progress = stats.this_week.target_seconds > 0
                ? (stats.this_week.total_seconds / stats.this_week.target_seconds * 100)
                : 0;
            weekProgress.style.width = `${Math.min(progress, 100)}%`;
            weekProgress.classList.remove('overtime', 'behind');
            if (progress >= 100) {
                weekProgress.classList.add('overtime');
            } else if (progress < 80) {
                weekProgress.classList.add('behind');
            }
        }

        const weekDays = document.getElementById('week-days');
        if (weekDays) {
            weekDays.textContent = stats.this_week.days_worked;
        }

        const weekAvg = document.getElementById('week-avg');
        if (weekAvg) {
            weekAvg.textContent = stats.this_week.avg_per_day_formatted;
        }

        const weekOvertimeRow = document.getElementById('week-overtime-row');
        const weekOvertimeLabel = document.getElementById('week-overtime-label');
        const weekOvertime = document.getElementById('week-overtime');
        if (weekOvertimeRow && weekOvertimeLabel && weekOvertime) {
            weekOvertimeRow.classList.remove('positive', 'negative');
            if (stats.this_week.overtime_seconds >= 0) {
                weekOvertimeRow.classList.add('positive');
                weekOvertimeLabel.textContent = 'Ueberstunden:';
            } else {
                weekOvertimeRow.classList.add('negative');
                weekOvertimeLabel.textContent = 'Fehlstunden:';
            }
            weekOvertime.textContent = stats.this_week.overtime_formatted;
        }

        // Update weekly average times
        const weekAverageStart = document.getElementById('week-average-start');
        if (weekAverageStart) {
            weekAverageStart.textContent = stats.this_week.average_start_time || '--:--';
        }

        const weekAverageEnd = document.getElementById('week-average-end');
        if (weekAverageEnd) {
            weekAverageEnd.textContent = stats.this_week.average_end_time || '--:--';
        }

        // Update monthly statistics
        const monthTotal = document.getElementById('month-total');
        if (monthTotal) {
            monthTotal.textContent = stats.this_month.total_formatted;
        }

        const monthDays = document.getElementById('month-days');
        if (monthDays) {
            monthDays.textContent = stats.this_month.days_worked;
        }

        const monthAvg = document.getElementById('month-avg');
        if (monthAvg) {
            monthAvg.textContent = stats.this_month.avg_per_day_formatted;
        }

        // Update monthly overtime
        const monthOvertimeRow = document.getElementById('month-overtime-row');
        const monthOvertimeLabel = document.getElementById('month-overtime-label');
        const monthOvertime = document.getElementById('month-overtime');
        if (monthOvertimeRow && monthOvertimeLabel && monthOvertime) {
            monthOvertimeRow.classList.remove('positive', 'negative');
            if (stats.this_month.overtime_seconds >= 0) {
                monthOvertimeRow.classList.add('positive');
                monthOvertimeLabel.textContent = 'Ueberstunden:';
            } else {
                monthOvertimeRow.classList.add('negative');
                monthOvertimeLabel.textContent = 'Fehlstunden:';
            }
            monthOvertime.textContent = stats.this_month.overtime_formatted;
        }

        // Update monthly average times
        const monthAverageStart = document.getElementById('month-average-start');
        if (monthAverageStart) {
            monthAverageStart.textContent = stats.this_month.average_start_time || '--:--';
        }

        const monthAverageEnd = document.getElementById('month-average-end');
        if (monthAverageEnd) {
            monthAverageEnd.textContent = stats.this_month.average_end_time || '--:--';
        }

    } catch (error) {
        console.error('Fehler beim Aktualisieren der Statistiken:', error);
    }
}

function checkEmptyList() {
    const sessionsList = document.querySelector('.sessions-list');
    const remainingSessions = sessionsList.querySelectorAll('.session-item');

    if (remainingSessions.length === 0) {
        sessionsList.innerHTML = '<p style="color: #888; text-align: center; padding: 1rem;">Noch keine abgeschlossenen Eintraege.</p>';
    }
}
