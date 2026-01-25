/**
 * Theme toggle functionality
 * Supports: dark, light, and system preference
 * Default: follows system preference
 */

const STORAGE_KEY = 'ticktick-theme';

function getSystemTheme() {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

function getSavedTheme() {
    return localStorage.getItem(STORAGE_KEY);
}

function applyTheme(theme) {
    if (theme === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
    updateToggleButton(theme);
}

function updateToggleButton(currentTheme) {
    const toggleBtn = document.getElementById('theme-toggle');
    if (!toggleBtn) return;

    if (currentTheme === 'light') {
        toggleBtn.textContent = '\u263E';
        toggleBtn.setAttribute('aria-label', 'Zu dunklem Modus wechseln');
        toggleBtn.setAttribute('title', 'Zu dunklem Modus wechseln');
    } else {
        toggleBtn.textContent = '\u263C';
        toggleBtn.setAttribute('aria-label', 'Zu hellem Modus wechseln');
        toggleBtn.setAttribute('title', 'Zu hellem Modus wechseln');
    }
}

function getCurrentTheme() {
    const saved = getSavedTheme();
    if (saved === 'light' || saved === 'dark') {
        return saved;
    }
    return getSystemTheme();
}

function toggleTheme() {
    const current = getCurrentTheme();
    const newTheme = current === 'light' ? 'dark' : 'light';
    localStorage.setItem(STORAGE_KEY, newTheme);
    applyTheme(newTheme);
}

function initTheme() {
    const theme = getCurrentTheme();
    applyTheme(theme);

    const toggleBtn = document.getElementById('theme-toggle');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleTheme);
    }

    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
        if (!getSavedTheme()) {
            applyTheme(e.matches ? 'light' : 'dark');
        }
    });
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTheme);
} else {
    initTheme();
}
