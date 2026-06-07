# Flash path has lowest test coverage

1. **Time & Date:** 2026-05-30T00:00:00Z
2. **Name:** Flash and macOS disk glue under-tested
3. **Issue:** Coverage is concentrated in validate/render/manifest; the destructive flash path and platform disk code are barely exercised by unit tests
4. **Severity:** S4 (Minor)
5. **Location:** `easymanet/cli_flash.py` (~17%), `easymanet/disks/macos.py` (~12%), `easymanet/format.py` (~12%); compare `easymanet/validate.py` (~89%), `easymanet/image.py` (~75%)
6. **Expected:** Critical flash/disk paths have tests or a safe dry-run harness so regressions in wipe offsets, disk selection, and macOS `diskutil` integration are caught before hardware flashes
7. **Actual:** `pytest --cov=easymanet` passes the 50% CI floor (~55% total) but `cli_flash.py` lines 37–117 and 190–320 are largely uncovered; `disks/macos.py` is almost entirely untested on non-macOS CI; `format.human_size()` has no direct tests (11-line module, used via disk listing)
8. **Reproduction:**
   1. `pytest -q --cov=easymanet --cov-report=term-missing`
   2. Inspect missing lines for `cli_flash.py`, `disks/macos.py`, `format.py`
9. **Notes:** Hardest to unit-test without mocking Typer prompts, sudo, and `diskutil`. Reasonable follow-ups: unit tests for `human_size()`; Linux disk tests where feasible; optional `--dry-run` flash that exercises CLI wiring without writing blocks; macOS-only integration job if needed. Overlay wipe logic is better covered via `image.py` tests — do not conflate with flash CLI gaps.
