import React, { FormEvent, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import RFB from "@novnc/novnc";
import { Activity, CalendarDays, FileImage, LogOut, MessageSquare, MonitorUp, Search, X } from "lucide-react";
import "./styles.css";

type ClinicalFile = { id: number; kind: string; original_name: string; mime_type?: string; size_bytes: number; role?: string; embedded_path?: string };
type PhoenixReport = { object_id: number; label: string; laterality?: string; mime_type: "image/png"; url: string };
type ReportAsset = { key: string; label: string; mimeType?: string; url: string };
type Machine = { id: number; code: string; name: string; modality?: string; location?: string; no_vnc_url?: string };
type Study = { id: number; study_uid: string; title: string; acquisition_datetime: string; status: string; machine: Machine; files: ClinicalFile[]; dicom_objects_count: number; series_count: number; embedded_entries_count: number; image_entries_count: number; unique_images_count: number; pixel_data_objects_count: number };
type Patient = { practo_uid: string; name?: string; date_of_birth?: string; studies: Study[] };
type RemoteSession = { id: string; status: "requested" | "ready" | "connected" | "ended" | "expired" | "failed"; machine_state: string; end_reason?: string };
type RemoteMessage = { id: number; sender: "doctor" | "local_operator"; body: string; created_at: string };
type AuthSession = { username: string; role: "admin" | "doctor"; mfa_required: boolean };

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

const api = async (path: string, options: RequestInit = {}) => {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  if (!response.ok) {
    let detail = "Request failed";
    try {
      const problem = (await response.json()).detail;
      if (typeof problem === "string") detail = problem;
      else if (Array.isArray(problem)) detail = problem.map(item => item.msg || "Invalid input").join("; ");
    } catch { /* ignore */ }
    throw new ApiError(detail, response.status);
  }
  return response;
};

function Login({ onLogin }: { onLogin: (session: AuthSession) => void }) {
  const [username, setUsername] = useState("doctor");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const response = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      onLogin(await response.json());
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return <main className="login-shell">
    <section className="login-card">
      <div className="brand-mark"><Activity size={28}/></div>
      <p className="eyebrow">Clinical workspace</p>
      <h1>Diagnostic Dashboard</h1>
      <p className="muted">Secure access to patient reports and diagnostic systems.</p>
      <form onSubmit={submit}>
        <label>Username<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" /></label>
        <label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </section>
  </main>;
}

const waitForReady = async (sessionId: string): Promise<RemoteSession> => {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const session: RemoteSession = await (await api(`/api/remote-sessions/${sessionId}`)).json();
    if (session.status === "ready") return session;
    if (["ended", "expired", "failed"].includes(session.status)) throw new Error(`Remote session ${session.status}.`);
    await new Promise(resolve => window.setTimeout(resolve, 750));
  }
  throw new Error("The diagnostic PC did not become ready within one minute.");
}

