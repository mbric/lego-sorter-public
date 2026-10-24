# 06 — Code Review Refactor Plan

**Branch:** `refactor/code-review`
**Source:** full-codebase review, 2026-07-25. This plan is written to be executed
phase-by-phase by an agent. Each phase is independent enough to commit and stop.

## Ground rules (read first, apply to every phase)

1. **No behavior changes unless a phase explicitly says so.** In particular, do
   NOT change any tuning constant in `src/orchestrator/sort_cycle.py` (the
   `WAIT_CLEAR_*`, `EMPTY_REF_EMA*`, `REMOVAL_*`, `PEAK_DROP_FRAC`, threshold
   defaults, etc.). Those values are hand-tuned against real hardware.
2. **One commit per phase**, message format: `refactor(phase N): <summary>`.
   Do not commit unrelated files that were already dirty in the working tree
   (`git add` specific paths, never `git add -A` / `git add .`).
3. **Verify after every phase** with the smoke checks in that phase plus the
   global smoke check:

   ```bash
   .venv/bin/python -c "from demo_brickognize.app import build_app; build_app()"
   .venv/bin/python -m pytest -q          # once Phase 2 exists
   ```

   The `build_app()` import check constructs the whole Gradio Blocks tree
   without launching a server; it catches most wiring mistakes. (Before
   Phase 1 it needs `PYTHONPATH=src`; after Phase 1 it works as-is.)
4. **Never touch `data/`** (sessions, poses.json, caches, reference images).
5. Robot code (`src/robot/`, `run_robot_*.py`) cannot be exercised without the
   arm. For those phases, verification = import checks + unit tests only. Do
   not attempt to connect to hardware.
6. Do not mass-reformat files. Run `ruff check --fix` only on files a phase
   already touches. Keep diffs reviewable.
7. If something in this plan contradicts what you find in the code, stop and
   note it in the commit message rather than improvising a bigger change.

---

## Phase 1 — Packaging: pyproject, one import convention, dependency cleanup

**Problem.** Two import conventions coexist: `run_brickognize_demo.py` inserts
`src/` on `sys.path` and imports `demo_brickognize.*` / `feeder.*` /
`orchestrator.*` / `robot.*` as top-level packages, while `run_robot_*.py`
scripts import `src.robot.*` (enabled by `src/__init__.py`). If both styles meet
in one process, modules load twice (two `SESSION` singletons, etc.). Also:
`requirements.txt` lists `python-dotenv` twice and omits `numpy`, which
`sort_cycle.py` / `color_detect.py` import directly.

**Steps.**

1. Create `pyproject.toml` at repo root:

   ```toml
   [build-system]
   requires = ["setuptools>=68"]
   build-backend = "setuptools.build_meta"

   [project]
   name = "lego-sorter"
   version = "0.1.0"
   requires-python = ">=3.10"
   dependencies = [
       "gradio>=5.0.0",
       "requests>=2.31.0",
       "pillow>=10.0.0",
       "requests-oauthlib>=1.3.0",
       "python-dotenv>=1.0.0",
       "numpy>=1.26",
   ]

   [project.optional-dependencies]
   dev = ["pytest>=8.0", "ruff>=0.5"]

   [tool.setuptools.packages.find]
   where = ["src"]

   [tool.ruff]
   line-length = 100
   src = ["src"]

   [tool.ruff.lint]
   select = ["E", "F", "W", "I", "UP", "B"]
   ignore = ["E501"]

   [tool.pytest.ini_options]
   testpaths = ["tests"]
   ```

2. Delete `src/__init__.py` (it only exists to make `src.robot.*` importable).
   The other `__init__.py` files under `src/*/` stay.
3. `pip install -e ".[dev]"` into `.venv`.
4. Standardize every `run_*.py` script on top-level package imports:
   - In all `run_robot_*.py` and `run_robot_teach*.py`: replace `from
     src.robot.X import Y` with `from robot.X import Y` (also the inline
     `from src.robot.poses import POSES_PATH` inside `run_robot_teach.py`).
   - In `run_brickognize_demo.py`: delete the `sys.path.insert` block (the
     editable install makes it unnecessary).
5. Replace `requirements.txt` contents with:

   ```
   -e .[dev]
   ```

   Leave `requirements-robot.txt` as-is (cri_lib stays optional; robot modules
   must keep working when it is absent — see Phase 3 step 2).
