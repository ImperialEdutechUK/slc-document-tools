"""Capture a Magnific/Freepik browser session for Railway.

Run locally on a trusted computer. A real browser opens; sign in normally and
complete any verification yourself. When you are fully signed in, return to the
terminal and press Enter. The script prints a base64 storage-state value that
can be stored in Railway as FREEPIK_ACCOUNT_N_STORAGE_STATE_B64.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

LOGIN_URL = "https://www.magnific.com/login"
OUTPUT = Path("freepik_storage_state.json")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("\nSign in to Magnific/Freepik in the opened browser.")
        print("Complete any email verification or 2FA normally.")
        input("When you are fully signed in, return here and press Enter... ")
        context.storage_state(path=str(OUTPUT))
        browser.close()

    raw = OUTPUT.read_bytes()
    # Validate and compact before encoding.
    data = json.loads(raw.decode("utf-8"))
    compact = json.dumps(data, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(compact).decode("ascii")

    print(f"\nSaved local session file: {OUTPUT.resolve()}")
    print("Add the following value to Railway as, for example:")
    print("FREEPIK_ACCOUNT_1_STORAGE_STATE_B64")
    print("\n--- COPY VALUE BELOW ---")
    print(encoded)
    print("--- END VALUE ---\n")
    print("Treat this value like a password. Do not commit it to Git or share it publicly.")


if __name__ == "__main__":
    main()
