These three files (`__init__.py`, `kronos.py`, `module.py`) are vendored
unmodified except for one import path fix, from:

https://github.com/shiyu-coder/Kronos — commit on `master`, fetched 2026-08-19

MIT License, Copyright (c) 2025 ShiYu. See `LICENSE` in this directory.

The only change: `kronos.py` originally did `sys.path.append("../"); from
model.module import *`, which assumed the file sat in a top-level `model/`
package next to the caller. Changed to `from .module import *` so it works
as a normal Python package inside `aurum.forecast.kronos_vendor`. No other
lines were touched.
