export type Page<T> = { items: T[]; total: number };
export type Article = {
  id: number;
  event_id: number;
  title: string;
  source: string;
  url: string;
  content: string;
  status: string;
  created_at: string;
  score: number;
};
export type Scene = {
  scene_id: number;
  seconds: number;
  narration: string;
  on_screen_text: string;
  visual_brief: string;
};
export type Script = {
  id: number;
  event_id: number;
  version: number;
  status: string;
  outcome: string;
  target_seconds?: number;
  data: {
    title: string;
    hook: string;
    scenes: Scene[];
    [key: string]: unknown;
  } | null;
};
export type Video = {
  id: number;
  script_version_id: number;
  status: string;
  duration_seconds: number;
  output_key: string;
  config: { width: number; height: number };
};
export type Job = {
  id: number;
  job_type: string;
  status: string;
  created_at: string;
  has_error: boolean;
};
