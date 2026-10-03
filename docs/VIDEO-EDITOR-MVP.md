# Video Editor — composition v2

## Workflow

Open `/videos`, select an approved script, generate TTS preview, and open the editor.
Import media, drag it to a compatible track, edit, save the draft, then export.
Existing script review, immutable output review, checksum and TEST publishing gates remain enforced.

Supported uploads: MP4, MOV, MKV, WEBM, AVI, M4V, TS; JPG, JPEG, PNG, WEBP,
GIF (animated GIF becomes video); MP3, WAV, M4A, AAC, OGG and FLAC.
The default upload limit is 500 MiB (`MAX_MEDIA_UPLOAD_MB`). A file must contain a
stream FFmpeg can decode; a corrupt or encrypted source cannot be repaired automatically.

Audit metadata is stored on MediaAsset, including codecs, container, display size,
rotation, duration, FPS, audio and timestamp warnings. Rotation and pixel aspect
ratio are applied before target comparison. Non-browser formats, high resolution,
high FPS, HEVC, rotation/timestamp issues and large videos receive a reusable
H.264/AAC preview proxy, bounded to 1280 pixels per side at 30 FPS. Originals are
retained and always used for final export. Import waits for proxy generation;
large sources can take several minutes. Silent video is valid and is reported as
missing audio, without fabricating a soundtrack. Re-import old incompatible assets
to generate proxies; existing asset metadata is backfilled when opened.

## Target and fit

Choose 1080×1920, 1920×1080, 1080×1080 or 1080×1350. All export at 30 FPS.
The safe-area guides for TikTok, Reels and Shorts are preview-only approximate
layout aids, not a platform guarantee. The project background is exported.

New clips compare source and target ratios. A mismatch defaults to Blur Background;
matching sources use Fill. Inspector supports Fit, Fill, Crop, Blur Background,
Smart Crop and Stretch. Stretch is never selected automatically. Fit supports a
color or imported image background; blur supports 20–40 px blur and darkening.
Manual crop takes priority over Auto Fit and target changes. Reset clears that
manual flag. Position, size, static scale/rotation and crop zoom remain editable.

## Timeline and animation

Six initial tracks support video, overlay, original audio, voice, subtitles and text.
Play, seek, split, trim, delete, reorder, overlay, canvas resize/crop, import audio,
add text/subtitles and undo/redo remain available. Reorder preserves source ranges.
Deleting leaves a gap. Within-track overlap is permitted only for a valid transition.
Remove transitions before reordering a track or applying automatic cuts. Split
outside the transition overlap. Use the second video track for independent overlays.

Animation In/Out: Fade, four Slide directions, Zoom and Pop; text also has Typewriter.
Motion: Static, Slow Zoom In/Out and Pan in four directions. Keyframes expose
x/y, relative scale, rotation offset, opacity and volume, with Linear, Ease In,
Ease Out and Ease In Out. Split preserves animation time offsets so motion does
not restart. Animation scale factors are capped at 1.35, rotation keyframe offsets
at ±10°, entrance/exit durations at 0.8 s, transitions at 1 s. Static manual
transforms retain the original editor's wider range.

Adjacent video clips offer Crossfade, Fade Black, Slide, Push and Zoom. Adding a
transition overlaps the pair and shifts later clips on that video track. Other
tracks keep their times: check voice/subtitle sync after this intentional duration
change. Transition audio crossfades source audio when present. Triple overlaps
are rejected. NONE/SUBTLE/MODERN/STORY/NEWS/DRAMA presets apply bounded motions,
cycling image zoom/pan variants. STORY also adds 0.2 s crossfades where possible.
New news compositions use auto-fit, image motion, text fade and subtitle fade.

Subtitle options: Static, Fade, Pop and Word Highlight. Word timings can be edited
individually; “Ước lượng mốc từ” evenly estimates timings and is explicitly not ASR.
Text/ASS use bundled DejaVu fonts. Browser wrapping and libass shaping can differ
slightly. ASS control characters are escaped safely.

Shortcuts: Space play/pause; S split; Delete remove; Ctrl+Z undo; Ctrl+Y or
Ctrl+Shift+Z redo; arrows one frame (Shift: one second); Ctrl +/- timeline zoom;
Ctrl+S save. Undo retains 100 browser-session operations.

## Content analysis foundation

“Phân tích crop / scene / silence” scans source content and offers separate apply
buttons. Scene detection samples at 5 FPS (about 200 ms precision); silence detection
uses -35 dB for 300 ms. Nearby candidates merge, preferring supplied sentence
boundaries, then visual cuts, then silence. There are no fixed-length automatic cuts.
`POST /assets/{id}/analysis` accepts optional `sentence_boundaries` from a future
ASR service. Results without supplied boundaries are cached by target size.

Smart Crop tracks the center of frame differences, smooths movement, converts it
to crop alignment, and stores keyframes. Sampling is bounded to 300 points over
sources up to two hours. It falls back to center and never overrides manual crop.
This MVP does not detect faces or recognize semantic subjects. Chinese ASR,
translation, sentence extraction and end-to-end Chinese-to-Vietnamese auto-edit
are not implemented; the analysis endpoint is their integration point.

## Persistence and renderer

Migration 0008 adds MediaAsset probe/proxy fields and allows audio assets.
Composition `version` and `schema_version` are 2. Legacy scenes and v1 compositions
adapt on read. Existing rendered files are preserved. Unsupported future versions
are rejected. Transform x/y are offsets from the canvas center; width/height are
canvas-relative. Source end is exclusive and source length equals duration × speed.

Drafts live in the successful source job's checkpoint with revision locking;
stale tabs receive 409. Export snapshots are immutable. Re-render uses that snapshot.
Browser preview uses proxy media, CSS transforms and a nearby-playhead buffer of
±2 seconds. It does not invoke FFmpeg while playing or adjusting controls. Media
outside the buffer is released. Explicit import/content analysis does invoke FFmpeg.

Export compiles original-source trim, fit, crop, dynamic transforms, alpha,
transitions, audio gain/fades and timed ASS text into H.264/AAC MP4. A fixed transparent
bounding box keeps alpha processing stable while zoom changes layer dimensions.
There are limits of 16 tracks, 500 clips and two hours per composition. Large
multi-layer exports can still be CPU/memory intensive. Waveforms, masks, speed
curves and semantic auto-edit remain future work.

## Verification

- `node --test tests/test_editor_model.cjs`: editing operations, animation timing,
  split continuity, transitions, fit and controlled presets.
- `pytest tests/test_editor_v2.py tests/test_editor.py tests/test_video.py tests/test_publishing.py tests/test_scene_audit.py`
  in an isolated database ending `_test`: formats/proxies/original preservation,
  orientation, target/version validation, real animated FFmpeg output, transition
  pixels, content-based cuts, draft conflicts, immutable exports and review gates.
- `scripts/editor_preview.py` starts a synthetic editor fixture and refuses to use
  a non-test database. QA artifacts are stored under `results/editor-qa/`.