6. `rg "src\.robot|sys\.path" run_*.py src docs README.md PLAN.md` — update any
   remaining references (docs mention commands, not imports; only change code).

**Known caution (do not fix, just preserve):** the top-level package name
`robot` collides with the `robotframework` PyPI package. We accept this;
nothing in this project installs robotframework. Renaming packages is out of
scope.

**Verify.**

```bash
.venv/bin/python -c "import demo_brickognize.app, orchestrator.sort_cycle, feeder.http_client, robot.poses"
.venv/bin/python -c "from demo_brickognize.app import build_app; build_app()"
.venv/bin/python run_robot_status.py --help 2>/dev/null; echo "exit=$? (import errors would be a traceback, nonzero-without-traceback is fine)"
```

---

## Phase 2 — Test harness for the pure logic (write BEFORE refactoring it)

**Problem.** Zero tests. Phases 4–7 refactor exactly the code that is easiest
to test. Write the safety net first.

**Steps.** Create `tests/` with the following files. All tests must run with no
network, no camera, no robot, and must not write inside `data/` (use `tmp_path`).

1. `tests/conftest.py` — helper to build synthetic frames:

   ```python
   import numpy as np

   def make_frame(h=240, w=320, gray=200):
       """Uniform RGB uint8 frame."""
       return np.full((h, w, 3), gray, dtype=np.uint8)

   def add_blob(frame, y0, y1, x0, x1, value=30):
       """Paint a dark rectangle (a 'part') onto a copy of frame."""
       out = frame.copy()
       out[y0:y1, x0:x1] = value
       return out
   ```

2. `tests/test_sort_cycle_vision.py` — pure vision helpers in
   `orchestrator.sort_cycle`:
   - `LandingRoi.clamped()` clamps inverted/out-of-range values and keeps
     `bottom > top`, `right > left`.
   - `load_landing_roi` / `save_landing_roi` round-trip via a monkeypatched
     `ROI_CONFIG_PATH` pointing into `tmp_path`.
   - `_occupancy_percent`: empty-vs-empty ≈ 0; a painted blob covering ~10% of
     the ROI reports roughly that percentage (assert within a factor of 2, the
     downscale blurs edges); a blob smaller than `MIN_BLOB_PIXELS` after
     downscale reports 0.
   - `_largest_blob_pixels`: hand-built boolean masks — empty mask → 0, one
     5-pixel plus-shape → 5, two disjoint blobs → size of the larger.
   - `_normalize_frame`: float 0–1 input scales to uint8; RGBA input drops
     alpha.
3. `tests/test_sort_cycle_state.py` — the `SortCycle` state machine end to end
   with fakes:

   ```python
   class FakeFeeder:
       def __init__(self): self.calls = []
       def start(self): self.calls.append("start")
       def stop(self): self.calls.append("stop")
       def advance(self): self.calls.append("advance")
   ```

   Build `SortCycle(feeder=FakeFeeder(), identify_fn=...)`, monkeypatch
   `REFERENCE_PATH` and `CAPTURES_DIR` into `tmp_path`, call
   `set_empty_reference(make_frame(), save=False)`, then drive `on_frame` with
   frame sequences. Scenarios (use `occupied_frames=1, empty_frames=1,
   settle_s=0.0` via `on_frame` kwargs to keep sequences short; for the settle
   timer, `time.monotonic` can be left real since settle_s=0):
   - **Idle:** `on_frame` while idle returns state IDLE, feeder untouched.
   - **Happy path:** `start_loop()` → feeder got `start`; empty frames keep
     state ADVANCING; a blob frame → state SETTLING and feeder got `stop`;
     repeated identical blob frames → CAPTURED (captured=True, a still saved
     under the patched `CAPTURES_DIR`); next frame with `auto_match=True` →
     MATCHING → identify_fn called → WAIT_CLEAR with `matched=True` and
     `virtual_bin` set; empty frames (and enough of them, given removal
     gating) eventually → ADVANCING and feeder got a second `start`.
     Note: WAIT_CLEAR requires `WAIT_CLEAR_STABLE_S` (0.6 s) of stillness
     before trusting removal — either sleep past it in the test or monkeypatch
     `orchestrator.sort_cycle.WAIT_CLEAR_STABLE_S` to 0.0.
   - **Identify retry:** identify_fn returning `IdentifyOutcome(error="No
     shape match")` (candidate=None) once → state returns to SETTLING and
     `_match_retries_left` decremented; second failure → WAIT_CLEAR.
   - **Feeder failure:** FakeFeeder whose `start` raises `ConveyorError` →
     `start_loop` returns state IDLE with the error string.
   - **force_clear:** during WAIT_CLEAR, `force_clear(make_frame())` →
     ADVANCING, feeder got `start`, and a new reference JPEG exists at the
     patched `REFERENCE_PATH`.