function RemoteViewer({ sessionId, ticket, websocketPath, onClose }: { sessionId: string; ticket: string; websocketPath: string; onClose: () => void }) {
  const screen = useRef<HTMLDivElement>(null);
  const rfb = useRef<RFB | null>(null);
  const [status, setStatus] = useState("Connecting securely…");
  const [messages, setMessages] = useState<RemoteMessage[]>([]);
  const [body, setBody] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!screen.current) return;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const client = new RFB(screen.current, `${scheme}://${window.location.host}${websocketPath}`, {
      shared: true,
      wsProtocols: [`hospital-ticket.${ticket}`],
    });
    rfb.current = client;
    client.scaleViewport = true;
    client.resizeSession = false;
    client.addEventListener("connect", () => { setStatus("Connected"); client.focus(); });
    client.addEventListener("disconnect", () => setStatus("Disconnected"));
    client.addEventListener("securityfailure", () => setError("The remote gateway rejected the connection."));
    return () => client.disconnect();
  }, [sessionId, ticket, websocketPath]);

  useEffect(() => {
    let active = true;
    let after = 0;
    const poll = async () => {
      while (active) {
        try {
          const incoming: RemoteMessage[] = await (await api(`/api/remote-sessions/${sessionId}/messages?after=${after}`)).json();
          if (incoming.length) {
            after = incoming[incoming.length - 1].id;
            setMessages(current => [...current, ...incoming]);
          }
          const session: RemoteSession = await (await api(`/api/remote-sessions/${sessionId}`)).json();
          if (["ended", "expired", "failed"].includes(session.status)) { setStatus(`Ended (${session.end_reason || session.status})`); rfb.current?.disconnect(); return; }
        } catch (e) { if (active) setError((e as Error).message); }
        await new Promise(resolve => window.setTimeout(resolve, 1500));
      }
    };
    void poll();
    return () => { active = false; };
  }, [sessionId]);

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    const message = body.trim();
    if (!message) return;
    try {
      await api(`/api/remote-sessions/${sessionId}/messages`, { method: "POST", body: JSON.stringify({ body: message }) });
      setBody("");
    } catch (e) { setError((e as Error).message); }
  };
  const end = async () => {
    try { await api(`/api/remote-sessions/${sessionId}`, { method: "DELETE" }); }
    finally { rfb.current?.disconnect(); onClose(); }
  };

  return <div className="remote-viewer" role="dialog" aria-modal="true" aria-label="Remote diagnostic desktop">
    <header className="remote-toolbar"><strong>Live diagnostic desktop</strong><span role="status">{status}</span><button onClick={() => rfb.current?.sendCtrlAltDel()}>Ctrl+Alt+Del</button><button className="danger" onClick={end}>End session</button></header>
    <div className="remote-workspace"><div ref={screen} className="remote-screen" tabIndex={0}/><aside className="remote-chat"><h2><MessageSquare size={17}/>Messages</h2><div className="message-list" aria-live="polite">{messages.map(message => <p key={message.id} className={message.sender}><strong>{message.sender === "doctor" ? "Doctor" : "Diagnostic PC"}</strong>{message.body}</p>)}</div><form onSubmit={sendMessage}><label htmlFor="remote-message">Message local operator</label><textarea id="remote-message" maxLength={500} value={body} onChange={event => setBody(event.target.value)}/><button>Send</button></form>{error && <p role="alert" className="error">{error}</p>}</aside></div>
  </div>;
}

