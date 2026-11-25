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