4. `tests/test_color_detect.py`:
   - `detect_color_in_bbox` on synthetic solid-color crops returns the right
     key for pure red `(201, 26, 9)`, blue `(0, 85, 191)`, yellow
     `(242, 205, 55)`, green `(35, 120, 65)`, black `(10, 10, 10)`, white
     `(250, 250, 250)`. Build a full image + a `BoundingBox` covering the
     middle (set `image_width/height` to the array size, `score=0.9`).
   - A mid-gray crop returns `unknown`.
   - `BoundingBox.to_pixel_rect` maps API coordinates to a resized image
     correctly (e.g. box on a 1280×960 source projected onto 320×240).
5. `tests/test_routing.py` — `virtual_bin`: known color wins; color unknown +
   category `"Brick, Modified"` → `brick_modified`; both missing → `unknown`.
6. `tests/test_poses.py` — `PoseSet.to_dict`/`from_dict` round-trip including
   defaults and a partial bin; `load_poses` on a missing path returns the MVP
   skeleton (all of `MVP_BIN_IDS` present); `save_poses`+`load_poses`
   round-trip in `tmp_path`; `require_bin` raises `KeyError` on incomplete
   bins; malformed JSON raises `RuntimeError`.
   **Note:** this file imports `robot.poses`, which currently imports
   `cri_lib` at module top. If `cri_lib` is not installed in the venv, do
   Phase 3 step 2 (TYPE_CHECKING guard) first, then come back — or write the
   test with `pytest.importorskip("robot.poses")` and un-skip in Phase 3.
7. `tests/test_bricklink_parse.py` — scrape parser against an inline HTML
   fixture (no network). Build a string with four summary blocks in the page's
   shape, e.g. repeated
   `>Qty Avg Price:</TD><TD ALIGN="RIGHT"><B>US $0.0813</B>` /
   `>Avg Price:</TD>...<B>US $0.10</B>` / `>Total Qty:</TD>...<B>1,234</B>`
   segments, and assert `_summary_values` / `_summary_ints` return 4 entries
   with the right numbers, commas handled.
8. `tests/test_session_catalog.py` — construct `SessionCatalog(base_dir=tmp_path)`
   directly (do NOT import the module-level `CATALOG`; importing the module is
   fine, it creates `data/sessions` which already exists). Cases: `log_scan`
   with a minimal `Candidate` writes header+row CSV and the session becomes
   persisted; `read_scans` round-trips; `inventory_totals` aggregates two
   scans of the same part+color into count 2; `purge_empty_sessions` removes a
   manually created empty session dir; `export_zip` produces a zip containing
   `scans.csv`, `inventory.csv`, `README.txt`. For valuation, monkeypatch
   `session_catalog.bricklink_client.is_configured` to return False and assert
   the valuation has `configured=False` and rows with `None` prices.

**Verify:** `.venv/bin/python -m pytest -q` — all green. Also confirm nothing
was created under the repo's real `data/` (`git status data/` must be clean).

---

## Phase 3 — Bug fixes (small, isolated, each testable)

Behavior changes here are intentional and listed.

