import React, { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, CalendarDays, FileImage, FileText, LogOut, MonitorUp, PlayCircle, Search } from "lucide-react";
import "./styles.css";

type ClinicalFile = { id: number; kind: string; original_name: string; mime_type?: string; size_bytes: number; role?: string; embedded_path?: string };
type Machine = { id: number; code: string; name: string; modality?: string; location?: string; no_vnc_url?: string };
type Study = { id: number; study_uid: string; title: string; acquisition_datetime: string; status: string; machine: Machine; files: ClinicalFile[]; dicom_objects_count: number; series_count: number; embedded_entries_count: number; image_entries_count: number; unique_images_count: number; pixel_data_objects_count: number };
type Patient = { practo_uid: string; name?: string; date_of_birth?: string; studies: Study[] };

const api = async (path: string, options: RequestInit = {}) => {
  const token = localStorage.getItem("hospital-token");
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = "Request failed";
    try {
      const problem = (await response.json()).detail;
      if (typeof problem === "string") detail = problem;
      else if (Array.isArray(problem)) detail = problem.map(item => item.msg || "Invalid input").join("; ");
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return response;
};

function Login({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState("doctor");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const response = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      const result = await response.json();
      localStorage.setItem("hospital-token", result.access_token);
      onLogin();
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

const reportTypes = ["Corneal mapping summary", "Corneal thickness", "Tear-film analysis", "Pupillography"];

// Open during the click so browsers do not block the tab after an authenticated fetch.
async function openResource(load: () => Promise<string>) {
  const tab = window.open("about:blank", "_blank");
  if (!tab) throw new Error("Allow pop-ups for this dashboard, then try again.");
  tab.opener = null;
  try { tab.location.replace(await load()); }
  catch (error) { tab.close(); throw error; }
}

function FileButton({ file }: { file: ClinicalFile }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const icon = file.kind === "video" ? <PlayCircle size={17}/> : file.kind.includes("image") ? <FileImage size={17}/> : <FileText size={17}/>;
  const open = async () => {
    setError(""); setBusy(true);
    try {
      await openResource(async () => {
        const response = await api(`/api/files/${file.id}`);
        const url = URL.createObjectURL(await response.blob());
        window.setTimeout(() => URL.revokeObjectURL(url), 300_000);
        return url;
      });
    } catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  const label = file.kind === "report_image" ? file.role : file.kind === "report" ? "View report" : file.kind === "video" ? "Play video" : file.kind === "dicom" ? "Original DICOM" : "View image";
  return <><button className="secondary" disabled={busy} title={file.embedded_path || file.original_name} onClick={open}>{icon}{busy ? "Opening…" : label}</button>{error && <span role="alert" className="error">{error}</span>}</>;
}

function StudyCard({ study }: { study: Study }) {
  const when = new Date(study.acquisition_datetime);
  const [files, setFiles] = useState(study.files);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reports = files.filter(f => ["report", "report_image"].includes(f.kind));
  const sources = files.filter(f => ["image", "video", "dicom"].includes(f.kind));
  const selectReport = async (file: ClinicalFile, report_type: string) => {
    setError(""); setBusy(true);
    try {
      const updated = await (await api(`/api/files/${file.id}/report-type`, { method: "PATCH", body: JSON.stringify({ report_type: report_type || null }) })).json();
      setFiles(current => current.map(item => item.id === updated.id ? updated : item));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const remote = async () => {
    setError(""); setBusy(true);
    try {
      await openResource(async () => (await (await api(`/api/studies/${study.id}/remote-session`, { method: "POST" })).json()).url);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return <article className="study-card">
    <div className="study-time"><strong>{when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong><span>{when.toLocaleDateString()}</span></div>
    <div className="study-main">
      <div className="study-heading"><div><h3>{study.title}</h3><p>{study.machine.name}{study.machine.location ? ` · ${study.machine.location}` : ""}</p></div><span className="status">{reports.length ? `${reports.length} report${reports.length === 1 ? "" : "s"}` : "Report needed"}</span></div>
      {!reports.length && <p className="muted">No report selected. Export the required report from Phoenix, or review source images below and select a matching report.</p>}
      <div className="report-list">{reports.map(file => <div className="file-row" key={file.id}><FileButton file={file}/><span className="filename">{file.original_name}</span>{file.kind === "report_image" && <button className="text-button" disabled={busy} onClick={() => selectReport(file, "")}>Move to source images</button>}</div>)}</div>
      <div className="actions">
        <button className="remote" disabled={busy || !study.machine.no_vnc_url} onClick={remote}><MonitorUp size={17}/>Open Phoenix / live software</button>
        <span className="muted">{study.machine.no_vnc_url ? "noVNC configured · connection unverified" : "Remote access not configured — use Remote access setup above."}</span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <details className="source-files"><summary>Source images and original files ({sources.length})</summary>
        <p className="muted">Select only complete report views matching Phoenix. Raw frames and previews stay here. Report selection does not verify clinical accuracy.</p>
        {sources.map(file => <div className="file-row" key={file.id}><FileButton file={file}/><span className="filename">{file.embedded_path || file.original_name}</span>{file.kind === "image" && ["image/jpeg", "image/png"].includes(file.mime_type || "") && <select aria-label={`Report type for ${file.original_name}`} disabled={busy} value="" onChange={e => selectReport(file, e.target.value)}><option value="">Use as report…</option>{reportTypes.map(type => <option key={type}>{type}</option>)}</select>}</div>)}
        <div className="inventory"><span>{study.dicom_objects_count} DICOM objects</span><span>{study.series_count} series</span><span>{study.image_entries_count} image entries</span><span>{study.unique_images_count} unique images</span></div>
      </details>
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
    <h2>Remote access setup</h2><p className="muted">Enter the noVNC gateway URL for the diagnostic PC. The PC needs a VNC server and a reachable noVNC gateway. Enter the VNC password only in noVNC.</p>
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

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const [showRemote, setShowRemote] = useState(false);
  const [machines, setMachines] = useState<Record<number, Machine>>({});
  const [uid, setUid] = useState("");
  const [patient, setPatient] = useState<Patient | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const search = async (event: FormEvent) => {
    event.preventDefault(); setError(""); setPatient(null); setBusy(true);
    try { setPatient(await (await api(`/api/patients/${encodeURIComponent(uid.trim())}`)).json()); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  return <>
    <header><div className="wordmark"><Activity size={23}/><span>Diagnostic Dashboard</span></div><button className="logout" onClick={onLogout}><LogOut size={16}/>Sign out</button></header>
    <main className="dashboard">
      <button className="setup-toggle secondary" aria-expanded={showRemote} onClick={() => setShowRemote(!showRemote)}><MonitorUp size={17}/>Remote access setup</button>
      {showRemote && <RemoteSetup onUpdate={machine => setMachines(current => ({ ...current, [machine.id]: machine }))}/>}
      <section className="hero"><p className="eyebrow">Patient lookup</p><h1>Find diagnostic history</h1><p>Search with the patient’s Practo UID.</p>
        <form className="search" onSubmit={search}><Search size={20}/><input aria-label="Practo UID" value={uid} onChange={e => setUid(e.target.value)} placeholder="Enter Practo UID" required autoFocus/><button disabled={busy}>{busy ? "Searching…" : "Search"}</button></form>
        {error && <p className="error search-error">{error}</p>}
      </section>
      {patient && <section className="patient">
        <div className="patient-header"><div className="avatar">{(patient.name ?? "P").slice(0, 1).toUpperCase()}</div><div><p className="eyebrow">Patient</p><h2>{patient.name || "Name unavailable"}</h2><p>Practo UID: <strong>{patient.practo_uid}</strong>{patient.date_of_birth ? ` · DOB ${new Date(patient.date_of_birth).toLocaleDateString()}` : ""}</p></div></div>
        {patient.studies.length === 0 ? <div className="empty">No diagnostic tests have been collected yet.</div> : [...groupStudies(patient.studies)].map(([label, studies]) => <div className="date-group" key={label}><h2><CalendarDays size={19}/>{label}</h2>{studies.map(s => <StudyCard key={s.id} study={{ ...s, machine: machines[s.machine.id] || s.machine }}/>)}</div>)}
      </section>}
    </main>
  </>;
}

function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(localStorage.getItem("hospital-token")));
  const logout = () => { localStorage.removeItem("hospital-token"); setLoggedIn(false); };
  return loggedIn ? <Dashboard onLogout={logout}/> : <Login onLogin={() => setLoggedIn(true)}/>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App/></React.StrictMode>);

