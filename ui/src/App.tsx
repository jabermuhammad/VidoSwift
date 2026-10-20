import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Page = "download" | "queue" | "history" | "settings";
type IconName = "home" | "queue" | "apps" | "history" | "settings" | "link" | "clipboard" | "close" | "play" | "folder" | "download" | "more" | "check" | "alert" | "clock" | "file" | "chevron" | "search" | "trash" | "external";
type FormatInfo = { format_id: string; ext: string; height?: number | null; resolution: string; note: string; filesize?: number | null; has_video: boolean; has_audio: boolean };
type VideoInfo = { id: string; title: string; thumbnail: string; duration: number | null; duration_text: string; extractor: string; uploader: string; view_count: number | null; formats: FormatInfo[]; heights: number[]; estimated_size: number | null; estimated_size_text: string; format_warning?: string };
type Job = { id: string; url: string; title: string; quality: string; format: string; folder: string; status: string; progress: number; speed: string; eta: string; filepath: string; error: string; created: number };
type SettingsData = { folder: string; quality: string; format: string; auto_start: boolean; keep_metadata: boolean; youtube_cookies_enabled: boolean; youtube_cookies_browser: string };
type AppState = { settings: SettingsData; jobs: Job[]; history: Job[] };
type NativeApi = {
  health: () => Promise<{ ok: boolean; status: string }>; quit_app: () => Promise<boolean>;
  minimize_window: () => Promise<boolean>; toggle_maximize_window: () => Promise<boolean>;
  get_state: () => Promise<AppState>; analyze: (url: string) => Promise<{ ok: boolean; video?: VideoInfo; error?: string }>;
  start_download: (request: Record<string, string>) => Promise<{ ok: boolean; id?: string; error?: string }>;
  choose_folder: () => Promise<string | null>; set_folder: (path: string) => Promise<string>;
  save_settings: (settings: Partial<SettingsData>) => Promise<SettingsData>; cancel: (id: string) => Promise<boolean>;
  retry: (id: string) => Promise<{ ok: boolean; error?: string }>; clear_history: () => Promise<boolean>;
  open_file: (path: string) => Promise<boolean>; open_folder: (path?: string) => Promise<boolean>; get_clipboard_text: () => Promise<string>;
};
declare global { interface Window { pywebview?: { api: NativeApi }; __vidoswiftPywebviewReady?: boolean; } }

function timedCall<T>(label: string, invoke: () => Promise<T>, timeoutMs = 10000): Promise<T> {
  let timer = 0;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = window.setTimeout(() => reject(new Error(`${label} timed out. Retry the connection.`)), timeoutMs);
  });
  return Promise.race([Promise.resolve().then(invoke), timeout]).finally(() => window.clearTimeout(timer));
}

function boundedApi(api: NativeApi): NativeApi {
  return {
    health: () => timedCall("Backend health check", () => api.health(), 3000),
    quit_app: () => timedCall("Exit", () => api.quit_app()),
    minimize_window: () => timedCall("Minimize window", () => api.minimize_window()),
    toggle_maximize_window: () => timedCall("Maximize window", () => api.toggle_maximize_window()),
    get_state: () => timedCall("Load application state", () => api.get_state(), 7000),
    analyze: url => timedCall("Video analysis", () => api.analyze(url), 30000),
    start_download: request => timedCall("Start download", () => api.start_download(request)),
    choose_folder: () => api.choose_folder(),
    set_folder: path => timedCall("Save download folder", () => api.set_folder(path)),
    save_settings: settings => timedCall("Save settings", () => api.save_settings(settings)),
    cancel: id => timedCall("Cancel download", () => api.cancel(id)),
    retry: id => timedCall("Retry download", () => api.retry(id)),
    clear_history: () => timedCall("Clear history", () => api.clear_history()),
    open_file: path => timedCall("Open downloaded file", () => api.open_file(path)),
    open_folder: path => timedCall("Open download folder", () => api.open_folder(path)),
    get_clipboard_text: () => timedCall("Read clipboard", () => api.get_clipboard_text()),
  };
}

