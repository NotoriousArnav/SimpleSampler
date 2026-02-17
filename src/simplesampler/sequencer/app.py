"""
SimpleSampler Step Sequencer TUI.

Textual app with a step grid (rows = pads from bank, columns = steps),
cursor navigation, pattern switching, and playback with count-in.
"""

import argparse
import asyncio
import json
import os
import sys
import numpy as np

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Label, Static
from textual.containers import Horizontal, ScrollableContainer

from simplesampler.audio.playback import AudioPlayer
from simplesampler.schemas.config import Bank
from simplesampler.schemas.ss_config import SSConfig, load_config
from simplesampler.sequencer.schema import SequenceFile
from simplesampler.sequencer.engine import SequencerEngine

# 10 MB sample cache limit
MAX_PRELOAD_BYTES = 10 * 1024 * 1024

# Characters for step display
STEP_ON = "\u25a0"  # ■
STEP_OFF = "\u00b7"  # ·
CURSOR = "\u25a3"  # ▣


class StepCell(Static):
    """A single cell in the step grid."""

    def __init__(self, pad_id: int, step: int, **kwargs):
        super().__init__(STEP_OFF, **kwargs)
        self.pad_id = pad_id
        self.step = step
        self.active = False

    def toggle(self) -> bool:
        self.active = not self.active
        self._update_display()
        return self.active

    def set_active(self, active: bool):
        self.active = active
        self._update_display()

    def _update_display(self):
        self.update(STEP_ON if self.active else STEP_OFF)


