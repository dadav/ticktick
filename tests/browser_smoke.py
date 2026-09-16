"""Run with: uv run --with playwright python tests/browser_smoke.py.

Uses a temporary database, a controlled server clock, and system Chromium.
"""

import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with tempfile.TemporaryDirectory(prefix="ticktick-browser-") as directory:
        os.environ["TICKTICK_DB_PATH"] = str(Path(directory) / "test.db")
        import uvicorn

        from app.services import calculations, statistics, timer
        from main import app

        test_clock = {"now": datetime(2026, 9, 15, 6)}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test_clock["now"]

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        base_url = f"http://127.0.0.1:{port}"
        with (
            patch.object(timer, "datetime", Clock),
            patch.object(statistics, "datetime", Clock),
            patch.object(calculations, "datetime", Clock),
        ):
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                for _ in range(100):
                    try:
                        with urlopen(base_url + "/api/status", timeout=1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    raise RuntimeError("Browser test server did not start")
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=shutil.which("chromium"), headless=True
                    )
                    page = browser.new_page(viewport={"width": 1100, "height": 1000})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("dialog", lambda dialog: dialog.accept())

                    def at(hour):
                        test_clock["now"] = datetime(2026, 9, 15) + timedelta(
                            hours=hour
                        )

                    def refresh():
                        page.evaluate("fetchStatus()")

                    def clear_history():
                        payload = page.request.get(
                            base_url + "/api/statistics/summary"
                        ).json()
                        for group in payload["recent_days"]:
                            for session in group["sessions"]:
                                response = page.request.delete(
                                    base_url + f"/api/sessions/{session['id']}"
                                )
                                assert response.ok

                    page.goto(base_url)
                    expect(page.locator("#btn-start")).to_be_enabled()
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(10)
                    page.locator("#btn-stop").click()
                    expect(page.locator("#current-time")).to_have_text("04:00:00")
                    expect(page.locator(".forecast-row").first).to_be_hidden()
                    at(16)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(20)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("08:00:00")
                    expect(page.locator("#session-time")).to_have_text("04:00:00")
                    expect(page.locator("#normal-leave")).to_have_text("20:12 Uhr")
                    expect(page.locator("#latest-leave")).to_have_text("22:00 Uhr")
                    at(20.2)
                    page.locator("#btn-stop").click()
                    expect(page.locator("#current-time")).to_have_text("08:12:00")
                    expect(page.locator("#day-notice")).to_have_text(
                        "Tagessoll erreicht."
                    )
                    page.reload()
                    expect(page.locator("#current-time")).to_have_text("08:12:00")

                    page.goto(base_url + "/statistics")
                    expect(page.locator(".workday")).to_have_count(1)
                    expect(page.locator(".session-item")).to_have_count(2)
                    expect(page.locator(".workday-total")).to_have_text(
                        "08:12:00 angerechnet"
                    )
                    page.locator(".session-open").first.click()
                    page.locator("#session-modal").get_by_role(
                        "button", name="Bearbeiten"
                    ).click()
                    page.locator("#edit-end-time").fill("09:00")
                    page.get_by_role("button", name="Speichern").click()
                    expect(page.locator(".workday-total")).to_have_text(
                        "07:12:00 angerechnet"
                    )
                    expect(page.locator("#week-total")).to_have_text("07:12:00")
                    expect(page.locator(".session-item .duration").first).to_have_text(
                        "03:00"
                    )
                    page.locator(".modal-close").click()
                    page.locator(".btn-delete").first.click()
                    expect(page.locator(".session-item")).to_have_count(1)
                    expect(page.locator(".workday-total")).to_have_text(
                        "04:12:00 angerechnet"
                    )
                    page.get_by_role("button", name="Eintrag hinzufügen").click()
                    page.locator("#add-date").fill("2026-09-15")
                    page.locator("#add-start").fill("06:00")
                    page.locator("#add-end").fill("10:00")
                    page.get_by_role("button", name="Speichern").click()
                    expect(page.locator(".session-item")).to_have_count(2)
                    expect(page.locator(".workday-total")).to_have_text(
                        "08:12:00 angerechnet"
                    )
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    clear_history()

                    at(6)
                    page.goto(base_url)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(13)
                    page.locator("#btn-pause").click()
                    expect(page.locator("#current-time")).to_have_text("06:30:00")
                    at(13.25)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("06:45:00")
                    expect(page.locator("#net-time")).to_have_text("07:00:00")
                    expect(page.locator("#lunch-deduction")).to_have_text("00:15:00")
                    at(13.5)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("07:00:00")
                    page.locator("#btn-continue").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(14.5)
                    page.locator("#btn-stop").click()
                    expect(page.locator("#current-time")).to_have_text("08:00:00")
                    at(15)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    page.locator("#btn-reset").click()
                    expect(page.locator("#status-text")).to_have_text("Bereit")
                    expect(page.locator("#current-time")).to_have_text("08:00:00")
                    expect(page.locator(".toast")).to_have_text(
                        "Aktueller Eintrag verworfen."
                    )
                    clear_history()

                    at(6)
                    refresh()
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(17)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("10:00:00")
                    expect(page.locator("#btn-start")).to_be_disabled()
                    expect(page.locator("#day-notice")).to_contain_text(
                        "Tagesmaximum erreicht"
                    )
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                    page.goto(base_url + "/statistics")
                    expect(page.locator(".session-item .times")).to_have_text(
                        "06:00 - 16:30"
                    )

                    clear_history()
                    at(12)
                    page.goto(base_url)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    active_id = page.request.get(base_url + "/api/status").json()[
                        "session"
                    ]["id"]
                    at(16)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("04:00:00")
                    history_page = browser.new_page()
                    history_page.on(
                        "pageerror", lambda error: errors.append(str(error))
                    )
                    history_page.on("dialog", lambda dialog: dialog.accept())
                    history_page.goto(base_url + "/statistics")
                    history_page.get_by_role(
                        "button", name="Eintrag hinzufügen"
                    ).click()
                    history_page.locator("#add-date").fill("2026-09-15")
                    history_page.locator("#add-start").fill("00:00")
                    history_page.locator("#add-end").fill("08:00")
                    history_page.get_by_role("button", name="Speichern").click()
                    expect(history_page.locator(".session-item")).to_have_count(2)
                    expect(history_page.locator(".workday-total")).to_have_text(
                        "12:00:00 angerechnet"
                    )
                    refresh()
                    expect(page.locator("#status-text")).to_have_text("Bereit")
                    expect(page.locator("#day-notice")).to_contain_text(
                        "Tagesmaximum überschritten"
                    )
                    expect(page.locator("#btn-start")).to_be_disabled()
                    details = page.request.get(
                        base_url + f"/api/sessions/{active_id}"
                    ).json()
                    assert details["end_time"] == "16:00"
                    assert details["net_work_formatted"] == "04:00"
                    history_page.locator(".session-open").last.click()
                    history_page.locator("#session-modal").get_by_role(
                        "button", name="Bearbeiten"
                    ).click()
                    history_page.locator("#edit-end-time").fill("17:00")
                    with (
                        history_page.expect_event("dialog") as validation_dialog,
                        history_page.expect_response(
                            lambda response: response.request.method == "PUT"
                        ) as validation_response,
                    ):
                        history_page.get_by_role("button", name="Speichern").click()
                    assert validation_response.value.status == 422
                    expect(
                        history_page.locator(".session-item .times").last
                    ).to_have_text("12:00 - 16:00")
                    assert (
                        validation_dialog.value.message
                        == "Fehler: Der Eintrag darf nicht in der Zukunft liegen."
                    )
                    validation_text = history_page.evaluate("""async () => readErrorDetail(await fetch('/api/sessions', {
                        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
                    }))""")
                    assert "Datum: Pflichtfeld fehlt." in validation_text
                    assert "Startzeit: Pflichtfeld fehlt." in validation_text
                    assert "Endzeit: Pflichtfeld fehlt." in validation_text
                    history_page.locator(".modal-close").click()
                    clear_history()

                    at(12)
                    response = page.request.post(
                        base_url + "/api/sessions",
                        data={
                            "date": "2026-09-15",
                            "start_time": "00:00",
                            "end_time": "06:00",
                        },
                    )
                    assert response.ok
                    page.goto(base_url)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    active_id = page.request.get(base_url + "/api/status").json()[
                        "session"
                    ]["id"]
                    at(15.5)
                    history_page.reload()
                    history_page.locator(".session-open").first.click()
                    history_page.locator("#session-modal").get_by_role(
                        "button", name="Bearbeiten"
                    ).click()
                    history_page.locator("#edit-end-time").fill("08:00")
                    history_page.get_by_role("button", name="Speichern").click()
                    expect(history_page.locator(".session-item")).to_have_count(2)
                    expect(history_page.locator(".workday-total")).to_have_text(
                        "11:30:00 angerechnet"
                    )
                    details = page.request.get(
                        base_url + f"/api/sessions/{active_id}"
                    ).json()
                    assert details["end_time"] == "15:30"
                    assert details["net_work_formatted"] == "03:30"
                    refresh()
                    expect(page.locator("#day-notice")).to_contain_text(
                        "Tagesmaximum überschritten"
                    )
                    history_page.close()
                    clear_history()

                    at(47)
                    page.goto(base_url)
                    page.locator("#btn-start").click()
                    expect(page.locator("#status-text")).to_have_text("Läuft")
                    at(49)
                    refresh()
                    expect(page.locator("#current-time")).to_have_text("02:00:00")
                    expect(page.locator("#timer-caption")).to_contain_text("2026-09-16")
                    assert not errors, errors
                    browser.close()
                    print(
                        "Browser checks passed: split sessions, paused credit, history editing/addition/deletion, discard, cap, history corrections, German validation, overnight date, and mobile layout."
                    )
            finally:
                server.should_exit = True
                thread.join(timeout=10)


if __name__ == "__main__":
    main()
