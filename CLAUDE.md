# Meeting Recorder — Claude Code Context

## What this project does
Automatically records meeting audio (app + mic), screen, transcribes with Whisper large-v3,
diarizes speakers with pyannote, generates AI summaries, and uploads to Google Drive.
Runs as a Windows system-tray app (`launch.pyw`).

## Key files
- `meeting_recorder/app.py` — top-level orchestrator (tray, hotkeys, recording lifecycle)
- `meeting_recorder/config.py` — all settings; split config (see below)
- `meeting_recorder/audio/capture_manager.py` — coordinates app audio + mic + screen capture threads
- `meeting_recorder/audio/app_audio.py` — per-process audio via ProcTap (WASAPI loopback)
- `meeting_recorder/audio/mic_audio.py` — mic capture via PyAudioWPatch + Silero VAD
- `meeting_recorder/audio/resampling.py` — resample to 16kHz mono int16; NoiseGate class
- `meeting_recorder/video/screen_capture.py` — window capture via PrintWindow API (falls back to mss)
- `meeting_recorder/video/window_finder.py` — Win32 EnumWindows helpers
- `meeting_recorder/ui/dashboard.py` — floating overlay (Tkinter GameBarDashboard)
- `meeting_recorder/transcription/` — faster-whisper pipeline + pyannote diarization
- `meeting_recorder/storage/action_items.py` — extract action items from transcripts
- `meeting_recorder/storage/auto_tag.py` — topic-based tag suggestions
- `meeting_recorder/storage/merge.py` — merge multiple recordings
- `meeting_recorder/storage/comparison.py` — compare recordings, find similar ones
- `meeting_recorder/storage/html_export.py` — self-contained HTML reports
- `meeting_recorder/storage/highlights.py` — transcript highlighting and annotations
- `meeting_recorder/storage/recurring.py` — recurring meeting detection and trends
- `meeting_recorder/storage/followups.py` — cross-recording follow-up tracker
- `meeting_recorder/storage/digest.py` — daily/weekly meeting digests
- `meeting_recorder/storage/speaker_analytics.py` — per-speaker talk time, WPM, cross-talk detection
- `meeting_recorder/storage/insights.py` — meeting insights engine (trends, collaboration, issues)
- `meeting_recorder/storage/meeting_cost.py` — meeting cost estimation (duration × attendees × rate)
- `meeting_recorder/storage/focus_time.py` — focus vs meeting time analysis
- `meeting_recorder/storage/bookmarks.py` — named timestamps within recordings
- `meeting_recorder/storage/csv_export.py` — CSV data export for spreadsheets/BI tools
- `meeting_recorder/storage/topic_trends.py` — keyword frequency trends across weeks
- `meeting_recorder/storage/streaks.py` — recording consistency and habit tracking
- `meeting_recorder/storage/heatmap.py` — day×time meeting density heatmap
- `meeting_recorder/storage/note_templates.py` — pre-formatted meeting note templates
- `meeting_recorder/storage/collaboration.py` — attendee co-occurrence analysis
- `meeting_recorder/storage/summary_diff.py` — compare summaries across recurring meetings
- `meeting_recorder/storage/sentiment.py` — keyword-based sentiment analysis for transcripts
- `meeting_recorder/storage/duration_predict.py` — meeting duration prediction and anomaly detection
- `meeting_recorder/storage/meeting_prep.py` — meeting preparation sheet generator
- `meeting_recorder/storage/word_frequency.py` — word frequency analysis with per-speaker terms
- `meeting_recorder/storage/participation.py` — participation equity scoring (Gini coefficient)
- `meeting_recorder/storage/meeting_roi.py` — meeting ROI calculator with recommendations
- `meeting_recorder/storage/efficiency_trend.py` — meeting efficiency trend tracking across weeks
- `meeting_recorder/storage/error_classifier.py` — error categorization with fix suggestions
- `meeting_recorder/storage/archive.py` — compress old recordings to save disk space
- `meeting_recorder/storage/transcript_export.py` — SRT/VTT/TXT transcript export
- `meeting_recorder/storage/health_summary.py` — recording health scoring with issue detection
- `meeting_recorder/storage/weekly_report.py` — weekly meeting report generator
- `meeting_recorder/storage/cost_budget.py` — weekly cost tracking, budget alerts, trends
- `meeting_recorder/storage/agenda_extract.py` — transcript agenda extraction via vocabulary shift
- `meeting_recorder/storage/effectiveness.py` — cross-recording effectiveness analysis
- `meeting_recorder/storage/duration_optimizer.py` — meeting duration optimizer with schedule suggestions
- `meeting_recorder/storage/quick_summary.py` — compact shareable meeting summary cards
- `meeting_recorder/storage/decision_log.py` — extract decisions from transcripts (distinct from action items)
- `meeting_recorder/storage/meeting_classifier.py` — auto-classify meetings into 10 types
- `meeting_recorder/storage/daily_summary.py` — daily meeting overview with timeline
- `meeting_recorder/storage/talk_balance.py` — entropy-based talk-time balance scoring
- `meeting_recorder/storage/transcript_search.py` — full-text search across all transcripts
- `meeting_recorder/storage/engagement_score.py` — composite 0-100 engagement score
- `meeting_recorder/storage/keyword_alerts.py` — watched keyword monitoring and alerting
- `meeting_recorder/storage/time_patterns.py` — meeting time-of-day distribution analysis
- `meeting_recorder/stats_cli.py` — CLI stats command
- `meeting_recorder/search/cli.py` — CLI search command
- `SETUP.md` — full install guide for a new Windows machine

