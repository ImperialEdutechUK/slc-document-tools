from __future__ import annotations

import os
from pathlib import Path
import tempfile


def account_fallback_configured() -> bool:
    return bool(
        os.getenv("FREEPIK_ACCOUNT_EMAIL")
        and os.getenv("FREEPIK_ACCOUNT_PASSWORD")
        and os.getenv("FREEPIK_ACCOUNT_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}
    )


def download_with_account(resource_url: str) -> bytes:
    """Download a Freepik/Magnific stock resource with the configured account.

    This is intentionally a fallback behind an explicit feature flag. The normal
    stock API remains the preferred path. Website UI automation can change over
    time, so selectors can be overridden through environment variables without
    editing application code.
    """
    email = os.getenv("FREEPIK_ACCOUNT_EMAIL")
    password = os.getenv("FREEPIK_ACCOUNT_PASSWORD")
    if not email or not password:
        raise ValueError("Freepik account credentials are not configured.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Playwright is not installed for the Freepik account fallback.") from exc

    login_url = os.getenv(
        "FREEPIK_LOGIN_URL",
        "https://www.magnific.com/log-in?client_id=magnific&lang=en",
    )
    email_selector = os.getenv("FREEPIK_EMAIL_SELECTOR", 'input[type="email"]')
    password_selector = os.getenv("FREEPIK_PASSWORD_SELECTOR", 'input[type="password"]')
    submit_selector = os.getenv(
        "FREEPIK_SUBMIT_SELECTOR",
        'button[type="submit"]',
    )
    chromium_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "/usr/bin/chromium")
    timeout_ms = int(os.getenv("FREEPIK_BROWSER_TIMEOUT_MS", "30000"))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=chromium_path if Path(chromium_path).exists() else None,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        try:
            page.goto(login_url, wait_until="domcontentloaded")
            page.locator(email_selector).first.fill(email)
            page.locator(password_selector).first.fill(password)
            page.locator(submit_selector).first.click()

            # Successful sign-in normally navigates away from the login form.
            try:
                page.locator(password_selector).first.wait_for(state="detached", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                # Some versions keep the element mounted; continue only if the
                # browser has left the login URL.
                if "log-in" in page.url or "login" in page.url:
                    raise ValueError(
                        "Freepik/Magnific account sign-in did not complete. "
                        "The site may require a verification step or updated selectors."
                    )

            page.goto(resource_url, wait_until="domcontentloaded")

            custom_download_selector = os.getenv("FREEPIK_DOWNLOAD_SELECTOR")
            candidates = []
            if custom_download_selector:
                candidates.append(page.locator(custom_download_selector).first)
            candidates.extend(
                [
                    page.get_by_role("button", name="Download", exact=False).first,
                    page.get_by_role("link", name="Download", exact=False).first,
                    page.locator('a[download]').first,
                ]
            )

            last_error: Exception | None = None
            for candidate in candidates:
                try:
                    if not candidate.is_visible(timeout=3000):
                        continue
                    with page.expect_download(timeout=timeout_ms) as download_info:
                        candidate.click()
                    download = download_info.value
                    with tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / (download.suggested_filename or "freepik-download")
                        download.save_as(str(target))
                        payload = target.read_bytes()
                    if payload:
                        return payload
                except Exception as exc:  # selector/UI variants are expected here
                    last_error = exc

            raise ValueError(
                "Signed in to Freepik/Magnific but no downloadable asset was captured. "
                "Set FREEPIK_DOWNLOAD_SELECTOR if the account page uses a different Download control."
            ) from last_error
        finally:
            context.close()
            browser.close()