class SequencerApp(App):
    CSS = """
    #status-bar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #grid-container {
        height: 1fr;
        padding: 0 1;
    }
    .pad-row {
        height: 1;
        layout: horizontal;
    }
    .pad-label {
        width: 14;
        height: 1;
        padding: 0 1 0 0;
    }
    .step-cell {
        width: 3;
        height: 1;
        text-align: center;
        content-align: center middle;
    }
    .step-cell.--cursor {
        background: $accent;
        text-style: bold;
    }
    .step-cell.--playhead {
        background: $warning 40%;
    }
    .step-cell.--cursor.--playhead {
        background: $accent;
        text-style: bold;
    }
    .step-cell.--active {
        color: $text;
    }
    .step-cell.--inactive {
        color: $text-muted;
    }
    .step-header {
        width: 3;
        height: 1;
        text-align: center;
        content-align: center middle;
        text-style: dim;
    }
    .header-pad-label {
        width: 14;
        height: 1;
    }
    #header-row {
        height: 1;
        layout: horizontal;
        padding: 0 1;
    }
    #help-bar {
        dock: bottom;
        height: 2;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("p", "toggle_play", "Play/Stop", show=True),
        Binding("space,enter", "toggle_step", "Toggle Step", show=True),
        Binding("equal,plus", "bpm_up", "BPM +5", show=False),
        Binding("minus", "bpm_down", "BPM -5", show=False),
        Binding("n", "add_pattern", "New Pattern", show=True),
        Binding("d", "delete_pattern", "Del Pattern", show=False),
        Binding("m", "toggle_metronome", "Metronome", show=True),
        Binding("s", "save_patterns", "Save", show=True),
        Binding("l", "load_patterns", "Load", show=False),
        Binding("q", "quit", "Quit", show=True),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("left", "cursor_left", "Left", show=False),
        Binding("right", "cursor_right", "Right", show=False),
        Binding("1", "switch_pattern_1", "Pattern 1", show=False),
        Binding("2", "switch_pattern_2", "Pattern 2", show=False),
        Binding("3", "switch_pattern_3", "Pattern 3", show=False),
        Binding("4", "switch_pattern_4", "Pattern 4", show=False),
        Binding("5", "switch_pattern_5", "Pattern 5", show=False),
        Binding("6", "switch_pattern_6", "Pattern 6", show=False),
        Binding("7", "switch_pattern_7", "Pattern 7", show=False),
        Binding("8", "switch_pattern_8", "Pattern 8", show=False),
        Binding("9", "switch_pattern_9", "Pattern 9", show=False),
        Binding("left_square_bracket", "prev_pattern", "Prev Pattern", show=False),
        Binding("right_square_bracket", "next_pattern", "Next Pattern", show=False),
    ]

    def __init__(
        self,
        bank_path: str,
        pattern_path: str | None = None,
        config_path: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bank_path = bank_path
        self.pattern_path = pattern_path

        # Load config
        self.config: SSConfig = load_config(config_path)
        seq_cfg = self.config.sequencer

        # Load bank
        self.bank = self._load_bank()
        self.pad_ids: list[int] = [p.id for p in self.bank.pads]
        self.pad_names: dict[int, str] = {p.id: p.name for p in self.bank.pads}
        self.pad_colors: dict[int, str] = {p.id: p.color for p in self.bank.pads}

        # Load or create sequence
        if pattern_path and os.path.isfile(pattern_path):
            self.sequence = SequenceFile.load(pattern_path)
        else:
            self.sequence = SequenceFile.create_default(
                bpm=seq_cfg.default_bpm,
                time_signature=seq_cfg.time_signature,
                steps_per_beat=seq_cfg.steps_per_beat,
                pattern_count=seq_cfg.pattern_count,
            )

        # Audio — larger blocksize than the sampler (1024 ≈ 23ms vs 256 ≈ 5.8ms).
        # The sequencer plays pre-programmed patterns so the extra latency is
        # imperceptible, but the bigger buffer gives far more GIL headroom and
        # eliminates the output-underflow crackling.
        self.audio = AudioPlayer(blocksize=1024)
        self.sample_cache: dict[int, np.ndarray] = {}
        self._preload_samples()

        # Load metronome click WAV if configured
        metro_click = None
        if self.config.metronome.sound and os.path.isfile(self.config.metronome.sound):
            metro_click = self.audio.load_wav(self.config.metronome.sound)

        # Engine
        self.engine = SequencerEngine(
            audio=self.audio,
            sequence=self.sequence,
            sample_cache=self.sample_cache,
            metronome_cfg=self.config.metronome,
            metronome_click=metro_click,
            on_step=self._on_step_callback,
            on_count_in_beat=self._on_count_in_callback,
            on_playback_start=self._on_playback_start_callback,
        )

        # Cursor position and previous position for targeted updates
        self._cursor_row = 0  # Index into self.pad_ids
        self._cursor_col = 0  # Step index
        self._prev_cursor_row = 0
        self._prev_cursor_col = 0

        # Playback head (for UI highlight)
        self._playhead: int = -1
        self._prev_playhead: int = -1

        # Pending pattern display
        self._pending_pattern: int | None = None

        # Cell lookup cache — populated on_mount to avoid query_one per tick
        self._cells: dict[tuple[int, int], StepCell] = {}  # (pad_id, step) -> cell
        self._status_label: Label | None = None  # cached status bar widget

    def _load_bank(self) -> Bank:
        try:
            with open(self.bank_path, "r") as f:
                data = json.load(f)
            return Bank(**data)
        except Exception as e:
            print(f"Error loading bank: {e}", file=sys.stderr)
            sys.exit(1)

    def _preload_samples(self):
        print("Preloading samples...", file=sys.stderr)
        total_size = 0
        for pad in self.bank.pads:
            if pad.sample_path and os.path.exists(pad.sample_path):
                try:
                    size = os.path.getsize(pad.sample_path)
                    if total_size + size <= MAX_PRELOAD_BYTES:
                        self.sample_cache[pad.id] = self.audio.load_wav(pad.sample_path)
                        total_size += size
                        print(
                            f"  Loaded: {pad.name} ({size / 1024:.1f} KB)",
                            file=sys.stderr,
                        )
                    else:
                        print(f"  Skipped: {pad.name} (cache full)", file=sys.stderr)
                except Exception as e:
                    print(f"  Error: {pad.sample_path}: {e}", file=sys.stderr)
        print(
            f"Preload complete: {total_size / 1024 / 1024:.2f} MB cached",
            file=sys.stderr,
        )

    # --- Compose UI ---

    def compose(self) -> ComposeResult:
        total_steps = self.sequence.total_steps

        yield Label(
            self._status_text(),
            id="status-bar",
        )

        # Header row with step numbers
        with Horizontal(id="header-row"):
            yield Static("", classes="header-pad-label")
            for s in range(total_steps):
                yield Static(str(s + 1), classes="step-header")

        # Grid rows
        with ScrollableContainer(id="grid-container"):
            for row_idx, pad_id in enumerate(self.pad_ids):
                with Horizontal(classes="pad-row"):
                    name = self.pad_names.get(pad_id, f"Pad {pad_id}")
                    # Truncate long names
                    if len(name) > 12:
                        name = name[:11] + "\u2026"
                    yield Static(name, classes="pad-label")
                    for s in range(total_steps):
                        cell = StepCell(
                            pad_id,
                            s,
                            classes="step-cell --inactive",
                            id=f"cell-{pad_id}-{s}",
                        )
                        yield cell

        yield Static(
            "[Space] Toggle  [P] Play/Stop  [+/-] BPM  "
            "[1-9] Pattern  [\\[/\\]] Prev/Next  [N] New  [D] Del  [M] Metro  [S] Save  [Q] Quit",
            id="help-bar",
        )

    def on_mount(self) -> None:
        """Load pattern data into grid, build cell cache, set initial cursor."""
        # Cache the status bar label
        self._status_label = self.query_one("#status-bar", Label)
        # Build cell lookup cache once — eliminates query_one during playback
        total_steps = self.sequence.total_steps
        for pad_id in self.pad_ids:
            for s in range(total_steps):
                try:
                    cell = self.query_one(f"#cell-{pad_id}-{s}", StepCell)
                    self._cells[(pad_id, s)] = cell
                except Exception:
                    pass
        self._sync_grid_from_sequence()
        self._update_cursor()

    # --- Status bar ---

    def _status_text(self) -> str:
        bpm = self.sequence.bpm
        sig = self.sequence.time_signature
        pat_name = self._current_pattern_name()
        pat_idx = self.sequence.active_pattern + 1
        pat_total = len(self.sequence.patterns)
        metro = "ON" if self.config.metronome.enabled else "OFF"
        playing = ""
        if self.engine.playing:
            playing = f"  \u25b6 Playing [step {self._playhead + 1}/{self.sequence.total_steps}]"
        elif self._playhead >= 0:
            playing = "  Count-in..."
        else:
            playing = "  \u25a0 Stopped"

        pending = ""
        if self._pending_pattern is not None:
            if 0 <= self._pending_pattern < len(self.sequence.patterns):
                pend_name = self.sequence.patterns[self._pending_pattern].name
                pending = f" \u2192 {pend_name}"
            else:
                self._pending_pattern = None

        return (
            f"BPM: {bpm} | {sig[0]}/{sig[1]} | "
            f"Pattern: {pat_name}{pending} [{pat_idx}/{pat_total}] | "
            f"Metro: {metro}{playing}"
        )

    def _refresh_status(self):
        if self._status_label is not None:
            self._status_label.update(self._status_text())

    def _current_pattern_name(self) -> str:
        idx = self.sequence.active_pattern
        if 0 <= idx < len(self.sequence.patterns):
            return self.sequence.patterns[idx].name
        return "?"

    # --- Cursor management ---

    def _update_cursor(self):
        """Update cursor highlight — only touches the old and new cursor cells."""
        # Remove cursor from old position
        old_pad = (
            self.pad_ids[self._prev_cursor_row]
            if self._prev_cursor_row < len(self.pad_ids)
            else None
        )
        if old_pad is not None:
            old_cell = self._cells.get((old_pad, self._prev_cursor_col))
            if old_cell is not None:
                old_cell.set_class(False, "--cursor")

        # Add cursor to new position
        new_pad = (
            self.pad_ids[self._cursor_row]
            if self._cursor_row < len(self.pad_ids)
            else None
        )
        if new_pad is not None:
            new_cell = self._cells.get((new_pad, self._cursor_col))
            if new_cell is not None:
                new_cell.set_class(True, "--cursor")

        # Track for next update
        self._prev_cursor_row = self._cursor_row
        self._prev_cursor_col = self._cursor_col

    def _move_playhead(self, new_step: int):
        """Move the playhead highlight from prev column to new column.

        Only touches cells in the two affected columns — O(num_pads) not O(num_pads * num_steps).
        """
        old = self._prev_playhead
        # Remove --playhead from old column
        if old >= 0:
            for pad_id in self.pad_ids:
                cell = self._cells.get((pad_id, old))
                if cell is not None:
                    cell.set_class(False, "--playhead")

        # Add --playhead to new column
        if new_step >= 0:
            for pad_id in self.pad_ids:
                cell = self._cells.get((pad_id, new_step))
                if cell is not None:
                    cell.set_class(True, "--playhead")

        self._prev_playhead = new_step

    def _clear_playhead(self):
        """Remove playhead highlight from the previous column."""
        if self._prev_playhead >= 0:
            for pad_id in self.pad_ids:
                cell = self._cells.get((pad_id, self._prev_playhead))
                if cell is not None:
                    cell.set_class(False, "--playhead")
        self._prev_playhead = -1

    def action_cursor_up(self):
        if self._cursor_row > 0:
            self._cursor_row -= 1
            self._update_cursor()

    def action_cursor_down(self):
        if self._cursor_row < len(self.pad_ids) - 1:
            self._cursor_row += 1
            self._update_cursor()

    def action_cursor_left(self):
        if self._cursor_col > 0:
            self._cursor_col -= 1
            self._update_cursor()

    def action_cursor_right(self):
        if self._cursor_col < self.sequence.total_steps - 1:
            self._cursor_col += 1
            self._update_cursor()

    # --- Step toggling ---

    def action_toggle_step(self):
        if not self.pad_ids:
            return
        pad_id = self.pad_ids[self._cursor_row]
        step = self._cursor_col
        pattern = self.sequence.patterns[self.sequence.active_pattern]
        pad_key = str(pad_id)

        # Ensure step list exists
        total = self.sequence.total_steps
        if pad_key not in pattern.steps:
            pattern.steps[pad_key] = [0] * total

        # Toggle
        current = pattern.steps[pad_key][step]
        pattern.steps[pad_key][step] = 0 if current else 1

        # Update cell via cache
        cell = self._cells.get((pad_id, step))
        if cell is not None:
            cell.set_active(pattern.steps[pad_key][step] == 1)
            cell.set_class(cell.active, "--active")
            cell.set_class(not cell.active, "--inactive")

    # --- Sync grid from sequence data ---

    def _sync_grid_from_sequence(self):
        """Update all grid cells to reflect the current pattern's step data."""
        pattern = self.sequence.patterns[self.sequence.active_pattern]
        total_steps = self.sequence.total_steps

        for pad_id in self.pad_ids:
            pad_key = str(pad_id)
            steps = pattern.steps.get(pad_key, [])
            for s in range(total_steps):
                cell = self._cells.get((pad_id, s))
                if cell is not None:
                    active = s < len(steps) and steps[s] == 1
                    cell.set_active(active)
                    cell.set_class(active, "--active")
                    cell.set_class(not active, "--inactive")

    # --- Playback ---

    def action_toggle_play(self):
        if self.engine.playing or self._playhead >= 0:
            self.engine.stop()
            self._playhead = -1
            self._pending_pattern = None
            self._clear_playhead()
            self._refresh_status()
        else:
            self._playhead = -2  # Sentinel: count-in started
            self._refresh_status()
            self.engine.start()

    def _on_step_callback(self, step: int):
        """Called from engine thread on each step.

        Posts UI work to the event loop without blocking the engine
        thread — critical for keeping audio timing tight.
        """
        self._playhead = step
        self._post_to_main(self._tick_ui, step)

    def _tick_ui(self, step: int):
        """Run on the main thread — move playhead and refresh status bar."""
        self._move_playhead(step)
        self._refresh_status()

    def _on_count_in_callback(self, beat: int):
        """Called from engine thread during count-in."""
        self._playhead = -2
        self._post_to_main(self._refresh_status)

    def _on_playback_start_callback(self):
        self._post_to_main(self._refresh_status)

    def _post_to_main(self, callback, *args):
        """Fire-and-forget: schedule callback on Textual's event loop.

        Unlike call_from_thread, this does NOT block the calling thread.
        Uses run_coroutine_threadsafe with Textual's app context so
        widget mutations (set_class, update) trigger proper repaints.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _run():
            with self._context():
                callback(*args)

        asyncio.run_coroutine_threadsafe(_run(), loop=loop)

    # --- BPM ---

    def action_bpm_up(self):
        self.sequence.bpm = min(300, self.sequence.bpm + 5)
        self._refresh_status()

    def action_bpm_down(self):
        self.sequence.bpm = max(20, self.sequence.bpm - 5)
        self._refresh_status()

    # --- Pattern switching ---

    def _switch_pattern(self, index: int):
        """Switch to pattern by 0-based index. During playback, queues for bar boundary."""
        if index < 0 or index >= len(self.sequence.patterns):
            return
        if self.engine.playing:
            self._pending_pattern = index
            self.engine.queue_pattern_switch(index)
            self._refresh_status()
        else:
            self.sequence.active_pattern = index
            self._pending_pattern = None
            self._sync_grid_from_sequence()
            self._update_cursor()
            self._refresh_status()

    def action_switch_pattern_1(self):
        self._switch_pattern(0)

    def action_switch_pattern_2(self):
        self._switch_pattern(1)

    def action_switch_pattern_3(self):
        self._switch_pattern(2)

    def action_switch_pattern_4(self):
        self._switch_pattern(3)

    def action_switch_pattern_5(self):
        self._switch_pattern(4)

    def action_switch_pattern_6(self):
        self._switch_pattern(5)

    def action_switch_pattern_7(self):
        self._switch_pattern(6)

    def action_switch_pattern_8(self):
        self._switch_pattern(7)

    def action_switch_pattern_9(self):
        self._switch_pattern(8)

    def action_prev_pattern(self):
        """Switch to previous pattern, wrapping around to the last."""
        count = len(self.sequence.patterns)
        if count <= 1:
            return
        current = (
            self.sequence.active_pattern
            if not self.engine.playing
            else (
                self._pending_pattern
                if self._pending_pattern is not None
                else self.sequence.active_pattern
            )
        )
        new_idx = (current - 1) % count
        self._switch_pattern(new_idx)

    def action_next_pattern(self):
        """Switch to next pattern, wrapping around to the first."""
        count = len(self.sequence.patterns)
        if count <= 1:
            return
        current = (
            self.sequence.active_pattern
            if not self.engine.playing
            else (
                self._pending_pattern
                if self._pending_pattern is not None
                else self.sequence.active_pattern
            )
        )
        new_idx = (current + 1) % count
        self._switch_pattern(new_idx)

    # --- Add / delete patterns ---

    def action_add_pattern(self):
        from simplesampler.sequencer.schema import Pattern, _pattern_names

        count = len(self.sequence.patterns)
        name = _pattern_names(count + 1)[-1]
        self.sequence.patterns.append(Pattern(name=name))
        self._refresh_status()

    def action_delete_pattern(self):
        if len(self.sequence.patterns) <= 1:
            return  # Can't delete the last pattern
        idx = self.sequence.active_pattern
        # Clear pending pattern references on both app and engine BEFORE
        # mutating the list — avoids the engine daemon thread applying a
        # stale index between the pop and the cleanup.
        self._pending_pattern = None
        self.engine._pending_pattern = None
        self.sequence.patterns.pop(idx)
        if self.sequence.active_pattern >= len(self.sequence.patterns):
            self.sequence.active_pattern = len(self.sequence.patterns) - 1
        self._sync_grid_from_sequence()
        self._update_cursor()
        self._refresh_status()

    # --- Metronome ---

    def action_toggle_metronome(self):
        self.config.metronome.enabled = not self.config.metronome.enabled
        self._refresh_status()

    # --- Save / Load ---

    def action_save_patterns(self):
        path = self.pattern_path or self._default_pattern_path()
        try:
            self.sequence.save(path)
            self.pattern_path = path
            self.notify(f"Saved: {path}", severity="information")
        except Exception as e:
            self.notify(f"Save failed: {e}", severity="error")

    def action_load_patterns(self):
        path = self.pattern_path or self._default_pattern_path()
        if not os.path.isfile(path):
            self.notify(f"File not found: {path}", severity="warning")
            return
        # Stop engine before swapping the sequence to avoid racing the
        # daemon thread which reads self.sequence on every step tick.
        was_playing = self.engine.playing
        if was_playing:
            self.engine.stop()
            self._playhead = -1
            self._pending_pattern = None
            self._clear_playhead()
        try:
            self.sequence = SequenceFile.load(path)
            self.engine.sequence = self.sequence
            self.engine._pending_pattern = None
            self._sync_grid_from_sequence()
            self._update_cursor()
            self._refresh_status()
            if was_playing:
                self.notify(
                    f"Loaded: {path} (playback stopped)", severity="information"
                )
            else:
                self.notify(f"Loaded: {path}", severity="information")
        except Exception as e:
            self.notify(f"Load failed: {e}", severity="error")

    def _default_pattern_path(self) -> str:
        base = os.path.splitext(self.bank_path)[0]
        return f"{base}_patterns.json"

    # --- Cleanup ---

    async def action_quit(self):
        self.engine.stop()
        self.audio.cleanup()
        self.exit()

    def on_unmount(self):
        self.engine.stop()
        self.audio.cleanup()


def main():
    parser = argparse.ArgumentParser(
        description="SimpleSampler Step Sequencer",
        prog="simplesampler-seq",
    )
    parser.add_argument("bank", help="Path to bank JSON file")
    parser.add_argument(
        "-p", "--pattern", help="Path to pattern JSON file", default=None
    )
    parser.add_argument("-c", "--config", help="Path to ss_config.toml", default=None)
    args = parser.parse_args()

    if not os.path.isfile(args.bank):
        print(f"Bank file not found: {args.bank}", file=sys.stderr)
        sys.exit(1)

    app = SequencerApp(
        bank_path=args.bank,
        pattern_path=args.pattern,
        config_path=args.config,
    )
    app.run()


if __name__ == "__main__":
    main()