function StudyCard({ study }: { study: Study }) {
  const when = new Date(study.acquisition_datetime);
  const reportsButtonRef = useRef<HTMLButtonElement>(null);
  const [reportAssets, setReportAssets] = useState<ReportAsset[]>([]);
  const [showReports, setShowReports] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [viewer, setViewer] = useState<{ sessionId: string; ticket: string; websocketPath: string } | null>(null);

  const closeReports = () => {
    setReportAssets([]); setShowReports(false);
    window.requestAnimationFrame(() => reportsButtonRef.current?.focus());
  };
  const trapReportFocus = (event: React.KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Tab") return;
    const controls = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), a[href]")];
    if (!controls.length) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  useEffect(() => () => reportAssets.forEach(asset => URL.revokeObjectURL(asset.url)), [reportAssets]);
  useEffect(() => {
    if (!showReports) return;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") closeReports(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  });

  const openReports = async () => {
    setError(""); setBusy(true);
    try {
      const phoenix: PhoenixReport[] = await (await api(`/api/studies/${study.id}/phoenix-reports`)).json();
      if (phoenix.length) {
        const blobs = await Promise.all(phoenix.map(async report => ({ report, blob: await (await api(report.url)).blob() })));
        setReportAssets(blobs.map(({ report, blob }) => ({
          key: `phoenix-${report.object_id}`, label: report.label, mimeType: report.mime_type,
          url: URL.createObjectURL(blob),
        })));
        setShowReports(true);
        return;
      }
      const files: ClinicalFile[] = await (await api(`/api/studies/${study.id}/reports`)).json();
      if (!files.length) throw new Error("No complete report views were found for this examination.");
      const blobs = await Promise.all(files.map(async file => ({ file, blob: await (await api(`/api/files/${file.id}`)).blob() })));
      setReportAssets(blobs.map(({ file, blob }, index) => ({
        key: `file-${file.id}`, label: file.role || `Report ${index + 1}`, mimeType: file.mime_type,
        url: URL.createObjectURL(blob),
      })));
      setShowReports(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const remote = async () => {
    setError(""); setBusy(true);
    try {
      const created = await (await api(`/api/studies/${study.id}/remote-sessions`, { method: "POST" })).json();
      await waitForReady(created.session.id);
      const access = await (await api(`/api/remote-sessions/${created.session.id}/viewer-tickets`, { method: "POST", body: JSON.stringify({ connection_grant: created.connection_grant }) })).json();
      setViewer({ sessionId: created.session.id, ticket: access.viewer_ticket, websocketPath: access.websocket_path });
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return <article className="study-card">
    <div className="study-time"><strong>{when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong><span>{when.toLocaleDateString()}</span></div>
    <div className="study-main">
      <div className="study-heading"><div><h3>{study.title}</h3><p>{study.machine.name}{study.machine.location ? ` · ${study.machine.location}` : ""}</p></div><span className="status">Report</span></div>
      <div className="actions">
        <button ref={reportsButtonRef} className="report-button" disabled={busy} onClick={openReports}><FileImage size={17}/>{busy ? "Opening reports…" : "Open reports"}</button>
        <button className="remote" disabled={busy} onClick={remote}><MonitorUp size={17}/>{busy ? "Preparing secure session…" : "Open Phoenix / live software"}</button>
        <span className="muted">Secure gateway · automatic VNC authentication</span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {showReports && <div className="report-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) closeReports(); }}>
        <section className="report-dialog" role="dialog" aria-modal="true" aria-labelledby={`reports-${study.id}`} onKeyDown={trapReportFocus}>
          <div className="report-dialog-heading"><div><p className="eyebrow">Report gallery</p><h2 id={`reports-${study.id}`}>{study.title}</h2><p>{reportAssets.length} report view{reportAssets.length === 1 ? "" : "s"}</p></div><button className="icon-button" aria-label="Close reports" autoFocus onClick={closeReports}><X size={20}/></button></div>
          <div className="report-grid">{reportAssets.map(({ key, label, mimeType, url }, index) => <figure key={key}>
            {mimeType?.startsWith("image/") ? <img src={url} alt={`${study.title}, ${label}`} /> : <a className="secondary report-link" href={url} target="_blank" rel="noreferrer">Open report document</a>}
            <figcaption>{label || `Report ${index + 1}`}</figcaption>
          </figure>)}</div>
        </section>
      </div>}
      {viewer && <RemoteViewer {...viewer} onClose={() => setViewer(null)}/>}
    </div>
  </article>;
}

function RemoteSetup({ onUpdate }: { onUpdate: (machine: Machine) => void }) {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  useEffect(() => {
    api("/api/machines").then(response => response.json()).then(setMachines).catch(e => setError(e.message)).finally(() => setBusy(false));
  }, []);
  const save = async (event: FormEvent<HTMLFormElement>, machine: Machine) => {
    event.preventDefault(); setError(""); setMessage(""); setBusy(true);
    const url = String(new FormData(event.currentTarget).get("url") || "").trim();
    try {
      const updated = await (await api(`/api/machines/${machine.id}/remote-access`, { method: "PATCH", body: JSON.stringify({ no_vnc_url: url || null }) })).json();
      setMachines(current => current.map(item => item.id === updated.id ? updated : item));
      onUpdate(updated); setMessage(`${machine.name}: ${url ? "URL saved. Open live software from an examination to test the connection." : "remote access cleared."}`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <section className="remote-setup" aria-label="Remote access setup">
    <h2>Legacy remote URL</h2><p className="muted">Kept for existing deployments. New one-click sessions use the machine agent and the server-side VNC target/secret configuration; no password is sent to this page.</p>
    {machines.map(machine => <form key={machine.id} onSubmit={event => save(event, machine)}><label htmlFor={`remote-${machine.id}`}>{machine.name} · {machine.code}<span className="muted">{machine.no_vnc_url ? "Configured · connection unverified" : "Not configured"}</span></label><div className="remote-input"><input id={`remote-${machine.id}`} name="url" type="url" defaultValue={machine.no_vnc_url || ""} placeholder="http://gateway:6080/vnc.html?view_only=true"/><button className="secondary" disabled={busy}>Save URL</button></div></form>)}
    {busy && <p role="status">Working…</p>}{!busy && !machines.length && !error && <p>No machines registered yet.</p>}
    {error && <p role="alert" className="error">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}

function groupStudies(studies: Study[]) {
  const groups = new Map<string, Study[]>();
  for (const study of studies) {
    const date = new Date(study.acquisition_datetime);
    const today = new Date();
    const key = date.toDateString() === today.toDateString() ? "Today" : date.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
    groups.set(key, [...(groups.get(key) ?? []), study]);
  }
  return groups;
}

function Dashboard({ session, onLogout }: { session: AuthSession; onLogout: () => void }) {
  const [showRemote, setShowRemote] = useState(false);
  const [machines, setMachines] = useState<Record<number, Machine>>({});
  const [uid, setUid] = useState("");
  const [patient, setPatient] = useState<Patient | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const search = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setPatient(null); setBusy(true);
    try { setPatient(await (await api(`/api/patients/${encodeURIComponent(uid.trim())}`)).json()); }
    catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        onLogout();
        return;
      }
      setError((e as Error).message);
    } finally { setBusy(false); }
  };

  return <>
    <header><div className="wordmark"><Activity size={23}/><span>Diagnostic Dashboard</span></div><button className="logout" onClick={onLogout}><LogOut size={16}/>Sign out</button></header>
    <main className="dashboard">
      {session.role === "admin" && <button className="setup-toggle secondary" aria-expanded={showRemote} onClick={() => setShowRemote(!showRemote)}><MonitorUp size={17}/>Diagnostic machines</button>}
      {session.role === "admin" && showRemote && <RemoteSetup onUpdate={machine => setMachines(current => ({ ...current, [machine.id]: machine }))}/>}
      <section className="hero"><p className="eyebrow">Patient lookup</p><h1>Find diagnostic history</h1><p>Search with the patient’s Practo UID.</p>
        <form className="search" onSubmit={search}><Search size={20}/><input aria-label="Practo UID" value={uid} onChange={e => setUid(e.target.value)} placeholder="Enter Practo UID" required autoFocus/><button disabled={busy}>{busy ? "Searching…" : "Search"}</button></form>
        {error && <p className="error search-error">{error}</p>}
      </section>
      {patient && <section className="patient">
        <div className="patient-header"><div className="avatar">{(patient.name ?? "P").slice(0, 1).toUpperCase()}</div><div><p className="eyebrow">Patient</p><h2>{patient.name || "Name unavailable"}</h2><p>Practo UID: <strong>{patient.practo_uid}</strong>{patient.date_of_birth ? ` · DOB ${new Date(patient.date_of_birth).toLocaleDateString()}` : ""}</p></div></div>
        {patient.studies.length === 0 ? <div className="empty">No diagnostic tests have been collected yet.</div> : [...groupStudies(patient.studies)].map(([label, studies]) => <div className="date-group" key={label}><h2><CalendarDays size={19}/>{label}</h2>{studies.map(study => <StudyCard key={study.id} study={{ ...study, machine: machines[study.machine.id] || study.machine }}/>)}</div>)}
      </section>}
    </main>
  </>;
}

function App() {
  const [session, setSession] = useState<AuthSession | null>();
  useEffect(() => {
    api("/api/auth/session").then(response => response.json()).then(setSession).catch(() => setSession(null));
  }, []);
  const logout = async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } finally { setSession(null); }
  };
  if (session === undefined) return <main className="login-shell"><p role="status">Loading secure sessionâ€¦</p></main>;
  return session ? <Dashboard session={session} onLogout={logout}/> : <Login onLogin={setSession}/>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);