1. **BrickLink: stop caching network failures for a week.**
   File: `src/demo_brickognize/bricklink_client.py`, in `price_for`.
   Today a `RequestException` inside `_price_via_api` / `_price_via_scrape`
   returns `None` and that `None` is cached with a fresh timestamp → a WiFi
   blip poisons that part/color for `CACHE_TTL_SECONDS` (7 days).
   Fix: make the fetch outcome distinguishable. Change both `_price_via_*`
   helpers to return `PartValue | None` where `None` strictly means
   *fetch failed* (they already do — a successful lookup with no data returns
   a zero-priced `PartValue`). Then in `price_for`, only write the cache entry
   when `value is not None`:

   ```python
   if value is not None:
       with _lock:
           cache = _load_cache()
           cache[key] = {
               "ts": time.time(),
               "value": value.__dict__ if value.has_price else None,
           }
           _save_cache()
   ```

   (Keep caching "genuinely no price data" as `None`-valued entries — that
   part is correct.) Add a test in `tests/test_bricklink_parse.py`:
   monkeypatch `requests.get` to raise `requests.RequestException`, call
   `price_for` with a tmp `CACHE_PATH` (monkeypatch module attr and reset the
   module-level `_cache` global to `None`), assert the cache file has no entry
   afterwards.

2. **`robot/poses.py` must import without `cri_lib`.**
   It imports `CRIController` at module top only for the `capture_pose` type
   hint. Move it under `TYPE_CHECKING` (same pattern as
   `src/robot/cri_session.py`) and quote the annotation:

   ```python
   from typing import TYPE_CHECKING
   if TYPE_CHECKING:
       from cri_lib import CRIController

   def capture_pose(controller: "CRIController") -> Pose: ...
   ```

   Un-skip/enable `tests/test_poses.py`.

3. **`force_clear` self-diff.** `src/orchestrator/sort_cycle.py`,
   `force_clear`: both branches compute
   `occupied = _occupancy_percent(gray, gray, roi=self.roi)` — a frame diffed
   against itself, always 0.0. Replace with `occupied = 0.0` and delete the
   now-unused `gray` variables/`assert`.

4. **Remove the `gr.skip()` duck-check.**
   File: `src/demo_brickognize/app.py`. `identify_live_frame` returns
   `gr.skip()` (whose being a `dict` is undocumented) and `stream_frame`
   detects it with `isinstance(result, dict)`. `identify_live_frame` is only
   called from `stream_frame` (verify with `rg identify_live_frame`), so
   change its contract: return `None` for "nothing to do" (replace both the
   `return gr.skip()` and the `_live_idle(...)`-returning paths carefully —
   see below) and a tuple only when there is a real result.
   Precisely: keep `_live_idle(...)` returns as tuples (they carry status
   text), change only the top `if not live_enabled: return gr.skip()` to
   `return None`, and in `stream_frame` change
   `if not isinstance(result, dict):` to `if result is not None:`.
   Wait — `_live_idle` tuples contain `gr.skip()` *elements*, which is fine
   (per-output skip is the supported use); only the whole-return sentinel was
   fragile.

5. **Thread-safety in `SortCycle`: make the lock real.**
   `self._lock` currently guards only `self.state` writes; all other mutable
   state (`_empty_ref`, streaks, `_prev_gray`, `_wait_peak_occupancy`) is
   mutated unlocked, and `force_clear` / `stop_loop` / `start_loop` run on
   Gradio button threads concurrently with `on_frame` on the stream thread.
   Fix:
   - Change `self._lock = threading.Lock()` → `threading.RLock()` (required:
     `on_frame` calls `stop_loop` internally when the reference is missing).
   - Wrap the entire bodies of `on_frame`, `force_clear`, `start_loop`,
     `stop_loop`, `set_empty_reference`, `load_empty_reference`, and
     `observe_frame` in `with self._lock:`.
   - Delete the now-redundant inner `with self._lock:` blocks inside the
     `_handle_*` helpers and the wrapped methods (plain assignments remain).
   - Do NOT hold the lock across `self.identify_fn(still)` (network call,
     ~1 s). In `_handle_matching`, release the lock around that call: easiest
     is to keep `on_frame` fully locked EXCEPT dispatching MATCHING — i.e.
     inside `on_frame`, when the state is MATCHING, exit the `with` block and
     call `self._handle_matching(...)`, which re-acquires the lock for its
     state mutations. Keep `identify_fn` invocation itself outside any lock.
   - The feeder HTTP calls (`self.feeder.start()/stop()`, 3 s timeout) are
     also blocking; holding the lock during them is acceptable (they only
     race against other button clicks) — leave them inside.

   Re-run the Phase 2 state-machine tests; they must pass unchanged.

**Verify:** pytest green; `build_app()` smoke check; `python -c "import robot.poses"`
in a venv without cri_lib (or `pip uninstall cri_lib` temporarily — skip if
that is disruptive, the TYPE_CHECKING pattern is self-evident).

