import {internalMutationGeneric as mutation, internalQueryGeneric as query} from "convex/server";
import {v} from "convex/values";
import {importedParty, party} from "./shape.js";

function publicParty(row) {
  return {id:row._id, revision:row.revision, ...row.profile};
}

function byDocument(db, record) {
  return db.query("parties").withIndex("by_document", index => index.eq("kind", record.kind).eq("digits", record.digits)).unique();
}

function complete(record, existing) {
  const profile = {...record.profile, address:record.profile.address ?? existing?.profile.address ?? {}};
  if (existing && existing.kind === "CUIT/CUIL" && record.kind === existing.kind && record.digits === existing.digits) {
    profile.id_type = existing.profile.id_type;
  }
  return {...record, profile, revision:(existing?.revision || 0) + 1};
}

function sameProfile(left, right) {
  if (left.name !== right.name || left.id_type !== right.id_type || left.id_number !== right.id_number) return false;
  return ["street","number","floor","apartment","locality","province"].every(key => (left.address?.[key] || "") === (right.address?.[key] || ""));
}

// The indexed document lookup and subsequent write belong to the same serializable mutation.
export const save = mutation({
  args:{record:v.object(party), id:v.optional(v.string()), revision:v.optional(v.number())},
  handler:async (ctx, {record, id, revision}) => {
    const matching = await byDocument(ctx.db, record);
    let existing = null;
    if (id) {
      const normalized = ctx.db.normalizeId("parties", id);
      existing = normalized ? await ctx.db.get("parties", normalized) : null;
      if (!existing) return {status:404, error:"Ya no se encuentra esta ficha en la agenda."};
      if (existing.revision !== revision) return {status:409, error:"La ficha cambió desde que la abriste. Volvé a abrirla para revisar sus datos actuales."};
      if (matching && matching._id !== existing._id) return {status:409, error:"Ese tipo y número de identificación ya corresponden a otra ficha."};
    } else if (matching) {
      return {status:409, error:"Ese documento ya está guardado. Seleccioná la ficha para actualizarla."};
    }
    const row = complete(record, existing);
    const target = existing?._id ?? await ctx.db.insert("parties", row);
    if (existing) await ctx.db.replace("parties", target, row);
    return {party:publicParty({...row, _id:target}), created:!existing};
  },
});

export const importBatch = mutation({
  args:{records:v.array(importedParty)},
  handler:async (ctx, {records}) => {
    const counts = {created:0, existing:0, conflicts:0};
    for (const {fingerprint, ...record} of records) {
      const receipt = await ctx.db.query("imports").withIndex("by_fingerprint", index => index.eq("fingerprint", fingerprint)).unique();
      if (receipt) { counts.existing++; continue; }
      const matching = await byDocument(ctx.db, record);
      const row = complete(record, matching);
      if (matching && !sameProfile(matching.profile, row.profile)) { counts.conflicts++; continue; }
      if (matching) counts.existing++;
      else { await ctx.db.insert("parties", row); counts.created++; }
      // Repeating an import cannot recreate an original document after a teammate corrects it.
      await ctx.db.insert("imports", {fingerprint});
    }
    return counts;
  },
});

export const scan = query({
  args:{cursor:v.union(v.string(), v.null()), name:v.string(), digits:v.string(), kind:v.string()},
  handler:async (ctx, {cursor, name, digits, kind}) => {
    const page = await ctx.db.query("parties").withIndex("by_name").paginate({cursor, numItems:128});
    const rows = [];
    for (const row of page.page) {
      const numberMatches = digits && (!kind || row.kind === kind) && row.digits.includes(digits);
      if (!row.nameKey.includes(name) && !numberMatches) continue;
      let rank = 2;
      if (numberMatches && row.digits === digits) rank = 0;
      else if (row.nameKey.startsWith(name) || (numberMatches && row.digits.startsWith(digits))) rank = 1;
      rows.push({rank, party:publicParty(row)});
    }
    return {rows, done:page.isDone, cursor:page.continueCursor};
  },
});
