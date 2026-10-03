# Editor v2 verification — 2026-10-02

Implemented and checked in D:/tiktok_tool/news-tool.

- Backend regression set: 104 tests across test_editor_v2, test_editor,
  test_video, test_publishing and test_scene_audit passed (the initial full run
  found one upload-message regression, repaired and rerun; later renderer changes
  reran all 11 animation/transition cases and added the separate-volume test).
- Browser model: 7 Node tests passed.
- Real FFmpeg fixtures cover 8 video containers, 6 audio formats, 4 image formats,
  90-degree rotation/proxy, all six fit modes, animated opacity/scale/rotation,
  five transitions with exported pixel assertions, and content-based cut detection.
- UI on an isolated test database: changed target to 1080 square, changed entrance
  to Slide Left, played media, added a keyframe, saved/reloaded, analyzed motion,
  applied Smart Crop, undid the crop and exported the saved composition.
- The sample export completed through the worker: H.264 + AAC, 1080×1080,
  30 FPS, 12.600 seconds, 3,488,772 bytes. Screenshot and exported frame inspected.
- Per-frame opacity commands replaced per-pixel expressions after the full-size
  export exposed excessive rendering cost. The final sample completes successfully.
- Fixed EXDEV publication between Compose tmp/video volumes using copy to a
  destination-volume staging file followed by atomic rename.
- Migration 0008 applied on the local service after a database backup under the
  ignored backups/ directory. Review and TEST gates were retained.

Artifacts: editor-v2-square.jpg, export-v2-square.mp4, export-v2-frame.png.
The synthetic colors and sine-wave audio are diagnostic media, not production content.

Limits: no Chinese ASR/translation or face detector; analysis accepts supplied
sentence boundaries and uses scene/silence plus motion-center crop. Browser/ASS
line wrapping can differ. Remove transitions before reordering or applying cuts.
