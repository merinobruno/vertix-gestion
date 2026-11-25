// The previous web version stored each party under an auto-incrementing primary key.
// Abort a first-time open so checking for an import never creates a database.
function existingDirectory() {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open("vertix-agenda");
    let absent = false;
    open.onupgradeneeded = () => { absent = true; open.transaction.abort(); };
    open.onerror = () => absent ? resolve(null) : reject(open.error);
    open.onsuccess = () => {
      const db = open.result;
      if (!db.objectStoreNames.contains("parties")) { db.close(); resolve(null); return; }
      db.onversionchange = () => db.close();
      resolve(db);
    };
  });
}

async function readRecords(action, missing) {
  const db = await existingDirectory();
  if (!db) return missing;
  try {
    const tx = db.transaction("parties", "readonly");
    const request = action(tx.objectStore("parties"));
    return await new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve(request.result);
      tx.onabort = () => reject(tx.error || new Error("No se pudieron leer las fichas de este navegador."));
    });
  } finally { db.close(); }
}

export const legacyPartyCount = () => readRecords(store => store.count(), 0);

export async function legacyPartyPage(afterId) {
  const rows = await readRecords(store => store.getAll(afterId === null ? undefined : IDBKeyRange.lowerBound(afterId, true), 25), []);
  return {parties:rows.map(row => row.profile), lastId:rows.at(-1)?.id ?? null};
}

export function setupLegacyImport(directoryRequest, onImport) {
  const button = document.getElementById("agenda-import");
  const outcome = document.getElementById("agenda-import-status");
  let current = null;

  button.addEventListener("click", async () => {
    const active = current;
    if (!active || button.disabled) return;
    button.disabled = true;
    outcome.hidden = false;
    outcome.textContent = "Importando fichas…";
    const counts = {created:0, existing:0, conflicts:0, invalid:0};
    try {
      let cursor = null;
      while (true) {
        const batch = await legacyPartyPage(cursor);
        if (active.signal.aborted) return;
        if (!batch.parties.length) break;
        const result = await directoryRequest("import", {parties:batch.parties}, active.signal);
        if (active.signal.aborted) return;
        for (const key of Object.keys(counts)) counts[key] += result[key];
        if (batch.parties.length < 25) break;
        cursor = batch.lastId;
      }
      outcome.textContent = "Incorporadas " + counts.created + " · Ya existentes " + counts.existing
        + (counts.conflicts ? " · Con datos diferentes " + counts.conflicts : "") + (counts.invalid ? " · Incompletas " + counts.invalid : "") + ".";
      button.hidden = true;
      onImport();
    } catch {
      if (!active.signal.aborted) outcome.textContent = "La importación se interrumpió. Podés retomarla sin duplicar fichas.";
    } finally { if (!active.signal.aborted) button.disabled = false; }
  });

  return function enable(active) {
    if (Boolean(current) === active) return;
    current?.abort();
    current = active ? new AbortController() : null;
    button.hidden = outcome.hidden = true;
    button.disabled = false;
    outcome.textContent = "";
    if (!current) return;
    const {signal} = current;
    legacyPartyCount().then(count => {
      if (signal.aborted || !count) return;
      button.textContent = "Importar " + count + (count === 1 ? " ficha de este navegador" : " fichas de este navegador");
      button.hidden = false;
    }).catch(() => {}); // Old browser storage is optional.
  };
}
