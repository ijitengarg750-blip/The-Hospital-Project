import React, { FormEvent, useState } from "react";
import { createRoot } from "react-dom/client";
import { Activity, CalendarDays, FileImage, FileText, LogOut, MonitorUp, PlayCircle, Search } from "lucide-react";
import "./styles.css";

type ClinicalFile = { id: number; kind: string; original_name: string; mime_type?: string; size_bytes: number; role?: string; embedded_path?: string };
type Machine = { code: string; name: string; modality?: string; location?: string; no_vnc_url?: string };
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
    try { detail = (await response.json()).detail ?? detail; } catch { /* ignore */ }
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

function FileButton({ file }: { file: ClinicalFile }) {
  const icon = file.kind === "video" ? <PlayCircle size={17}/> : file.kind.includes("image") ? <FileImage size={17}/> : <FileText size={17}/>;
  const open = async () => {
    const response = await api(`/api/files/${file.id}`);
    const blob = await response.blob();
    window.open(URL.createObjectURL(blob), "_blank", "noopener,noreferrer");
  };
  const label = file.kind === "report" ? "View report" : file.kind === "video" ? "Play video" : file.kind === "dicom" ? "Original DICOM" : file.role || "View image";
  return <button className="secondary" title={file.embedded_path || file.original_name} onClick={open}>{icon}{label}</button>;
}

function StudyCard({ study }: { study: Study }) {
  const when = new Date(study.acquisition_datetime);
  const visible = study.files.filter(f => ["report", "report_image", "image", "video", "dicom"].includes(f.kind));
  const remote = async () => {
    const response = await api(`/api/studies/${study.id}/remote-session`, { method: "POST" });
    const result = await response.json();
    window.open(result.url, "_blank", "noopener,noreferrer");
  };
  return <article className="study-card">
    <div className="study-time"><strong>{when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</strong><span>{when.toLocaleDateString()}</span></div>
    <div className="study-main">
      <div className="study-heading"><div><h3>{study.title}</h3><p>{study.machine.name}{study.machine.location ? ` · ${study.machine.location}` : ""}</p></div><span className="status">Available</span></div>
      <div className="inventory"><span>{study.dicom_objects_count} DICOM objects</span><span>{study.series_count} series</span><span>{study.image_entries_count} image entries</span><span>{study.unique_images_count} unique images</span>{study.pixel_data_objects_count > 0 && <span>{study.pixel_data_objects_count} PixelData objects</span>}</div>
      <div className="actions">
        {visible.slice(0, 8).map(file => <FileButton key={file.id} file={file} />)}
        {study.machine.no_vnc_url && <button className="remote" onClick={remote}><MonitorUp size={17}/>Open live software</button>}
      </div>
    </div>
  </article>;
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
      <section className="hero"><p className="eyebrow">Patient lookup</p><h1>Find diagnostic history</h1><p>Search with the patient’s Practo UID.</p>
        <form className="search" onSubmit={search}><Search size={20}/><input value={uid} onChange={e => setUid(e.target.value)} placeholder="Enter Practo UID" required autoFocus/><button disabled={busy}>{busy ? "Searching…" : "Search"}</button></form>
        {error && <p className="error search-error">{error}</p>}
      </section>
      {patient && <section className="patient">
        <div className="patient-header"><div className="avatar">{(patient.name ?? "P").slice(0, 1).toUpperCase()}</div><div><p className="eyebrow">Patient</p><h2>{patient.name || "Name unavailable"}</h2><p>Practo UID: <strong>{patient.practo_uid}</strong>{patient.date_of_birth ? ` · DOB ${new Date(patient.date_of_birth).toLocaleDateString()}` : ""}</p></div></div>
        {patient.studies.length === 0 ? <div className="empty">No diagnostic tests have been collected yet.</div> : [...groupStudies(patient.studies)].map(([label, studies]) => <div className="date-group" key={label}><h2><CalendarDays size={19}/>{label}</h2>{studies.map(s => <StudyCard key={s.id} study={s}/>)}</div>)}
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