function waitForBridge(timeoutMs = 20000): Promise<NativeApi> {
  return new Promise((resolve, reject) => {
    let timer = 0;
    let poll = 0;
    const cleanup = () => { window.removeEventListener("pywebviewready", check); window.clearTimeout(timer); window.clearInterval(poll); };
    const check = () => {
      const api = window.pywebview?.api;
      // Some WebView2 builds deliver the ready event before page scripts attach; the method bridge itself is also a safe signal.
      if (api && typeof api.health === "function") { cleanup(); resolve(api); }
    };
    window.addEventListener("pywebviewready", check);
    poll = window.setInterval(check, 200);
    timer = window.setTimeout(() => { cleanup(); reject(new Error("The desktop bridge did not become ready within 20 seconds.")); }, timeoutMs);
    check();
  });
}

const paths: Record<IconName, React.ReactNode> = {
  home: <><path d="M3 10.8 12 3l9 7.8" /><path d="M5.5 9.5V21h13V9.5M9 21v-7h6v7" /></>,
  queue: <><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></>,
  apps: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
  history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15.5a1.7 1.7 0 0 0 .3 1.8l-2.8 2.8a1.7 1.7 0 0 0-2-.3 1.7 1.7 0 0 0-1 1.6h-4a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-2 .3l-2.8-2.8a1.7 1.7 0 0 0 .3-2 1.7 1.7 0 0 0-1.6-1v-4a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-2l2.8-2.8a1.7 1.7 0 0 0 2 .3 1.7 1.7 0 0 0 1-1.6h4a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 2-.3l2.8 2.8a1.7 1.7 0 0 0-.3 2 1.7 1.7 0 0 0 1.6 1v4a1.7 1.7 0 0 0-1.6 1Z" /></>,
  link: <><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1" /><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1" /></>,
  clipboard: <><rect x="6" y="4" width="12" height="17" rx="2" /><path d="M9 4.5V3h6v1.5M9 9h6M9 13h6" /></>, close: <path d="m6 6 12 12M18 6 6 18" />,
  play: <path d="m9 7 8 5-8 5Z" />, folder: <path d="M3 6.5h7l2 2h9v10.8a1.7 1.7 0 0 1-1.7 1.7H4.7A1.7 1.7 0 0 1 3 19.3Z" />,
  download: <><path d="M12 3v12m0 0 4-4m-4 4-4-4M4 20h16" /></>, more: <><circle cx="5" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1" fill="currentColor" stroke="none" /></>,
  check: <path d="m5 12 4 4L19 6" />, alert: <><path d="M12 3 2.5 20h19Z" /><path d="M12 9v4m0 3h.01" /></>, clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>, file: <><path d="M6 2h8l4 4v16H6Z" /><path d="M14 2v5h5" /></>, chevron: <path d="m9 7 5 5-5 5" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>, trash: <><path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" /></>, external: <><path d="M14 4h6v6M20 4l-9 9" /><path d="M18 13v7H4V6h7" /></>,
};

