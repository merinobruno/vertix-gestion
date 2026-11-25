import {legacyPartyCount, legacyPartyPage} from "./legacy-directory.js";

const storageKey = "vertix.agenda.team-link";
const linkPattern = /^[a-f0-9]{64}$/;
const missingLink = "Abrí el enlace de la Agenda que compartió tu equipo.";
const invalidLink = "Este enlace de la Agenda no es válido. Pedí el enlace actual al equipo.";

async function post(action, payload, link, signal) {
  const response = await fetch("/api/agenda/" + action, {method:"POST", credentials:"omit", cache:"no-store", redirect:"error",
    headers:{"Content-Type":"application/json", "X-Vertix-Agenda-Link":link}, body:JSON.stringify(payload), signal});
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
  const message = byId("agenda-access-status"), retry = byId("agenda-retry"), copy = byId("agenda-copy-link");
  const importer = byId("agenda-import"), outcome = byId("agenda-import-status");
  let enabled = false, current = null, remembered = "";

  function showStatus(text, canRetry = false) {
    message.textContent = text;
    message.hidden = !text;
    retry.hidden = !canRetry;
  }

  function reset() {
    const wasAvailable = current?.ready;
    current?.controller.abort();
    current = null;
    copy.hidden = importer.hidden = outcome.hidden = true;
    importer.disabled = false;
    outcome.textContent = "";
    if (wasAvailable) onAccess();
  }

  function savedLink() {
    try { return localStorage.getItem(storageKey) || remembered; }
    catch { return remembered; }
  }

  function forget(link) {
    if (remembered === link) remembered = "";
    try { if (localStorage.getItem(storageKey) === link) localStorage.removeItem(storageKey); }
    catch { /* The URL also works when browser storage is disabled. */ }
  }

  async function check() {
    if (!enabled) return;
    const route = /^#(?:agenda|operacion)\/(.*)$/.exec(location.hash);
    const link = route ? route[1] : savedLink();
    if (current?.link === link) return;
    reset();
    if (!linkPattern.test(link)) {
      showStatus(route ? invalidLink : missingLink);
      return;
    }
    const access = {link, controller:new AbortController(), ready:false};
    current = access;
    showStatus("Abriendo la Agenda…");
    try {
      const result = await post("access", {}, link, access.controller.signal);
      if (current !== access) return;
      if (result.available !== true) throw new Error();
    } catch (error) {
      if (current !== access) return;
      if (error.status === 401) forget(link);
      showStatus(error.status === 401 ? invalidLink : "No se pudo conectar con la agenda compartida.", error.status !== 401);
      return;
    }
    access.ready = true;
    remembered = link;
    try { localStorage.setItem(storageKey, link); } catch { /* Navigation also keeps the link in the fragment. */ }
    if (location.hash === "#agenda") history.replaceState(history.state, "", "#agenda/" + link);
    copy.hidden = false;
    showStatus("");
    onAccess();
    legacyPartyCount().then(count => {
      if (current !== access || !count) return;
      importer.textContent = "Importar " + count + (count === 1 ? " ficha de este navegador" : " fichas de este navegador");
      importer.hidden = false;
    }).catch(() => {}); // Old browser storage is optional.
  }

  async function request(action, payload, signal) {
    const access = current;
    if (!access?.ready) throw new Error(missingLink);
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.throwIfAborted();
    signal?.addEventListener("abort", abort, {once:true});
    access.controller.signal.addEventListener("abort", abort, {once:true});
    try {
      const data = await post(action, payload, access.link, controller.signal);
      controller.signal.throwIfAborted();
      return data;
    } catch (error) {
      if (current === access && error.status === 401) {
        forget(access.link);
        reset();
        showStatus(invalidLink);
      }
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      access.controller.signal.removeEventListener("abort", abort);
    }
  }

  copy.addEventListener("click", async () => {
    const access = current;
    if (!access?.ready) return;
    const url = new URL("/", location.origin);
    url.hash = "agenda/" + access.link;
    try {
      await navigator.clipboard.writeText(url.href);
      if (access === current) showStatus("Enlace copiado.");
    } catch {
      if (access !== current) return;
      history.replaceState(history.state, "", url);
      showStatus("Copiá el enlace de la barra de direcciones.");
    }
  });

  importer.addEventListener("click", async () => {
    const access = current;
    if (!access?.ready || importer.disabled) return;
    importer.disabled = true;
    outcome.hidden = false;
    outcome.textContent = "Importando fichas…";
    const counts = {created:0, existing:0, conflicts:0, invalid:0};
    try {
      let cursor = null;
      while (true) {
        const batch = await legacyPartyPage(cursor);
        if (current !== access) return;
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
      if (current === access) outcome.textContent = "La importación se interrumpió. Podés retomarla sin duplicar fichas.";
    } finally { if (current === access) importer.disabled = false; }
  });

  function retryCheck() { reset(); check(); }
  retry.addEventListener("click", retryCheck);
  window.addEventListener("online", () => { if (!retry.hidden) retryCheck(); });
  window.addEventListener("hashchange", check);
  window.addEventListener("storage", event => { if (event.key === storageKey) check(); });
  return {request, get available() { return Boolean(current?.ready); },
    fragment(agenda) {
      if (current) return (agenda ? "#agenda/" : "#operacion/") + current.link;
      return agenda ? "#agenda" : "";
    },
    enable(active) {
      if (enabled === active) return;
      enabled = active;
      if (active) check();
      else { reset(); showStatus(""); }
    }
  };
}
