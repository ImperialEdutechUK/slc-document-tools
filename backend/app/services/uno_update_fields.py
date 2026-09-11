#!/usr/bin/python3
"""Update Word document indexes/fields through LibreOffice UNO.

This helper is intentionally executed with the operating system Python
(`/usr/bin/python3`) because Debian's ``python3-uno`` package is installed for
that interpreter, while the FastAPI application itself runs in the Python
image's /usr/local interpreter.
"""

from __future__ import annotations

import json
import sys
import time

import uno
from com.sun.star.beans import PropertyValue


def property_value(name: str, value):
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def connect(pipe_name: str):
    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_context
    )
    url = (
        f"uno:pipe,name={pipe_name};urp;"
        "StarOffice.ComponentContext"
    )

    last_error = None
    for _ in range(100):
        try:
            return resolver.resolve(url)
        except Exception as exc:  # UNO raises implementation-specific types.
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"Could not connect to LibreOffice UNO: {last_error}")


def update_fields(path: str, pipe_name: str) -> dict:
    context = connect(pipe_name)
    service_manager = context.ServiceManager
    desktop = service_manager.createInstanceWithContext(
        "com.sun.star.frame.Desktop", context
    )

    document = desktop.loadComponentFromURL(
        uno.systemPathToFileUrl(path),
        "_blank",
        0,
        (
            property_value("Hidden", True),
            property_value("ReadOnly", False),
            property_value("UpdateDocMode", 3),
        ),
    )
    if document is None:
        raise RuntimeError("LibreOffice could not open the DOCX for field refresh.")

    indexes_updated = 0
    fields_refreshed = False
    try:
        indexes = document.getDocumentIndexes()
        for index in range(indexes.getCount()):
            indexes.getByIndex(index).update()
            indexes_updated += 1

        try:
            document.getTextFields().refresh()
            fields_refreshed = True
        except Exception:
            # The TOC/index update above is the important operation. Some
            # document models do not expose a refreshable TextFields object.
            fields_refreshed = False

        document.store()
    finally:
        try:
            document.close(True)
        except Exception:
            document.dispose()

    return {
        "indexes_updated": indexes_updated,
        "fields_refreshed": fields_refreshed,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: uno_update_fields.py <document.docx> <pipe_name>", file=sys.stderr)
        return 2

    try:
        result = update_fields(sys.argv[1], sys.argv[2])
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