---

## Phase 4 — SortCycle: compute the frame diff once, faster blobs, dedupe transitions

All internal to `src/orchestrator/sort_cycle.py`. Occupancy *values* must not
change (tests from Phase 2 pin behavior).

1. **One diff per (reference, frame) pair.**
   `on_frame` currently recomputes crop → exposure-match → threshold → flood
   fill up to five times per tick (initial `_occupancy_percent`,
   `_changed_pixel_percent` inside `_note_occupancy_sample` during WAIT_CLEAR,
   the WAIT_CLEAR re-measure, the removal score, the post-EMA re-measure).
   Refactor:
   - Change `_roi_diff_mask` callers to share work: add

     ```python
     @dataclass
     class _DiffStats:
         occupancy: float      # largest-blob % of ROI
         changed_pct: float    # % of ROI pixels changed

     def _diff_stats(empty_ref, gray, *, roi, pixel_thr=PIXEL_DIFF_THR) -> _DiffStats:
         a, changed = _roi_diff_mask(empty_ref, gray, roi=roi, pixel_thr=pixel_thr)
         blob = _largest_blob_pixels(changed)
         occupancy = 0.0 if blob < MIN_BLOB_PIXELS else blob * 100.0 / a.size
         return _DiffStats(occupancy, float(changed.mean() * 100.0) if changed.size else 0.0)
     ```

   - In `on_frame`, compute `stats = _diff_stats(empty_ref, gray, roi=roi)`
     once and pass `stats.occupancy` where `occupied_score` was used. Change
     `_note_occupancy_sample(occupied_score, gray)` to accept the precomputed
     `changed_pct` (new parameter) instead of calling `_changed_pixel_percent`
     itself.
   - Re-measures after an EMA update of `self._empty_ref` are *legitimate*
     (the reference changed). Make `_note_occupancy_sample` return `True` when
     it modified `self._empty_ref`, and only then re-run `_diff_stats` in
     `on_frame`'s WAIT_CLEAR branch. Same idea inside `_handle_wait_clear`'s
     "stuck EMA" block: recompute only after the blend actually ran.
   - `observe_frame` uses `_diff_stats` too.
   - Keep `_occupancy_percent` and `_changed_pixel_percent` as thin wrappers
     over `_diff_stats` (tests and removal-score call sites use them).

2. **Faster largest-blob.** Replace the pure-Python flood fill body of
   `_largest_blob_pixels` with `scipy.ndimage.label` when scipy is available,
   keeping the current implementation as fallback:

   ```python
   try:
       from scipy import ndimage as _ndimage
   except ImportError:
       _ndimage = None

   def _largest_blob_pixels(changed: np.ndarray) -> int:
       if _ndimage is not None:
           labels, n = _ndimage.label(changed)  # default structure = 4-connectivity
           if n == 0:
               return 0
           return int(np.bincount(labels.ravel())[1:].max())
       ...existing python implementation...
   ```

   Add `scipy>=1.11` to `[project] dependencies` in `pyproject.toml` and
   reinstall. The Phase 2 blob tests must pass with scipy present AND with the
   fallback (test both: monkeypatch `sort_cycle._ndimage = None` in one test).

3. **Deduplicate the WAIT_CLEAR entry ritual.** The block

   ```python
   with self._lock:            # (after Phase 3: plain assignments)
       self.state = SortState.WAIT_CLEAR
       self._empty_streak = 0
       self._wait_peak_occupancy = max(self._wait_peak_occupancy, occupied_score)
       self._reset_motion()
   ```

   appears 4× across `_handle_captured` / `_handle_matching`. Extract
   `def _enter_wait_clear(self, occupied_score: float) -> None` and call it.

4. **Share the gray/motion helpers with the app.** `app.py` has its own
   `_to_gray_small`, `_frame_motion_score`, and `STABILITY_COMPARE_SIZE ==
   COMPARE_SIZE`. Delete the app copies and import from
   `orchestrator.sort_cycle` (note the sort_cycle version resizes via
   `_normalize_frame` — equivalent for webcam uint8 frames; the app's motion
   score `_frame_motion_score(prev, curr)` is `np.mean(np.abs(a-b))` without a
   ROI crop, which equals `sort_cycle._motion_score` with a full-frame ROI —
   simplest is to export `_to_gray_small` and keep a 2-line
   `_frame_motion_score` in the app, or add a public
   `frame_motion_score(prev, curr)` in sort_cycle. Either is fine; pick one
   and delete the duplicate downscale code.)

