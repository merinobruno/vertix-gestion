import {legacyPartyCount, legacyPartyPage} from "./legacy-directory.js";

async function post(action, payload, signal) {
  const response = await fetch("/api/agenda/" + action, {method:"POST", credentials:"same-origin", cache:"no-store",
    headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload), signal});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || "No se pudo conectar con la agenda compartida. Volvé a intentar.");
    error.status = response.status;
    throw error;
  }
  return data;
}

export function setupSharedDirectory(onAccess, onImport) {
  const byId = id => document.getElementById(id);
  const form = byId("agenda-access"), code = byId("agenda-code"), login = byId("agenda-login"), logout = byId("agenda-logout");
  const message = byId("agenda-access-status"), importer = byId("agenda-import"), outcome = byId("agenda-import-status");
  let enabled = false, authenticated = false, busy = false, retryLogout = false, generation = 0, timer = 0, channel;
  let session = new AbortController();

  function render() {
    form.hidden = !enabled || authenticated;
    logout.hidden = !enabled || (!authenticated && !retryLogout);
    logout.disabled = login.disabled = busy;
    message.hidden = !message.textContent;
  }

  function setAccess(allowed, expires = 0) {
    clearTimeout(timer);
    const changed = authenticated !== allowed;
    authenticated = allowed;
    if (changed) {
      session.abort();
      session = new AbortController();
      importer.hidden = outcome.hidden = true;
      importer.disabled = false;
      outcome.textContent = "";
      code.value = "";
    }
    render();
    if (changed) onAccess();
    if (!allowed) return;
    const active = session;
    timer = setTimeout(() => lock("La sesión venció. Volvé a ingresar."), Math.max(0, expires * 1000 - Date.now()));
    if (changed) legacyPartyCount().then(count => {
      if (session !== active || !count) return;
      importer.textContent = "Importar " + count + (count === 1 ? " ficha de este navegador" : " fichas de este navegador");
      importer.hidden = false;
    }).catch(() => {}); // Old browser storage is optional.
  }

  function lock(text = "") {
    generation++;
    code.value = "";
    message.textContent = text;
    setAccess(false);
  }

  async function check() {
    if (!enabled || busy || retryLogout) return;
    const token = ++generation;
    try {
      const current = await post("session", {});
      if (token !== generation || !enabled) return;
      message.textContent = !current.configured ? "La agenda compartida no está disponible. Volvé a intentar." :
        (authenticated && !current.authenticated ? "La sesión venció. Volvé a ingresar." : message.textContent);
      setAccess(current.authenticated === true, current.expires_at);
    } catch {
      if (token === generation && enabled) lock("No se pudo verificar la sesión. Volvé a ingresar.");
    }
  }

  async function request(action, payload, signal) {
    if (!enabled || !authenticated) throw new Error("Ingresá la clave del equipo para acceder a la agenda.");
    const active = session;
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.throwIfAborted();
    signal?.addEventListener("abort", abort, {once:true});
    active.signal.addEventListener("abort", abort, {once:true});
    try {
      const data = await post(action, payload, controller.signal);
      controller.signal.throwIfAborted();
      return data;
    } catch (error) {
      if (session === active && error.status === 401) lock("La sesión venció. Volvé a ingresar.");
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      active.signal.removeEventListener("abort", abort);
    }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!enabled || busy || !code.value.trim()) return;
    const token = ++generation;
    busy = true;
    message.textContent = "";
    render();
    try {
      const result = await post("login", {code:code.value.trim()});
      if (token !== generation || !enabled) return;
      retryLogout = false;
      code.value = "";
      setAccess(true, result.expires_at);
      channel?.postMessage("login");
    } catch (error) {
      if (token === generation) { message.textContent = error.message === "Failed to fetch" ? "No se pudo iniciar sesión." : error.message; code.select(); }
    } finally { busy = false; render(); }
  });

  logout.addEventListener("click", async () => {
    if (!enabled || busy) return;
    busy = true;
    retryLogout = true;
    lock();
    try {
      await post("logout", {});
      retryLogout = false;
      channel?.postMessage("logout");
      code.focus();
    } catch {
      message.textContent = "No se pudo cerrar la sesión. Volvé a elegir «Cerrar sesión».";
    } finally { busy = false; render(); }
  });

  importer.addEventListener("click", async () => {
    if (!authenticated || importer.disabled) return;
    const active = session;
    importer.disabled = true;
    outcome.hidden = false;
    outcome.textContent = "Importando fichas…";
    const counts = {created:0, existing:0, conflicts:0, invalid:0};
    try {
      let cursor = null;
      while (true) {
        const batch = await legacyPartyPage(cursor);
        if (session !== active) return;
        if (!batch.parties.length) break;
        const result = await request("import", {parties:batch.parties});
        for (const key of Object.keys(counts)) counts[key] += result[key];
        if (batch.parties.length < 25) break;
        cursor = batch.lastId;
      }
      outcome.textContent = "Incorporadas " + counts.created + " · Ya existentes " + counts.existing
        + (counts.conflicts ? " · Con datos diferentes " + counts.conflicts : "") + (counts.invalid ? " · Incompletas " + counts.invalid : "") + ".";
      importer.hidden = true;
      onImport();
    } catch {
      if (session === active) outcome.textContent = "La importación se interrumpió. Podés retomarla sin duplicar fichas.";
    } finally { if (session === active) importer.disabled = false; }
  });

  window.addEventListener("focus", check);
  window.addEventListener("pageshow", event => { if (event.persisted) check(); });
  return {request, get authenticated() { return authenticated; }, enable(active) {
    if (enabled === active) return;
    enabled = active;
    channel?.close();
    channel = null;
    retryLogout = false;
    lock();
    if (!active) return;
    try {
      channel = new BroadcastChannel("vertix-agenda-session");
      channel.onmessage = event => { if (event.data === "logout") lock(); else if (event.data === "login") check(); };
    } catch { /* Session checks on focus also work without BroadcastChannel. */ }
    check();
  }};
}