## Audio pipeline
```
ProcTap (48kHz stereo float32)  →  resample_to_16khz_mono  →  NoiseGate  →  RingBuffer  →  WAV writer
PyAudioWPatch mic (44.1kHz 2ch) →  resample_to_16khz_mono  →  Silero VAD →  RingBuffer  →  WAV writer
```
- Silero VAD needs **exactly 512 samples at 16kHz** per chunk
- `proctap.ProcessAudioCapture` is the correct class (not `ProcTap`)
- Mute sync hooks **Alt+A** (Zoom) and **Ctrl+Shift+M** (Teams) keyboard shortcuts
- Mute sync starts **UNMUTED** by default — starting muted silences all mic audio

## Screen capture
- Uses Win32 `PrintWindow` API first (captures only the window, no overlays)
- Falls back to `mss` region grab if PrintWindow returns a blank frame
- Teams / Zoom often have multiple processes; the meeting window may be a different PID
  than the one `process_finder` selects — use the **⊙ Window** picker in the dashboard
  to switch both screen and audio capture to the correct window mid-recording
- `switch_screen_window(hwnd)` hot-swaps both screen capture AND audio PID atomically

## Record Anything mode
- If no meeting app (Zoom/Teams/Webex) is detected, `start_recording()` opens a window picker
- "Record Window..." tray menu item always opens the picker regardless of meeting app state
- Selected window uses `app_key="manual"` — disables mute sync, skips Outlook calendar
- Audio: tries ProcTap per-process first, auto-switches to desktop audio if silent for 3 seconds
- `_silence_auto_switch()` thread in capture_manager.py handles the detection
- Health warnings (silence, volume, write errors) surfaced via `_on_health_warning(key)` callback

## Thread safety
- `_transcriber_lock` in capture_manager.py protects `_live_transcriber` (writer loop + stop)
- `_metadata_lock` in app.py protects all `metadata.save()` calls
- Ring buffer tracks overflow count, logs warning every 100 drops
- Writer thread sets `_write_error` flag and fires health warning on exception

## Pause/Resume
- `Ctrl+Shift+P` (configurable) toggles pause during recording
- Paused state: audio capture continues but data is discarded (not written to WAV)
- Screen capture still grabs frames for live preview but doesn't write to video
- `CaptureManager.pause()` / `resume()` / `toggle_pause()` / `is_paused`
- `_pause_lock` in capture_manager.py protects pause state transitions
- `elapsed_seconds` excludes paused duration via `_total_paused_seconds` tracking
- Dashboard shows amber "⏸ PAUSED" indicator and pause button toggles play/pause icon
- Tray menu "Pause / Resume" item, enabled only during recording

## Voice profiles
- `VoiceProfileDB` in `transcription/voice_profiles.py` stores speaker embeddings in SQLite
- Auto-enrolled during transcription when diarization identifies speakers
- Cross-meeting speaker identification via cosine similarity (threshold 0.75)
- Settings > Speakers tab: list, rename, delete voice profiles
- `list_profiles_detailed()` returns name, sample_count, timestamps
- `rename_profile()` renames with conflict detection