function Icon({ name, size = 18 }: { name: IconName; size?: number }) { return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>; }
function WindowControls() {
  const perform = (action: "minimize" | "maximize" | "close") => {
    const api = window.pywebview?.api;
    if (!api) { if (action === "close") window.close(); return; }
    if (action === "minimize") void api.minimize_window().catch(() => {});
    else if (action === "maximize") void api.toggle_maximize_window().catch(() => {});
    else void api.quit_app().catch(() => window.close());
  };
  return <div className="window-controls">
    <button title="Minimize" aria-label="Minimize window" onClick={() => perform("minimize")}><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2 6h8" /></svg></button>
    <button title="Maximize or restore" aria-label="Maximize or restore window" onClick={() => perform("maximize")}><svg viewBox="0 0 12 12" aria-hidden="true"><rect x="2.25" y="2.25" width="7.5" height="7.5" /></svg></button>
    <button className="window-close" title="Close" aria-label="Close VidoSwift" onClick={() => perform("close")}><svg viewBox="0 0 12 12" aria-hidden="true"><path d="m2.5 2.5 7 7m0-7-7 7" /></svg></button>
  </div>;
}
function Button({ children, kind = "secondary", icon, onClick, disabled, className = "" }: { children: React.ReactNode; kind?: "primary" | "secondary" | "quiet" | "danger"; icon?: IconName; onClick?: () => void; disabled?: boolean; className?: string }) {
  return <button className={`button ${kind} ${className}`} onClick={onClick} disabled={disabled}>{icon && <Icon name={icon} size={16} />}<span>{children}</span></button>;
}
const nav: { id: Page; label: string; bn: string; icon: IconName }[] = [
  { id: "download", label: "Home / Download", bn: "ডাউনলোড", icon: "home" }, { id: "queue", label: "Download Queue", bn: "সক্রিয় কাজ", icon: "apps" },
  { id: "history", label: "History", bn: "ইতিহাস", icon: "history" }, { id: "settings", label: "Settings", bn: "সেটিংস", icon: "settings" },
];
function AppShell({ page, setPage, children, activeCount }: { page: Page; setPage: (p: Page) => void; children: React.ReactNode; activeCount: number }) {
  const current = nav.find(n => n.id === page);
  return <div className={`desktop ${page === "download" ? "home-shell" : ""}`}>
    <header className="titlebar" onDoubleClick={event => { if (!(event.target as HTMLElement).closest(".window-controls")) void window.pywebview?.api.toggle_maximize_window().catch(() => {}); }}><div className="titlebar-drag pywebview-drag-region"><div className="breadcrumb"><span>VidoSwift</span><Icon name="chevron" size={13} /><b>{current?.label}</b></div></div><WindowControls /></header>
    <div className="shell-body"><aside className="sidebar">
      <nav aria-label="Main navigation">{nav.map(item => <button key={item.id} className={`nav-item ${page === item.id ? "active" : ""}`} onClick={() => setPage(item.id)} title={`${item.label} · ${item.bn}`} aria-label={item.label} aria-current={page === item.id ? "page" : undefined}><Icon name={item.icon} /><span className="nav-copy"><b>{item.label}</b><small lang="bn">{item.bn}</small></span>{item.id === "queue" && activeCount > 0 && <em>{activeCount}</em>}</button>)}</nav>
      <div className="sidebar-bottom"><img src="./VidoSwift.ico" alt="VidoSwift" title="VidoSwift" /></div>
    </aside><div className="workspace">{page === "download" && <div className="workspace-meta"><span className="engine-label">Desktop app</span></div>}<main>{children}</main></div></div>
  </div>;
}
function PageHeading({ eyebrow, title, bangla, action }: { eyebrow: string; title: string; bangla: string; action?: React.ReactNode }) {
  return <div className="page-heading"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p lang="bn">{bangla}</p></div>{action}</div>;
}
function formatDuration(seconds: number | null | undefined) {
  if (seconds == null) return "Unavailable";
  const h = Math.floor(seconds / 3600); const m = Math.floor((seconds % 3600) / 60); const s = seconds % 60;
  return h ? `${h} hr ${m} min` : `${m} min ${s} sec`;
}
function Home({ api, settings, onStarted, onChangeFolder, onSaveSettings }: { api: NativeApi; settings: SettingsData; onStarted: () => void; onChangeFolder: () => void; onSaveSettings: (values: Partial<SettingsData>) => void }) {
  const [url, setUrl] = useState(""); const [video, setVideo] = useState<VideoInfo | null>(null); const [loading, setLoading] = useState(false); const [error, setError] = useState("");
  const [quality, setQuality] = useState(settings.quality); const [format, setFormat] = useState(settings.format);
  useEffect(() => { setQuality(settings.quality); setFormat(settings.format); }, [settings.quality, settings.format]);
  const formatOptions = useMemo(() => {
    if (!video) return ["mp4", "webm", "mkv"];
    const extensions = new Set(video.formats.map(item => item.ext));
    return [extensions.has("mp4") || extensions.has("m4a") ? "mp4" : "", extensions.has("webm") ? "webm" : "", "mkv"].filter(Boolean);
  }, [video]);
  const chosenFormat = quality === "audio" ? "mp3" : formatOptions.includes(format) ? format : formatOptions[0] || "mkv";
  const hasDownloadableFormats = Boolean(video?.formats.length);
  const analyze = async () => {
    if (!url.trim()) return;
    setLoading(true); setError(""); setVideo(null);
    let result: Awaited<ReturnType<NativeApi["analyze"]>>;
    try { result = await api.analyze(url.trim()); } catch (e) { setLoading(false); setError(String(e)); return; }
    setLoading(false);
    if (!result.ok || !result.video) { setError(result.error || "Could not analyze this URL."); return; }
    setVideo(result.video);
    if (settings.auto_start && result.video.formats.length) await begin(result.video);
  };
  const begin = async (selected = video) => {
    if (!selected || !selected.formats.length) return;
    const result = await api.start_download({ url, quality, format: chosenFormat, title: selected.title });
    if (!result.ok) { setError(result.error || "Could not add this download."); return; }
    onStarted();
  };
  const paste = async () => { try { setUrl(await api.get_clipboard_text()); setError(""); } catch { setError("Clipboard access is unavailable."); } };
  const selectedHeight = quality === "audio" ? "Audio only" : quality === "best" ? (video?.heights[0] ? `${video.heights[0]}p` : "Best available") : `Up to ${quality}`;
  return <div className="page home-page">
    <div className="home-page-heading"><span className="home-eyebrow">DOWNLOAD STUDIO</span></div>
    <section className={`url-box ${loading ? "loading" : ""}`}><div className="url-input"><Icon name="link" size={19} /><input value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === "Enter" && void analyze()} placeholder="Paste video URL here…" aria-label="Video URL" />
      {url ? <button className="clear" onClick={() => { setUrl(""); setVideo(null); setError(""); }} aria-label="Clear URL"><Icon name="close" size={16} /></button> : <button className="paste" onClick={() => void paste()}><Icon name="clipboard" size={15} />Paste</button>}</div>
      <Button kind="primary" onClick={() => void analyze()} disabled={!url.trim() || loading}>{loading ? "Analyzing…" : "Analyze Video"}</Button>{loading && <div className="scan-line" />}</section>
    {error && <div className="inline-error" role="alert"><Icon name="alert" size={15} />{error}</div>}
    {video ? <div className="content-grid"><section className="preview-panel"><div className="section-kicker"><span>VIDEO PREVIEW</span><span className="source-dot">{video.extractor}</span></div>
      <div className="preview-media">{video.thumbnail ? <img src={video.thumbnail} alt="Video thumbnail" onError={e => { e.currentTarget.style.display = "none"; }} /> : <div className="thumbnail-placeholder"><Icon name="file" size={30} /></div>}<div className="media-shade" />{video.duration != null && <span className="duration">{video.duration_text}</span>}</div>
      <div className="video-copy"><h2>{video.title}</h2><p>{video.uploader || video.extractor}{video.view_count != null && <> <span>•</span> {video.view_count.toLocaleString()} views</>}</p></div>
      <div className="metadata-row"><div><small>DURATION</small><b>{formatDuration(video.duration)}</b></div><div><small>SOURCE</small><b>{video.extractor}</b></div><div><small>AVAILABLE UP TO</small><b>{video.formats.length ? (video.heights[0] ? `${video.heights[0]}p` : "Audio") : "Unavailable"}</b></div></div>
    </section><section className="options-panel"><div className="section-kicker"><span>DOWNLOAD OPTIONS</span><span>{video.formats.length.toString().padStart(2, "0")}</span></div>
      <div className="control-group"><label>Video quality <span lang="bn">ভিডিওর মান</span></label><div className="segments">{["best", "1080p", "720p", "audio"].map((q, i) => {
        const allowed = hasDownloadableFormats && (q === "best" || q === "audio" ? (q !== "audio" || video.formats.some(item => item.has_audio)) : video.heights.some(height => height >= Number.parseInt(q, 10)));
        return <button key={q} disabled={!allowed} className={quality === q ? "selected" : ""} onClick={() => { setQuality(q); onSaveSettings({ quality: q }); }}>{["Best", "1080p", "720p", "Audio"][i]}</button>;
      })}</div></div>
      <div className="control-row"><div className="control-group"><label>Format</label><div className="select-wrap"><select value={quality === "audio" ? "mp3" : chosenFormat} onChange={e => { setFormat(e.target.value); onSaveSettings({ format: e.target.value }); }} disabled={!hasDownloadableFormats || quality === "audio"}>{quality === "audio" && <option value="mp3">MP3</option>}{formatOptions.map(option => <option key={option} value={option}>{option.toUpperCase()}</option>)}</select><Icon name="chevron" size={14} /></div></div><div className="control-group"><label>Estimated size</label><div className="static-value">{video.estimated_size_text}</div></div></div>
      <div className="location"><div className="folder-icon"><Icon name="folder" /></div><div><small>SAVE TO</small><b title={settings.folder}>{settings.folder}</b></div><button onClick={onChangeFolder}>Browse</button></div>
      {!hasDownloadableFormats && <div className="inline-error format-unavailable" role="status"><Icon name="alert" size={15} />{video.format_warning || "ভিডিওর তথ্য পাওয়া গেছে, কিন্তু YouTube কোনো download format দেয়নি। Node.js 22+ runtime আছে কি না এবং startup.log-এর YouTube diagnostics দেখুন; YouTube-এর request সীমাবদ্ধ থাকলে পরে retry করুন। VidoSwift protection এড়িয়ে যায় না।"}</div>}
      <div className="download-cta"><Button kind="primary" icon="download" onClick={() => void begin()} disabled={!hasDownloadableFormats} className="wide">Download video</Button><p lang="bn">ডাউনলোড শুরু হলে এটি স্বয়ংক্রিয়ভাবে কিউতে যোগ হবে।</p></div>
    </section></div> : !loading && <section className="empty-state"><div className="empty-icon"><Icon name="link" /></div><h2>Ready when you are</h2><p lang="bn">উপরের ঘরে ভিডিওর লিংক পেস্ট করে “Analyze Video” চাপুন।</p></section>}
    <footer className="home-footer"><div className="home-engine" role="status" title="Engine ready · yt-dlp desktop engine"><span /><b>Engine ready</b><small>yt-dlp desktop engine</small></div><small className="home-developer">Developed by Jaber Mohammad</small></footer>
  </div>;
}
function Status({ label }: { label: string }) { return <span className={`status ${label.toLowerCase()}`}><i />{label}</span>; }
function Queue({ jobs, api, onOpenFolder }: { jobs: Job[]; api: NativeApi; onOpenFolder: () => void }) {
  const active = jobs.filter(j => ["Queued", "Downloading", "Processing"].includes(j.status));
  return <div className="page"><PageHeading eyebrow="ACTIVITY" title="Download Queue" bangla="চলমান এবং অপেক্ষমাণ ডাউনলোডগুলো এক জায়গায় দেখুন।" action={<Button kind="secondary" icon="folder" onClick={onOpenFolder}>Open folder</Button>} />
    <div className="stats-line"><span><b>{active.length}</b> active jobs</span><span><b>{jobs.filter(j => j.status === "Downloading").length}</b> downloading</span><span><b>{jobs.filter(j => j.status === "Failed").length}</b> failed</span></div>
    <section className="list-panel"><div className="list-header"><span>CURRENT JOBS</span><span>{active.length} ITEMS</span></div>{active.length ? active.map(job => <div className="job" key={job.id}><div className="job-thumb"><Icon name={job.status === "Queued" ? "clock" : "file"} /></div><div className="job-main"><div className="job-title"><div><h3>{job.title}</h3><p>{job.quality} · {job.format.toUpperCase()}</p></div><Status label={job.status} /></div><div className="progress"><i style={{ width: `${job.progress}%` }} /></div><div className="job-meta"><span>{job.error || (job.status === "Processing" ? "Finalizing downloaded file…" : job.status === "Queued" ? "Waiting for an available download slot" : [job.speed, job.eta && `ETA ${job.eta}`].filter(Boolean).join(" · ") || "Receiving progress…")}</span><b>{job.progress ? `${Math.floor(job.progress)}%` : job.status === "Queued" ? "Waiting" : "—"}</b></div></div><button className="icon-button" title="Cancel download" onClick={() => void api.cancel(job.id)}><Icon name="close" /></button></div>) : <div className="empty-list">No active downloads. Analyze a video to add it to the queue.</div>}
      {jobs.filter(j => j.status === "Failed").map(job => <div className="job" key={job.id}><div className="job-thumb"><Icon name="alert" /></div><div className="job-main"><div className="job-title"><div><h3>{job.title}</h3><p>{job.format.toUpperCase()} · {job.error}</p></div><Status label="Failed" /></div></div><Button kind="quiet" onClick={() => void api.retry(job.id)}>Retry</Button></div>)}
    </section></div>;
}
function History({ items, api, onCleared }: { items: Job[]; api: NativeApi; onCleared: () => void }) {
  const [query, setQuery] = useState(""); const filtered = useMemo(() => items.filter(item => item.title.toLowerCase().includes(query.toLowerCase())), [items, query]);
  const clear = async () => { await api.clear_history(); onCleared(); };
  return <div className="page"><PageHeading eyebrow="LIBRARY" title="Download History" bangla="আগের ডাউনলোড খুঁজুন এবং ফাইলের অবস্থান খুলুন।" action={<Button kind="quiet" icon="trash" onClick={() => void clear()} disabled={!items.length}>Clear history</Button>} />
    <div className="history-tools"><div className="search-box"><Icon name="search" /><input placeholder="Search download history…" value={query} onChange={e => setQuery(e.target.value)} /></div><Button kind="secondary" onClick={() => void api.open_folder()}>Open download folder</Button></div>
    <section className="list-panel history-list"><div className="list-header"><span>RECENT DOWNLOADS</span><span>{filtered.length} ITEMS</span></div>{filtered.length ? filtered.map(item => <div className="history-row" key={item.id}><div className="file-type"><Icon name={item.status === "Completed" ? "file" : "alert"} /></div><div className="history-name"><h3>{item.title}</h3><p>{new Date((item as Job & { finished?: number }).finished ? (item as Job & { finished: number }).finished * 1000 : item.created * 1000).toLocaleString()} <span>·</span> {item.quality} · {item.format.toUpperCase()}</p></div><Status label={item.status} />{item.status === "Completed" && item.filepath ? <Button kind="quiet" icon="folder" onClick={() => void api.open_folder(item.filepath)}>Open folder</Button> : <Button kind="quiet" icon="download" onClick={() => void api.retry(item.id)}>Retry</Button>}<button className="icon-button" title={item.filepath ? "Open file" : item.error} onClick={() => item.filepath && void api.open_file(item.filepath)} disabled={!item.filepath}><Icon name="external" /></button></div>) : <div className="empty-list">{items.length ? "No matching downloads." : "Completed and failed downloads will appear here."}</div>}</section>
    <p className="history-note"><Icon name="alert" size={15} /> Clearing history removes records only. Your downloaded files are not deleted.</p></div>;
}
function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) { return <button className={`toggle ${checked ? "on" : ""}`} onClick={() => onChange(!checked)} aria-label={label} aria-pressed={checked}><i /></button>; }
function Settings({ settings, api, onChanged, onChangeFolder }: { settings: SettingsData; api: NativeApi; onChanged: (s: SettingsData) => void; onChangeFolder: () => void }) {
  const save = async (values: Partial<SettingsData>) => { try { onChanged(await api.save_settings(values)); } catch (e) { window.alert(String(e)); } };
  const changeCookieBrowser = async (browser: string) => {
    if (settings.youtube_cookies_enabled && !window.confirm("Allow yt-dlp to read this browser's local cookie store for YouTube requests? Only cookies matching YouTube are used for YouTube URLs. VidoSwift does not export, save, or log cookie values.")) return;
    await save({ youtube_cookies_browser: browser });
  };
  const toggleCookies = async (enabled: boolean) => {
    if (enabled && settings.youtube_cookies_browser === "none") { window.alert("Choose your signed-in browser first."); return; }
    if (enabled && !window.confirm("YouTube browser-cookie access is optional. yt-dlp will read the selected browser's local cookie store; only cookies matching YouTube are used for YouTube URLs. VidoSwift does not export, save, or log cookie values. Continue?")) return;
    await save({ youtube_cookies_enabled: enabled });
  };
  return <div className="page settings-page"><PageHeading eyebrow="PREFERENCES" title="Settings" bangla="VidoSwift আপনার কাজের ধরন অনুযায়ী সাজিয়ে নিন।" />
    <section className="settings-section"><div className="settings-title"><h2>Downloads</h2><p>Default save location and media preferences.</p></div><div className="settings-controls"><div className="setting-row"><div><b>Download folder</b><small>Where completed files are stored</small></div><div className="path-value" title={settings.folder}><Icon name="folder" />{settings.folder}</div><Button kind="secondary" onClick={onChangeFolder}>Change</Button></div>
      <div className="setting-row"><div><b>Default video quality</b><small>Applied when quality is available</small></div><div className="select-wrap compact"><select value={settings.quality} onChange={e => void save({ quality: e.target.value })}><option value="best">Best available</option><option value="1080p">1080p</option><option value="720p">720p</option><option value="480p">480p</option><option value="audio">Audio only (MP3)</option></select><Icon name="chevron" size={14} /></div></div>
      <div className="setting-row"><div><b>Default format</b><small>Container used for video downloads</small></div><div className="select-wrap compact"><select value={settings.format} onChange={e => void save({ format: e.target.value })}><option value="mp4">MP4</option><option value="webm">WEBM</option><option value="mkv">MKV</option></select><Icon name="chevron" size={14} /></div></div></div></section>
    <section className="settings-section"><div className="settings-title"><h2>Behavior</h2><p>Control what happens during and after downloads.</p></div><div className="settings-controls"><div className="setting-row"><div><b>Start downloads automatically</b><small>Begin as soon as video analysis completes</small></div><Toggle checked={settings.auto_start} label="Auto-start downloads" onChange={value => void save({ auto_start: value })} /></div>
      <div className="setting-row"><div><b>Keep source metadata</b><small>Write video title and metadata beside the downloaded file</small></div><Toggle checked={settings.keep_metadata} label="Keep source metadata" onChange={value => void save({ keep_metadata: value })} /></div></div></section>
    <section className="settings-section"><div className="settings-title"><h2>YouTube browser cookies</h2><p>Optional sign-in support for YouTube only. VidoSwift never asks for your password.</p></div><div className="settings-controls"><div className="setting-row"><div><b>Browser</b><small>Choose the browser profile where you are signed in</small></div><div className="select-wrap compact"><select value={settings.youtube_cookies_browser} onChange={e => void changeCookieBrowser(e.target.value)}><option value="none">Not selected</option><option value="chrome">Google Chrome</option><option value="edge">Microsoft Edge</option><option value="firefox">Firefox</option><option value="brave">Brave</option><option value="chromium">Chromium</option><option value="opera">Opera</option><option value="vivaldi">Vivaldi</option><option value="whale">Whale</option><option value="safari">Safari</option></select><Icon name="chevron" size={14} /></div></div><div className="setting-row"><div><b>Allow local browser-cookie access</b><small>Only used for YouTube analysis and downloads; turn off anytime</small></div><Toggle checked={settings.youtube_cookies_enabled} label="Allow YouTube browser-cookie access" onChange={value => void toggleCookies(value)} /></div></div></section>
    <section className="about-row"><div className="version-info"><span className="version-chip">v1.0.0</span><small>Powered by yt-dlp</small></div><Button kind="quiet" icon="external" onClick={() => void api.open_folder()}>Open download folder</Button></section></div>;
}
const emptyState: AppState = { settings: { folder: "", quality: "best", format: "mp4", auto_start: false, keep_metadata: true, youtube_cookies_enabled: false, youtube_cookies_browser: "none" }, jobs: [], history: [] };
export default function App() {
  const [page, setPage] = useState<Page>("download"); const [state, setState] = useState<AppState>(emptyState); const [toast, setToast] = useState(""); const [nativeApi, setNativeApi] = useState<NativeApi | null>(null); const [error, setError] = useState("");
  const [startupStatus, setStartupStatus] = useState<"waiting" | "connecting" | "failed">("waiting"); const [startupError, setStartupError] = useState("");
  const startupTask = useRef<Promise<void> | null>(null);
  const refresh = useCallback(async (api: NativeApi) => { try { setState(await api.get_state()); return true; } catch (e) { setError(`Backend request failed: ${e instanceof Error ? e.message : String(e)}`); return false; } }, []);
  const connect = useCallback(async () => {
    setStartupStatus("connecting"); setStartupError("");
    try {
      const api = boundedApi(await waitForBridge());
      let lastError: unknown = new Error("The backend did not report ready.");
      for (let attempt = 0; attempt < 4; attempt++) {
        try {
          const health = await api.health();
          if (!health.ok || health.status !== "ready") throw new Error("The desktop engine is still initializing.");
          const initialState = await api.get_state();
          setState(initialState); setNativeApi(api); setStartupStatus("waiting"); setStartupError(""); setError("");
          return;
        } catch (e) {
          lastError = e;
          if (attempt < 3) await new Promise(resolve => window.setTimeout(resolve, 350 * (attempt + 1)));
        }
      }
      throw lastError;
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      setStartupError(`${detail} Check %APPDATA%\\VidoSwift\\startup.log for diagnostics.`);
      setStartupStatus("failed");
    }
  }, []);
  const runConnect = useCallback(() => {
    if (startupTask.current) return startupTask.current;
    startupTask.current = connect().finally(() => { startupTask.current = null; });
    return startupTask.current;
  }, [connect]);
  useEffect(() => { document.documentElement.dataset.theme = "dark"; void runConnect(); }, [runConnect]);
  useEffect(() => {
    if (!nativeApi) return;
    let stopped = false; let timer = 0; let delay = 1500;
    const poll = async () => {
      if (stopped) return;
      const ok = await refresh(nativeApi);
      if (stopped) return;
      delay = ok ? 1500 : Math.min(delay * 2, 8000);
      timer = window.setTimeout(() => void poll(), delay);
    };
    timer = window.setTimeout(() => void poll(), delay);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [nativeApi, refresh]);
  const tell = (message: string) => { setToast(message); window.setTimeout(() => setToast(""), 3500); };
  const changeFolder = async () => { if (!nativeApi) return; try { const folder = await nativeApi.choose_folder(); if (folder) setState(await nativeApi.get_state()); } catch (e) { setError(String(e)); } };
  const saveSettings = async (values: Partial<SettingsData>) => { if (!nativeApi) return; try { const settings = await nativeApi.save_settings(values); setState(previous => ({ ...previous, settings })); } catch (e) { setError(String(e)); } };
  if (!nativeApi) return <div className="desktop"><header className="titlebar"><div className="titlebar-drag pywebview-drag-region"><div className="breadcrumb"><span>VidoSwift</span></div></div><WindowControls /></header><div className="startup-workspace"><div className="empty-state startup-state"><div className="empty-icon"><Icon name={startupStatus === "failed" ? "alert" : "download"} /></div><h2>{startupStatus === "failed" ? "VidoSwift could not connect" : "Starting VidoSwift"}</h2><p>{startupStatus === "failed" ? startupError : "Waiting for the desktop bridge and checking backend health…"}</p>{startupStatus === "failed" && <div style={{ display: "flex", gap: 10, marginTop: 14 }}><Button kind="primary" onClick={() => void runConnect()}>Retry connection</Button><Button kind="secondary" onClick={() => { const api = window.pywebview?.api; if (api) void boundedApi(api).quit_app().catch(() => window.close()); else window.close(); }}>Exit VidoSwift</Button></div>}</div></div></div>;
  const activeCount = state.jobs.filter(job => ["Queued", "Downloading", "Processing"].includes(job.status)).length;
  return <AppShell page={page} setPage={setPage} activeCount={activeCount}>{error && <div className="global-error" role="alert"><Icon name="alert" size={15} /><span>{error}</span><button onClick={() => void runConnect()}>Retry</button><button onClick={() => setError("")} aria-label="Dismiss">×</button></div>}
    {page === "download" && <Home api={nativeApi} settings={state.settings} onStarted={() => { void refresh(nativeApi); tell("Download added to queue"); setPage("queue"); }} onChangeFolder={() => void changeFolder()} onSaveSettings={values => void saveSettings(values)} />}
    {page === "queue" && <Queue jobs={state.jobs} api={nativeApi} onOpenFolder={() => void nativeApi.open_folder()} />}
    {page === "history" && <History items={state.history} api={nativeApi} onCleared={() => void refresh(nativeApi)} />}
    {page === "settings" && <Settings settings={state.settings} api={nativeApi} onChanged={settings => setState(previous => ({ ...previous, settings }))} onChangeFolder={() => void changeFolder()} />}
    {toast && <div className="toast"><span><Icon name="check" size={16} /></span><div><b>{toast}</b><small>VidoSwift is working on your request.</small></div><button onClick={() => setPage("queue")}>View queue</button></div>}
  </AppShell>;
}
