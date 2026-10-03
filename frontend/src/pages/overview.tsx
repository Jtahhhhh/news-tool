import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  ArrowRight,
  Newspaper,
  FileText,
  Film,
  Send,
  AlertCircle,
  Layers,
  CheckCircle2,
  Activity,
  Sparkles,
  Radio,
  CheckCheck,
} from "lucide-react";
import { useApi } from "../hooks/use-api";
import { Panel, Feedback, Status } from "../components/shared";
import type { Job } from "../types";
import { PipelineStates } from "../components/pipeline-states";
const metrics = [
  {
    key: "articles_today",
    label: "Articles today (UTC)",
    note: "Fresh stories to explore",
    icon: Newspaper,
    to: "/news",
    tone: "teal",
  },
  {
    key: "scripts_generated",
    label: "Scripts generated",
    note: "Stories taking shape",
    icon: FileText,
    to: "/scripts",
    tone: "violet",
  },
  {
    key: "videos_rendered",
    label: "Videos rendered",
    note: "Ready for a closer look",
    icon: Film,
    to: "/videos",
    tone: "blue",
  },
  {
    key: "published",
    label: "Published",
    note: "Confirmed by TikTok",
    icon: Send,
    to: "/publish",
    tone: "green",
  },
  {
    key: "failed_jobs",
    label: "Failed jobs",
    note: "Review & get back on track",
    icon: AlertCircle,
    to: "/jobs",
    tone: "amber",
  },
  {
    key: "queue_size",
    label: "Queue size",
    note: "Work currently in motion",
    icon: Layers,
    to: "/jobs",
    tone: "slate",
  },
] as const;
const stageIcons = [Newspaper, CheckCheck, FileText, CheckCircle2, Film, Send];
export function Overview() {
  const summary = useApi<Record<string, number>>("/api/dashboard/summary");
  const pipeline = useApi<{ stage: string; count: number }[]>(
    "/api/dashboard/pipeline",
  );
  const activity = useApi<Job[]>("/api/dashboard/activity");
  const health = useApi<{ services: Record<string, string> }>(
    "/api/health/services",
    30000,
  );
  const failed = summary.data?.failed_jobs;
  return (
    <div className="overview">
      <div className="page-heading overview-heading">
        <div>
          <div className="eyebrow">
            <span /> THE EDITORIAL WORKSPACE
          </div>
          <h1>
            Your stories. <span>In motion.</span>
          </h1>
          <p>
            A little inspiration. A clear workflow. Your next great story starts
            here.
          </p>
        </div>
        <Link className="primary-link" to="/news">
          <Newspaper size={17} /> Explore news <ArrowUpRight size={17} />
        </Link>
      </div>
      <section className="studio-banner" aria-label="Workspace introduction">
        <div className="banner-copy">
          <span className="banner-label">
            <Sparkles size={14} /> FROM HEADLINE TO SPOTLIGHT
          </span>
          <h2>
            Make something
            <br />
            worth watching.
          </h2>
          <p>
            Find a story, shape your script, and bring it to life.
            <br className="desktop-break" /> Every step, together in one
            workspace.
          </p>
          <Link to="/scripts">
            Continue to your scripts <ArrowRight size={16} />
          </Link>
        </div>
        <div className="story-art" aria-hidden="true">
          <div className="art-orbit orbit-one" />
          <div className="art-orbit orbit-two" />
          <div className="art-card art-script">
            <span>
              <FileText size={15} /> THE STORY
            </span>
            <i />
            <i />
            <i />
            <div className="art-lines" />
          </div>
          <div className="art-card art-video">
            <div className="art-video-top">
              <span>STUDIO / 01</span>
              <Radio size={13} />
            </div>
            <div className="art-play">
              <Film size={30} strokeWidth={1.4} />
            </div>
            <div className="art-video-bottom">
              <span>
                Ready for your
                <br />
                <b>next idea.</b>
              </span>
              <ArrowUpRight size={25} />
            </div>
          </div>
          <div className="art-tag">
            <CheckCircle2 size={15} /> Create. Review. Publish.
          </div>
        </div>
      </section>
      <div className="section-heading">
        <div>
          <h2>Workspace overview</h2>
          <span>Your content, at a glance</span>
        </div>
        <span className="live-label">
          <span className="dot online" />
          Updates every 20s
        </span>
      </div>
      <Feedback
        loading={summary.isPending}
        error={summary.error}
        retry={() => summary.refetch()}
      />
      {summary.data && (
        <div className="kpis">
          {metrics.map(({ key, label, note, icon: Icon, to, tone }) => (
            <Link to={to} className={"kpi tone-" + tone} key={key}>
              <div className="kpi-top">
                <span className="metric-icon">
                  <Icon size={18} />
                </span>
                <ArrowUpRight className="metric-arrow" size={15} />
              </div>
              <span>{label}</span>
              <strong>{(summary.data[key] ?? 0).toLocaleString()}</strong>
              <small>{note}</small>
            </Link>
          ))}
        </div>
      )}
      <section className="panel pipeline-panel">
        <div className="section-heading">
          <div>
            <h2>Content pipeline</h2>
            <span>A clear path from discovery to publication</span>
          </div>
          <span className="subtle-label">6 STAGES</span>
        </div>
        <Feedback
          loading={pipeline.isPending}
          error={pipeline.error}
          retry={() => pipeline.refetch()}
        />
        <div className="funnel">
          {pipeline.data?.map((s, i) => {
            const Icon = stageIcons[i] || Layers;
            return (
              <div key={s.stage} className={s.count > 0 ? "has-content" : ""}>
                <div className="stage-top">
                  <span className="stage-icon">
                    <Icon size={19} />
                  </span>
                  <span className="step">0{i + 1}</span>
                </div>
                <strong>{s.count}</strong>
                <span>{s.stage}</span>
                {i < 5 && <ArrowRight className="stage-arrow" size={14} />}
              </div>
            );
          })}
        </div>
        <div className="pipeline-note">
          Stage totals include saved script and video versions.
        </div>
      </section>
      <div className="overview-bottom">
        <div>
          <Panel title="Recent activity">
            <Feedback
              loading={activity.isPending}
              error={activity.error}
              retry={() => activity.refetch()}
            />
            {activity.data?.length === 0 ? (
              <div className="activity-empty">
                <span className="empty-orbit">
                  <Activity size={25} />
                </span>
                <h3>A fresh start, a new story.</h3>
                <p>
                  Your latest activity will appear here.
                  <br />
                  Start by finding a story you want to tell.
                </p>
                <Link className="text-link" to="/news">
                  Discover your first story <ArrowRight size={15} />
                </Link>
              </div>
            ) : (
              activity.data?.slice(0, 8).map((j) => (
                <div className="activity" key={j.job_type + j.id}>
                  <div className="activity-icon">
                    <Activity size={17} />
                  </div>
                  <div>
                    <Link to="/jobs">
                      {j.job_type} <span className="muted-id">#{j.id}</span>
                    </Link>
                    <small>
                      {new Date(j.created_at).toLocaleString("vi-VN")}
                    </small>
                  </div>
                  <Status value={j.status} />
                </div>
              ))
            )}
            {Boolean(activity.data?.length) && (
              <Link className="activity-all text-link" to="/jobs">
                View all activity <ArrowRight size={15} />
              </Link>
            )}
          </Panel>
          <PipelineStates />
        </div>
        <div>
          <section
            className={"attention-card " + (failed === 0 ? "all-clear" : "")}
          >
            <div className="attention-top">
              <span className="attention-icon">
                {failed === 0 ? (
                  <CheckCircle2 size={21} />
                ) : (
                  <AlertCircle size={21} />
                )}
              </span>
              <span>{failed === 0 ? "ALL CLEAR" : "NEEDS ATTENTION"}</span>
              <strong>{failed ?? "—"}</strong>
            </div>
            <h3>
              {failed === 0
                ? "A little peace of mind."
                : "Let’s get things moving."}
            </h3>
            <p>
              {failed === 0
                ? "No failed jobs to review right now. You’re ready to focus on your next story."
                : "Some jobs need a closer look. Review the details and pick up where you left off."}
            </p>
            <Link to="/jobs">
              Open job monitor <ArrowUpRight size={16} />
            </Link>
          </section>
          <Panel title="System health">
            <Feedback
              loading={health.isPending}
              error={health.error}
              retry={() => health.refetch()}
            />
            {Object.entries(health.data?.services || {}).map(
              ([name, status]) => (
                <div className="health-row" key={name}>
                  <span>
                    <span
                      className={
                        "service-dot " + (status === "ok" ? "is-ok" : "")
                      }
                    />
                    {name}
                  </span>
                  <Status value={status} />
                </div>
              ),
            )}
            <div className="health-footer">
              <Radio size={12} /> Service checks refresh every 30s
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