## Re-process recording
- `reprocess_recording(path)` in app.py re-runs transcription + summary on existing audio
- Available via "Re-process" button in recording detail view and right-click context menu
- `reprocess_all_failed()` batch re-processes all recordings with status "error"
- Uses current config (not original recording's config) — useful after switching backends
- Guards against concurrent re-processing (checks if post-processing thread is alive)

## Recording quality scoring
- `storage/quality.py`: audio, transcript, video quality scores (0-100)
- Runs in parallel during post-processing; stored in `metadata.quality_scores`
- Displayed on history cards (colored indicator) and in detail view (Quality section)

## Cross-recording analytics
- `ui/stats_window.py`: total recordings, time, weekly trends, top speakers, platform usage
- `ui/timeline_view.py`: per-recording speaker timeline visualization
- `ui/voice_profiles_window.py`: manage voice profiles (rename, delete)
- `ui/speaker_editor.py`: rename diarized speakers, saves to metadata + transcript.txt

## Recording tags
- User-defined tags stored in `metadata.tags` list
- Inline add/remove in detail view, displayed as pills on history cards
- Tags are searchable via the inline filter bar

## Search index
- SQLite FTS5 index in `~/.meeting_recorder/recordings.db`
- Auto-syncs on startup via background thread (`_sync_search_index`)
- `RecordingIndex.sync()` adds new recordings, removes deleted ones
- Each post-processed recording indexed individually via `_index_recording`

## Known pitfalls
- `pythonw.exe` sets `sys.stdout/stderr = None` — redirect to devnull before `torch.hub.load`
- `torch.hub.load` deadlocks in background threads when pystray + keyboard hooks are active;
  pre-load the VAD model on the main thread before `pystray` starts
- `stop_recording()` is protected by `_stop_lock` to prevent double-invocation from concurrent
  callers (Stop button + process-exit auto-stop); do not remove this lock
- Teams calendar window (main PID) produces no audio — always use the meeting window PID

## Meeting auto-detection
- `recording.auto_start = true` enables background scanner thread
- `_meeting_scanner_loop()` polls every 5 seconds when idle
- Auto-starts recording when window score >= 50 (active meeting, not idle lobby)
- Toggleable from tray menu ("Auto-Record Meetings") — persists to config on toggle
- Scanner pauses during recording and resumes after post-processing completes
- Disk space check before recording: refuses at < 100 MB, warns at < 1 GB

## Recording retention
- `[retention]` config section: `enabled`, `max_age_days` (default 90), `max_total_gb` (default 0)
- `RecordingStore.cleanup()` deletes by age and/or total size, oldest-first
- Runs at app startup and after each post-processing completes
- Never deletes the active recording directory (`exclude` parameter)
- Disabled by default — user must set `retention.enabled = true`

## Import external audio
- `import_audio(file_path)` in app.py accepts WAV/MP3/M4A/OGG/FLAC files
- Non-WAV converted via pydub → app_audio.wav in new recording directory
- Runs the full post-processing pipeline (transcription, diarization, summary)
- Available from main window "Import Audio..." button and tray menu

## Main window UI features
- `ui/main_window.py` — full desktop GUI with idle and recording views
- `ui/diagnostics_window.py` — system health checks (GPU, audio, config)
- `ui/notification_center.py` — NotificationStore + NotificationWindow for system alerts
- `ui/calendar_view.py` — monthly calendar showing recording days, click to filter
- `ui/stats_window.py` — aggregate statistics across all recordings
- **Notification center**: bell icon in header, unread badge, stores warnings/info/errors
- **Calendar view**: monthly grid with green tint for recording days, navigation arrows
- **Diagnostics panel**: runs structured checks, shows pass/warn/fail with re-run button
- **Import audio**: file dialog or tray menu, converts non-WAV formats
- **Bulk operations**: Select mode in history, multi-select with checkboxes, action bar
  (Delete/Export/Re-process/Merge/Compare/Select All), confirmation dialog for batch delete
- **Recording merge**: combine 2+ selected recordings into one (transcripts, summaries, metadata)
- **Recording comparison**: select 2 recordings to see attendee/topic/tag diff (copies to clipboard)
- **Notes tab**: fourth tab in detail view for free-form notes (notes.md), auto-edit when empty
- **Error banner**: failed recordings show red banner with error message and Retry button
- **Play audio button**: opens mixed.wav/app_audio.wav/mic_audio.wav in system player
- **Config export/import**: buttons in Settings > Storage tab for multi-machine portability
- **Hotkeys tab**: all 4 global hotkeys editable in Settings, window shortcuts reference
- **HTML export**: self-contained HTML reports with dark theme, speaker stats, action items
- **Auto-tag suggestions**: topic-pattern matching + keyword extraction, wired into post-processing
- **Action items**: auto-extracts commitments, assignments, directives from transcripts (Actions tab)
- **Tag filter pills**: clickable tag pills above filter bar for quick filtering
- **Weekly activity heatmap**: Mon-Sun colored strip showing recording frequency
- **Similar recordings**: collapsible section in detail view showing related meetings
- **Stats enhancements**: collaborator frequency, time-of-day, day-of-week, common tags
- **Idle view analytics panels**: Today, Weekly Report (with week navigation), Follow-ups, Digest, Insights, Trends, Focus, Streaks, Costs, Heatmap, Effectiveness, Optimizer, Network, Balance, Alerts, Times, Prep
- **Agenda tab**: detail view tab showing extracted topic agenda from transcript (vocabulary shift detection)
- **Decisions tab**: detail view tab showing extracted decisions (distinct from action items)
- **Detail view analytics**: engagement score, meeting type classification, talk balance, sentiment, ROI — all in Details tab
- All analytics panels: centered overlay with close button, copy button, text display

## Config validation
- `Config.validate()` checks backend, provider, fps, vad threshold, retention, API keys, model size, device, speaker counts, output dir
- Warns when cloud/gemini backend selected but no API key, summary enabled without key, diarization without HF token
- Validates model_size against known Whisper models, device against cuda/cpu/auto
- Logs warnings for invalid values; does not raise
- Called automatically in `MeetingRecorderApp.run()` at startup

## Split config (multi-machine sync)
Config is split into two layers so non-secret settings sync via git:
- **`config.toml`** (repo root, git-tracked) — model choices, FPS, features, all non-secret settings
- **`~/.meeting_recorder/secrets.toml`** (local only, git-ignored) — API keys, tokens, mic device, dashboard position

`Config.load()` reads bundled config first, then overlays secrets.toml.
`Config.save()` splits: secrets → secrets.toml, everything else → repo config.toml.
On first run after upgrade, auto-migrates from legacy `~/.meeting_recorder/config.toml` → split files.

Secret/local fields: `transcription.openai_api_key`, `transcription.gemini_api_key`,
`diarization.huggingface_token`, `summary.api_key`, `audio.mic_device`,
`dashboard.position_x`, `dashboard.position_y`.

## Config defaults (current)
- `recording.auto_start = false` (manual window picker; auto-detect available via toggle)
- `transcription.model_size = "large-v3"` (base is too inaccurate)
- `diarization.enabled = true` (requires HuggingFace token + 3 gated model acceptances)
- `screen_recording.enabled = true`, `fps = 30`
- `retention.enabled = false`, `max_age_days = 90`, `max_total_gb = 0`
- Output: `~/MeetingRecordings/`

## Development
```bash
# Install
pip install -e ".[dev,audio,video,transcription,diarization,summary,search,integrations]"

# Run tests
python -m pytest tests/ -q

# Launch
python -m meeting_recorder        # console (shows logs)
pythonw launch.pyw                # background (tray only)
```

## CLI subcommands
```bash
python -m meeting_recorder diagnose       # system health checks
python -m meeting_recorder search <query> # search recordings (FTS5)
python -m meeting_recorder stats          # aggregate statistics (--json for raw)
python -m meeting_recorder stats --weekly # weekly meeting report (--week-offset N)
python -m meeting_recorder stats --health # recording health summary
python -m meeting_recorder stats --streaks # recording streaks and habits
python -m meeting_recorder stats --costs  # weekly cost tracking and budget
python -m meeting_recorder stats --effectiveness # meeting effectiveness analysis
python -m meeting_recorder stats --optimizer  # meeting duration optimizer suggestions
python -m meeting_recorder stats --sentiment  # sentiment analysis across recordings
python -m meeting_recorder stats --balance    # talk-time balance analysis
python -m meeting_recorder stats --alerts     # keyword watchlist alerts
python -m meeting_recorder stats --search "query"  # full-text transcript search
python -m meeting_recorder stats --all    # comprehensive report (all of the above)
python -m meeting_recorder archive [days] # compress old recordings (default: 30 days)
python -m meeting_recorder export-config  # export secrets for multi-machine
python -m meeting_recorder import-config <file>  # import secrets
```

## Recording archive
- `storage/archive.py`: compresses audio/video files into ZIP, keeps metadata/transcripts accessible
- `archive_recording()` / `unarchive_recording()` — per-recording operations
- `archive_old_recordings(dir, days)` — batch archive by age
- Archive/Unarchive button in detail view top bar
- CLI: `python -m meeting_recorder archive 30`

## Error classification
- `storage/error_classifier.py`: maps error_message strings to known categories
- Categories: audio, transcription, gpu, diarization, summary, network, storage, video
- Each classification includes title, explanation, fix suggestions, retryable flag
- Wired into detail view error banner — shows classified title and suggestions

## Transcript export
- `storage/transcript_export.py`: reads transcript.json, produces SRT/VTT/TXT
- Export button in detail view tab bar with format picker menu
- Saved to recording directory as transcript.srt/.vtt/.txt

## HTML export enhancements
- `storage/html_export.py` includes: sentiment, participation equity, meeting ROI, key terms
- Each section gracefully degrades if module/data unavailable (try/except)

## Dependencies requiring manual steps
- **PyTorch + CUDA**: `pip install torch --index-url https://download.pytorch.org/whl/cu121`
- **pyannote.audio**: pin to `3.4.0` (4.x has breaking API changes)
- **HuggingFace gated models**: must accept on hf.co before first run:
  - `pyannote/speaker-diarization-3.1`
  - `pyannote/segmentation-3.0`
  - `pyannote/wespeaker-voxceleb-resnet34-LM`
