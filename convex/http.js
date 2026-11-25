import {httpActionGeneric, httpRouter, makeFunctionReference} from "convex/server";

const scan = makeFunctionReference("directory:scan");
const save = makeFunctionReference("directory:save");
const importBatch = makeFunctionReference("directory:importBatch");
const headers = {"Content-Type":"application/json; charset=utf-8", "Cache-Control":"private, no-store"};
const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers});

async function authorized(request) {
  const secret = process.env.VERTIX_AGENDA_SERVICE_KEY?.trim();
  const provided = request.headers.get("Authorization");
  if (!secret || secret.length < 32 || !provided || provided.length > 520) return false;
  const bytes = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", bytes.encode(secret), {name:"HMAC", hash:"SHA-256"}, false, ["sign","verify"]);
  const expected = await crypto.subtle.sign("HMAC", key, bytes.encode("Bearer " + secret));
  return crypto.subtle.verify("HMAC", key, expected, bytes.encode(provided));
}

async function ranked(ctx, filters, skip, limit) {
  const counts = [0,0,0], groups = [[],[],[]];
  let cursor = null;
  while (true) {
    const page = await ctx.runQuery(scan, {...filters, cursor});
    for (const {rank, party} of page.rows) {
      if (counts[rank]++ >= skip[rank] && groups[rank].length < limit) groups[rank].push(party);
    }
    if (page.done) return {counts, groups};
    cursor = page.cursor;
  }
}

async function list(ctx, data, suggestions) {
  const {name, digits, kind, offset} = data;
  if (!name && suggestions) return {parties:[]};
  const limit = suggestions ? 8 : 25;
  const filters = {name, digits, kind};
  let {counts, groups} = await ranked(ctx, filters, [0,0,0], offset ? 0 : limit);
  const total = counts.reduce((sum, count) => sum + count, 0);
  if (offset && offset < total) {
    let remaining = offset;
    const skip = counts.map(count => { const n = Math.min(remaining, count); remaining -= n; return n; });
    ({groups} = await ranked(ctx, filters, skip, limit));
  }
  return {parties:groups.flat().slice(0, limit), total, offset, page_size:25};
}

async function readBody(request) {
  const limit = 196608;
  if (Number(request.headers.get("Content-Length")) > limit || !request.body) return null;
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let text = "", length = 0;
  while (true) {
    const {done, value} = await reader.read();
    if (done) return text + decoder.decode();
    length += value.byteLength;
    if (length > limit) { await reader.cancel(); return null; }
    text += decoder.decode(value, {stream:true});
  }
}

const http = httpRouter();
http.route({path:"/agenda", method:"POST", handler:httpActionGeneric(async (ctx, request) => {
  if (!await authorized(request)) return json({error:"Acceso denegado."}, 403);
  if (request.headers.get("Content-Type")?.split(";")[0] !== "application/json") return json({error:"Solicitud inválida."}, 415);
  const text = await readBody(request);
  if (text === null) return json({error:"Solicitud inválida."}, 413);
  let body;
  try { body = JSON.parse(text); } catch { return json({error:"Solicitud inválida."}, 422); }
  if (!body || typeof body.data !== "object" || !body.data) return json({error:"Solicitud inválida."}, 422);
  const {action, data} = body;
  let result;
  if (action === "search" || action === "list") result = await list(ctx, data, action === "search");
  else if (action === "save" || action === "update") result = await ctx.runMutation(save, data);
  else if (action === "import" && Array.isArray(data.records) && data.records.length <= 25) result = await ctx.runMutation(importBatch, data);
  else return json({error:"Acción inválida."}, 422);
  return result.error ? json({error:result.error}, result.status) : json(result);
})});
export default http;