**Verify:** pytest green (state-machine values unchanged); `build_app()` smoke.

---

## Phase 5 — SessionCatalog: stop re-reading every CSV on every scan

File: `src/demo_brickognize/session_catalog.py`.

**Problem.** After each scan, the UI refresh path
(`_after_scan_catalog_update` → `live_panel_html` → `list_sessions()` +
`read_scans()` ×3 + `current_session_label()` → `list_sessions()` again)
parses every `scans.csv` in every session folder. It runs inside the same
Gradio queue as the camera stream.

**Fix: mtime-keyed read cache.** In `SessionCatalog.__init__` add
`self._scans_cache: dict[str, tuple[float, list[ScanRecord]]] = {}`. In
`read_scans`:

```python
def read_scans(self, session_id):
    path = self._scans_path(session_id)
    if not path.exists():
        return []
    mtime = path.stat().st_mtime_ns
    cached = self._scans_cache.get(session_id)
    if cached and cached[0] == mtime:
        return cached[1]
    rows = ...existing parse...
    self._scans_cache[session_id] = (mtime, rows)
    return rows
```

Guard cache reads/writes with `self._lock` (note `log_scan` already holds
`self._lock` while appending — make sure you do not deadlock; the plain
`threading.Lock` there is not reentrant, so read/update the cache *outside*
the existing `with self._lock:` append block, or switch that lock to `RLock`).
After appending a row in `log_scan`, invalidate:
`self._scans_cache.pop(session_id, None)`.

Also fix the label lookup in `live_panel_html`: it calls `list_sessions()`
(full walk) just to find one session's label. Replace that loop with reading
the single session's `session.json` directly (fall back to
`self._current_label` as now).

**Tests to add** (`tests/test_session_catalog.py`): `read_scans` returns the
same list object on a second call (cache hit); after `log_scan` it returns the
new row (invalidation works); after an external append + mtime change it
re-reads.

**Verify:** pytest green; `build_app()` smoke.

---

## Phase 6 — app.py decomposition (largest phase; behavior-neutral)

File: `src/demo_brickognize/app.py` (1,845 lines). Split into focused modules.
Do this in the sub-steps below, verifying `build_app()` after each sub-step.

1. **Return a dataclass from `_identify_from_image`.** It currently returns an
   8-tuple; callers index `results[5]` / `results[4]`. Define in a new
   `src/demo_brickognize/identify.py`:

   ```python
   @dataclass
   class IdentifyView:
       match_panel: str
       alt_html: str
       status: str
       history_html: str
       api_calls: int
       annotated: np.ndarray | None = None
       candidate: Candidate | None = None
       color: ColorDetection | None = None
   ```

   Change `_identify_from_image` to return `IdentifyView` and update all three
   call sites (`_sort_identify`, `identify_piece`, `identify_live_frame`) to
   attribute access. `_sort_identify` then builds
   `orchestrator.sort_cycle.IdentifyOutcome` from the view (the two dataclasses
   stay separate on purpose — orchestrator must not import demo modules).
   This also collapses the three near-identical error-return blocks in
   `_identify_from_image` into one small helper that fills an `IdentifyView`
   with a message.

2. **`src/demo_brickognize/ui_theme.py`** — move `THEME_FACTORIES`,
   `DEFAULT_THEME_NAME`, `_THEME_CSS_CACHE`, `_theme_config_path`,
   `load_ui_theme_name`, `_save_ui_theme_name`, `_theme_style_block`,
   `apply_ui_theme`. Move the function-local `import json` statements to the
   top of the new module. `_theme_config_path` re-imports `REFERENCE_PATH`
   locally even though app.py imports it at the top — in the new module import
   it once at the top.

3. **`src/demo_brickognize/render.py`** — move the pure HTML builders:
   `_format_links`, `_format_candidate`, `_reference_html`,
   `_match_panel_html`, `_empty_match_panel`, `_html_table`,
   `_inventory_table_html`, `_scans_table_html`, `_format_valued_rows`,
   `_shape_only_summary`, `MATCH_PANEL_HEIGHT`. Make names public (drop the
   leading underscore) since they now cross a module boundary; update imports.

