from __future__ import annotations

import os
from pathlib import Path
import tempfile

_TRUE_VALUES = {"1", "true", "yes", "on"}
_MAX_ACCOUNTS = 10


def _fallback_enabled() -> bool:
    return os.getenv("FREEPIK_ACCOUNT_FALLBACK", "false").lower() in _TRUE_VALUES


def configured_accounts() -> list[tuple[str, str]]:
    """Return configured Freepik accounts in deterministic fallback order.

    Preferred multi-account variables are FREEPIK_ACCOUNT_1_EMAIL/PASSWORD,
    FREEPIK_ACCOUNT_2_EMAIL/PASSWORD, and so on. The original single-account
    FREEPIK_ACCOUNT_EMAIL/PASSWORD variables remain supported for backwards
    compatibility. If account 1 is not explicitly configured, the legacy
    account is treated as account 1, followed by indexed accounts 2..10.
    Duplicate credential pairs are ignored.
    """
    if not _fallback_enabled():
        return []

    accounts: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(email: str | None, password: str | None) -> None:
        if not email or not password:
            return
        pair = (email.strip(), password)
        if pair in seen:
            return
        seen.add(pair)
        accounts.append(pair)

    indexed_one = (
        os.getenv("FREEPIK_ACCOUNT_1_EMAIL"),
        os.getenv("FREEPIK_ACCOUNT_1_PASSWORD"),
    )
    legacy = (
        os.getenv("FREEPIK_ACCOUNT_EMAIL"),
        os.getenv("FREEPIK_ACCOUNT_PASSWORD"),
    )

    # Preserve an existing deployment that already uses the legacy names.
    if not all(indexed_one):
        add(*legacy)

    for index in range(1, _MAX_ACCOUNTS + 1):
        add(
            os.getenv(f"FREEPIK_ACCOUNT_{index}_EMAIL"),
            os.getenv(f"FREEPIK_ACCOUNT_{index}_PASSWORD"),
        )

    # If both legacy and explicit account 1 exist, keep legacy as a final
    # backwards-compatible fallback unless it duplicates an indexed account.
    if all(indexed_one):
        add(*legacy)

    return accounts


def account_fallback_configured() -> bool:
    return bool(configured_accounts())


def download_with_account(resource_url: str, *, email: str, password: str) -> bytes:
    """Download a Freepik/Magnific stock resource with one account."""
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
    submit_selector = os.getenv("FREEPIK_SUBMIT_SELECTOR", 'button[type="submit"]')
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

            try:
                page.locator(password_selector).first.wait_for(state="detached", timeout=timeout_ms)
            except PlaywrightTimeoutError:
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
                except Exception as exc:
                    last_error = exc

            raise ValueError(
                "Signed in to Freepik/Magnific but no downloadable asset was captured. "
                "Set FREEPIK_DOWNLOAD_SELECTOR if the account page uses a different Download control."
            ) from last_error
        finally:
            context.close()
            browser.close()


def download_with_accounts(resource_url: str) -> bytes:
    """Try each configured account in order without exposing credentials."""
    accounts = configured_accounts()
    if not accounts:
        raise ValueError("No Freepik account fallback credentials are configured.")

    failures: list[str] = []
    for position, (email, password) in enumerate(accounts, start=1):
        try:
            return download_with_account(resource_url, email=email, password=password)
        except Exception as exc:
            failures.append(f"account {position}: {exc}")

    raise ValueError("All configured Freepik accounts failed (" + "; ".join(failures) + ").")
