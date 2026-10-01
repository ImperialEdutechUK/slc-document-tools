from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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


def _page_scopes(page):
    """Return the main page plus child frames for auth widgets embedded in iframes."""
    scopes = [page]
    for frame in page.frames:
        if frame != page.main_frame:
            scopes.append(frame)
    return scopes


def _first_visible(locator_candidates, *, timeout_ms: int = 5000):
    """Return the first visible Playwright locator from a list of locators."""
    for locator in locator_candidates:
        try:
            if locator.count() and locator.first.is_visible(timeout=timeout_ms):
                return locator.first
        except Exception:
            continue
    return None


def _click_first_visible(locator_candidates, *, timeout_ms: int = 5000) -> bool:
    locator = _first_visible(locator_candidates, timeout_ms=timeout_ms)
    if locator is None:
        return False
    locator.click()
    return True


def _download_with_account_sync(resource_url: str, *, email: str, password: str) -> bytes:
    """Run the Playwright sync workflow in a thread that has no asyncio loop."""
    if not email or not password:
        raise ValueError("Freepik account credentials are not configured.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ValueError("Playwright is not installed for the Freepik account fallback.") from exc

    login_url = os.getenv("FREEPIK_LOGIN_URL", "https://www.magnific.com/login")
    custom_email_selector = os.getenv("FREEPIK_EMAIL_SELECTOR")
    custom_password_selector = os.getenv("FREEPIK_PASSWORD_SELECTOR")
    custom_submit_selector = os.getenv("FREEPIK_SUBMIT_SELECTOR")
    chromium_path = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "/usr/bin/chromium")
    timeout_ms = int(os.getenv("FREEPIK_BROWSER_TIMEOUT_MS", "45000"))

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

            # Magnific's current sign-in screen may first present Google/Apple and
            # a separate "Continue with email" control before rendering the form.
            scopes = _page_scopes(page)

            cookie_candidates = []
            for scope in scopes:
                cookie_candidates.extend(
                    [
                        scope.get_by_role("button", name="Accept all", exact=False),
                        scope.get_by_role("button", name="Accept", exact=True),
                        scope.get_by_role("button", name="Allow all", exact=False),
                    ]
                )
            _click_first_visible(cookie_candidates, timeout_ms=1500)

            email_entry_candidates = []
            for scope in scopes:
                email_entry_candidates.extend(
                    [
                        scope.get_by_role("button", name="Continue with email", exact=False),
                        scope.get_by_role("link", name="Continue with email", exact=False),
                        scope.get_by_role("button", name="Sign in with email", exact=False),
                        scope.get_by_role("link", name="Sign in with email", exact=False),
                        scope.get_by_text("Continue with email", exact=False),
                        scope.get_by_text("Sign in with email", exact=False),
                    ]
                )
            _click_first_visible(email_entry_candidates, timeout_ms=5000)
            page.wait_for_timeout(500)
            scopes = _page_scopes(page)

            email_candidates = []
            for scope in scopes:
                if custom_email_selector:
                    email_candidates.append(scope.locator(custom_email_selector))
                email_candidates.extend(
                    [
                        scope.locator('input[type="email"]'),
                        scope.locator('input[name="email"]'),
                        scope.locator('input[autocomplete="email"]'),
                        scope.locator('input[placeholder*="email" i]'),
                        scope.get_by_label("Email", exact=False),
                    ]
                )
            email_box = _first_visible(email_candidates, timeout_ms=10000)
            if email_box is None:
                raise ValueError(
                    "Magnific login email field was not found after opening the email sign-in flow. "
                    f"Current page: {page.url}. The site may be showing a verification, consent, "
                    "bot-protection, or changed login screen."
                )
            email_box.fill(email)

            password_candidates = []
            for scope in _page_scopes(page):
                if custom_password_selector:
                    password_candidates.append(scope.locator(custom_password_selector))
                password_candidates.extend(
                    [
                        scope.locator('input[type="password"]'),
                        scope.locator('input[name="password"]'),
                        scope.locator('input[autocomplete="current-password"]'),
                        scope.locator('input[placeholder*="password" i]'),
                        scope.get_by_label("Password", exact=False),
                    ]
                )

            password_box = _first_visible(password_candidates, timeout_ms=2500)
            if password_box is None:
                # Some auth flows ask for email first, then render the password
                # field only after Continue/Next.
                advance_candidates = []
                for scope in _page_scopes(page):
                    if custom_submit_selector:
                        advance_candidates.append(scope.locator(custom_submit_selector))
                    advance_candidates.extend(
                        [
                            scope.get_by_role("button", name="Continue", exact=False),
                            scope.get_by_role("button", name="Next", exact=False),
                            scope.get_by_role("button", name="Sign in", exact=False),
                            scope.locator('button[type="submit"]'),
                        ]
                    )
                if not _click_first_visible(advance_candidates, timeout_ms=5000):
                    email_box.press("Enter")
                password_box = _first_visible(password_candidates, timeout_ms=10000)

            if password_box is None:
                raise ValueError(
                    "Magnific password field was not found after submitting the email. "
                    f"Current page: {page.url}. The account may use Google/Apple sign-in, "
                    "or Magnific may be requesting verification/2FA."
                )

            password_box.fill(password)

            submit_candidates = []
            for scope in _page_scopes(page):
                if custom_submit_selector:
                    submit_candidates.append(scope.locator(custom_submit_selector))
                submit_candidates.extend(
                    [
                        scope.get_by_role("button", name="Sign in", exact=False),
                        scope.get_by_role("button", name="Log in", exact=False),
                        scope.get_by_role("button", name="Continue", exact=False),
                        scope.locator('button[type="submit"]'),
                    ]
                )
            if not _click_first_visible(submit_candidates, timeout_ms=5000):
                password_box.press("Enter")

            # Allow redirects/callbacks to settle. Do not depend on the password
            # field detaching because modern auth UIs can keep it mounted.
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(1500)

            lowered_url = page.url.lower()
            password_still_visible = _first_visible(password_candidates, timeout_ms=1500) is not None
            verification_visible = False
            try:
                visible_text = page.locator("body").inner_text(timeout=2000).lower()
                verification_visible = any(
                    marker in visible_text
                    for marker in (
                        "verification code",
                        "security code",
                        "two-factor",
                        "two factor",
                        "check your email",
                        "verify your identity",
                        "captcha",
                    )
                )
            except Exception:
                pass

            if verification_visible:
                raise ValueError(
                    "Magnific requires an interactive verification/2FA step for this login. "
                    "A headless Railway browser cannot complete that step automatically."
                )
            if password_still_visible and ("login" in lowered_url or "log-in" in lowered_url):
                raise ValueError(
                    "Magnific account sign-in did not complete. Check the account credentials, "
                    "sign-in method, or whether Magnific is requiring verification."
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

def download_with_account(resource_url: str, *, email: str, password: str) -> bytes:
    """Download with one account without calling Playwright sync API on an asyncio thread.

    FastAPI endpoints may invoke the linked-image pipeline while an asyncio event
    loop is already running. Playwright's sync API intentionally refuses to run
    on that same thread. In that case, execute the browser workflow in a short-
    lived worker thread. Normal synchronous callers still run directly.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _download_with_account_sync(resource_url, email=email, password=password)

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="freepik-playwright") as executor:
        future = executor.submit(
            _download_with_account_sync,
            resource_url,
            email=email,
            password=password,
        )
        return future.result()


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