4. **Move `identify` machinery into `identify.py`** alongside `IdentifyView`:
   `_to_pil`, `_identify_from_image`, the history log (`HISTORY`,
   `_LAST_LOGGED_ID`, `_log_match`, `_log_match_shape_only`, `_history_html`)
   — wrap the history globals in a small `ScanHistory` class with `log(...)`
   and `html()` methods, instantiated once in `identify.py`. Also move the
   desk-mode motion-gating state (`_live_lock`, `_live_in_flight`,
   `_prev_gray`, `_stable_since`, `_armed`) plus `identify_live_frame` and
   `_reset_live_motion_state` into a `DeskScanner` class (one module-level
   instance). Keep behavior identical — this is a mechanical move of globals
   into instance attributes.

5. **Collapse the four identical catalog-refresh functions.**
   `_full_catalog_refresh`, `_initial_catalog_load`, `_start_new_session`,
   `_after_scan_catalog_update` all produce the same 6-tuple with a slightly
   different session-id choice. Replace with:

   ```python
   def _catalog_snapshot(*, cache_only: bool = True) -> tuple[...]:
       """Live panel, current label, dropdown, stats, valued table, scans table."""
       sid = CATALOG.current_session_id
       choice_list = CATALOG.session_picker_choices()
       session_ids = [s for _, s in choice_list]
       value = sid if sid in session_ids else (session_ids[0] if session_ids else None)
       stats, valued, scans = _catalog_state(value, cache_only=cache_only)
       return (_live_panel_update(cache_only=cache_only),
               CATALOG.current_session_label(),
               gr.Dropdown(choices=choice_list, value=value),
               stats, valued, scans)
   ```

   `_start_new_session` becomes `CATALOG.start_new_session()` followed by
   `return _catalog_snapshot()`. (Note `_initial_catalog_load` and
   `_after_scan_catalog_update` currently pass `value=sid` even when the
   session has no folder yet — `session_picker_choices()` already prepends the
   in-memory current session, so the unified `value` logic above is
   equivalent; verify by reading `session_picker_choices`.)

6. **What stays in `app.py`:** module constants (`MIN_SCORE`,
   `DEFAULT_SETTLE_TIME`, poll intervals, CSS/JS blocks), `SORT_CYCLE` +
   `_sort_identify`, the conveyor button handlers, ROI/reference handlers, the
   loop handlers, `stream_frame`, `build_app`, `main`. Move the stray
   `from .brickognize_client import ...` block (currently at line ~49, below
   `_refresh_roi_defaults`) up with the other top-of-file imports, and fold
   `_refresh_roi_defaults`'s global-mutating pattern into locals: `build_app`
   already calls it and uses the returned value; the `DEFAULT_ROI_*` module
   globals are only read as slider defaults inside `build_app` — replace those
   reads with fields of the returned `saved_roi` and delete the four globals
   and the `global` statement.

7. Run `ruff check --fix src/demo_brickognize` and fix remaining warnings in
   touched files only.

**Target sizes** (sanity check, not hard limits): `app.py` ≤ ~900 lines;
`identify.py`, `render.py`, `ui_theme.py` each ≤ ~250.

**Verify:** pytest green; `build_app()` smoke; `rg "results\[" src` returns
nothing; launch check if a camera-less run is acceptable:
`timeout 15 .venv/bin/python run_brickognize_demo.py` should print a local URL
before the timeout kills it (Gradio launches without a camera; the camera is
only needed client-side).

---

## Phase 7 — Robot code dedupe

1. **Merge the two Z-move test scripts.** `run_robot_move_test.py` and
   `run_robot_homing_move_test.py` are identical except
   `prepare_for_motion(c, force_reference=True)`. Add
   `--reference` (store_true, help "Force encoder referencing first (use after
   power-on)") to `run_robot_move_test.py` via argparse, delete
   `run_robot_homing_move_test.py`, and update every mention:
   `rg -l "homing_move_test" README.md PLAN.md docs plans` and edit those
   lines to `run_robot_move_test.py --reference`.

2. **`move_and_wait_z` delegates.** In `src/robot/cri_session.py`,
   `move_and_wait_z(controller, dz, vel, timeout_s)` is a hand-rolled special
   case of `move_base_relative_and_wait`. Reimplement as:

   ```python
   def move_and_wait_z(controller, dz, vel, timeout_s=45.0) -> bool:
       return move_base_relative_and_wait(
           controller, 0.0, 0.0, dz,
           vel_mm_s=vel, timeout_s=timeout_s, pos_tol_mm=0.5,
       )
   ```

   (Success tolerance 0.5 mm and timeout-fallback 2× = 1.0 mm match the
   original constants; the poll interval difference 0.15 s vs 0.2 s is
   immaterial.) Delete the old body.

3. **Teach CLI drives `TeachSession` instead of duplicating it.**
   `run_robot_teach.py` reimplements nudge/save/goto/test/park logic that also
   lives in `src/robot/teach_session.py`, so the two frontends can drift.
   Rewrite the REPL command handlers as thin calls:

   | command | TeachSession call |
   |---|---|
   | `status` | `print(SESSION.status().live)` + summary |
   | `list` | `status().summary` + `POSES_PATH` |
   | `stop` | `SESSION.stop()` |
   | `override <pct>` | `SESSION.set_override(pct)` |
   | `vel <pct>` | `SESSION.set_velocity(pct)` |
   | `nudge x\|y\|z ± [mm]` | `SESSION.nudge_cart(axis, sign, amount)` |
   | `nudge a1..a6 ± [deg]` | `SESSION.nudge_joint(axis, sign, amount)` |
   | `save home\|rest` | `SESSION.save_named(name)` |
   | `save bin <id> <kind>` | `SESSION.save_bin(id, kind)` |
   | `goto home\|rest` | `SESSION.goto_named(name)` |
   | `goto bin <id> <kind>` | `SESSION.goto_bin(id, kind)` |
   | `test dump <id>` | `SESSION.test_dump(id)` |
   | `park` | `SESSION.park()` then break |
   | `quit`/EOF | `SESSION.disconnect(park=True)` |

   Concretely: instantiate `session = TeachSession()`, call
   `session.connect()` at startup (print the returned status; exit nonzero if
   `not session.connected`), print `st.message` after each command, and keep
   the existing `finally:` park-on-exit safety behavior by calling
   `session.disconnect(park=True)` when the loop ends un-parked. Two behavior
   notes to preserve: (a) the current CLI's `vel` does NOT save poses.json but
   `TeachSession.set_velocity` DOES — that is an acceptable improvement, note
   it in the commit message; (b) `TeachSession` methods raise `RuntimeError`
   from `_require()` when disconnected — wrap the dispatch in the same
   `except (RuntimeError, KeyError, ValueError)` the CLI already has.
   Delete the now-unused direct imports (`move_base_relative_and_wait`, etc.)
   from the script.

4. **Counter races (comment only).** In `brickognize_client.py` and
   `bricklink_client.py` the call counters are incremented without consistent
   locking; they are cosmetic UI counters. Add a one-line comment at each
   counter declaration: `# best-effort counter; races are acceptable (UI display only)`.
   Do not add locking.

**Verify:** `python -c "import robot.cri_session, robot.teach_session, robot.dump"`;
pytest green; `python run_robot_teach.py --help 2>&1 | head -3` shows no
traceback (it will fail to connect without hardware — connection errors are
expected, import errors are not; do not let it actually retry/loop).

---

## Explicitly out of scope (do not do)

- Renaming the `robot`/`feeder`/`orchestrator` packages.
- Multi-browser-session support in the Gradio app (module-level `SORT_CYCLE`,
  `CATALOG`, `SESSION` singletons stay; the app is a one-operator rig).
- Any change to vision tuning constants or the WAIT_CLEAR heuristics.
- Touching firmware, hardware docs, or `plans/01–05`.
- Async/queueing changes to Gradio streaming (`concurrency_limit=1` stays).

## Progress tracking

Mark phases done here as you commit them:

- [x] Phase 1 — packaging & imports
- [x] Phase 2 — test harness
- [x] Phase 3 — bug fixes
- [x] Phase 4 — SortCycle perf/dedupe
- [x] Phase 5 — SessionCatalog cache
- [x] Phase 6 — app.py decomposition
- [x] Phase 7 — robot dedupe
