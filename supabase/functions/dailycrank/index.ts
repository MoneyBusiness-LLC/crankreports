// The Daily Crank leaderboard API (Supabase Edge Function "dailycrank").
//   POST /dailycrank/submit  { player, nickname, day, picks }  -> re-scores the trip, saves it, returns the board
//   GET  /dailycrank/board?day=YYYY-MM-DD&player=<uuid>          -> today's board
// Scores are worked out here from the player's picks, using the live questions.json,
// so the browser can't post a made-up score.
import { createClient } from "npm:@supabase/supabase-js@2";

const QUESTIONS_URL = Deno.env.get("DC_QUESTIONS_URL") ?? "https://crankreports.com/thedailycrank/questions.json";
const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, { auth: { persistSession: false } });
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "content-type, authorization, apikey, x-client-info",
};
const reply = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { ...CORS, "Content-Type": "application/json" } });

// ---------- questions ----------
let qCache: any = null, qAt = 0;
async function questions() {
  if (qCache && Date.now() - qAt < 5 * 60 * 1000) return qCache;
  const r = await fetch(`${QUESTIONS_URL}?v=${Math.floor(Date.now() / 300000)}`);
  if (!r.ok) throw new Error(`questions.json ${r.status}`);
  qCache = await r.json(); qAt = Date.now();
  return qCache;
}
function quizDayFor(all: any, day: string) {
  const keys = Object.keys(all).filter((k) => /^\d{4}-\d{2}-\d{2}$/.test(k)).sort();
  return keys.filter((k) => k <= day).pop() ?? keys[0];
}

// ---------- scoring (must match the game) ----------
const norm = (s: unknown) => String(s ?? "").toLowerCase().replace(/[^a-z]/g, "");
const tol = (a: string) => (a.length <= 4 ? 1 : 2);
function dist(a: string, b: string, max: number) {
  const n = a.length, m = b.length;
  if (Math.abs(n - m) > max) return max + 1;
  let pp: number[] | null = null, p = Array.from({ length: m + 1 }, (_, j) => j);
  for (let i = 1; i <= n; i++) {
    const c = new Array(m + 1); c[0] = i; let best = i;
    for (let j = 1; j <= m; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let v = Math.min(p[j] + 1, c[j - 1] + 1, p[j - 1] + cost);
      if (pp && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, pp[j - 2] + 1);
      c[j] = v; if (v < best) best = v;
    }
    if (best > max) return max + 1;
    pp = p; p = c;
  }
  return p[m];
}
function closeness(g: number | null, a: number) {
  if (g == null || !(g > 0)) return 0;
  const r = Math.max(g / a, a / g);
  if (r <= 1.05) return 1;
  if (r >= 2) return 0;
  return 1 - (Math.log(r) - Math.log(1.05)) / (Math.log(2) - Math.log(1.05));
}
function scoreTrip(quiz: any, picks: any) {
  const catches: number[] = []; let total = 0; const s1: string[] = [], s2: string[] = [];
  quiz.surface.forEach((q: any, i: number) => {
    const p = picks?.r1?.[i] ?? {};
    const ms = Math.min(10000, Math.max(0, Math.round(+p.ms || 0)));
    if (p.choice != null && String(p.choice) === String(q.answer)) {
      if (ms < 150) throw new Error("too fast");
      const pts = 100 + Math.round((50 * (10000 - ms)) / 10000);
      catches.push(pts); total += pts; s1.push("🐟");
    } else s1.push("❌");
  });
  quiz.midwater.forEach((q: any, i: number) => {
    const p = picks?.r2?.[i] ?? {};
    const pts = Math.round(300 * closeness(p.guess == null ? null : +p.guess, +q.answer));
    if (pts > 0) { catches.push(pts); total += pts; s2.push("🐠"); } else s2.push("❌");
  });
  const b = quiz.bottom, r3 = picks?.r3 ?? {};
  const v = norm(String(r3.text ?? "").replace(/\s+/g, ""));
  const answers = [b.answer, ...(b.alsoAccept ?? [])].map(norm).filter(Boolean);
  const win = v.length > 0 && answers.some((a: string) => dist(v, a, tol(a)) <= tol(a));
  const bait = Number.isInteger(r3.bait) ? r3.bait : -1;
  let s3 = "❌";
  if (bait >= 0) {
    if (bait >= catches.length) throw new Error("bad bait");
    const bet = catches[bait];
    if (win) { total += 2 * bet; s3 = bet <= 150 ? "🦀" : bet <= 250 ? "🦑" : "🐋💀"; } else total -= bet;
  } else if (win) { total += 100; s3 = "🦀"; }
  return { total, haul: `${s1.join("")} ${s2.join("")} ${s3}` };
}

// ---------- nicknames ----------
const BL = JSON.parse(atob("eyJ3IjogWyJhbmFsIiwgImFudXMiLCAiYXJzZSIsICJhcnNlaG9sZSIsICJhc3MiLCAiYXNzZXMiLCAiYXNzaG9sZSIsICJhc3Nob2xlcyIsICJiYXJlYmFjayIsICJiYXN0YXJkIiwgImJhc3RhcmRzIiwgImJidyIsICJiZHNtIiwgImJlYW5lciIsICJiaXRjaCIsICJiaXRjaGVzIiwgImJpdGNoaW5nIiwgImJpdGNoeSIsICJibG93am9iIiwgImJsb3dqb2JzIiwgImJvbGxvY2tzIiwgImJvbmRhZ2UiLCAiYm9uZXIiLCAiYm9vYiIsICJib29iaWVzIiwgImJvb2JzIiwgImJ1Z2dlciIsICJidWxsc2hpdCIsICJidWxsc2hpdHRpbmciLCAiYnVzdHkiLCAiYnV0dGhvbGUiLCAiY2hpbmsiLCAiY2hpbmtzIiwgImNsaXQiLCAiY2xpdG9yaXMiLCAiY29jayIsICJjb2NrcyIsICJjb29uIiwgImNyYXAiLCAiY3JhcHB5IiwgImN1bSIsICJjdW1taW5nIiwgImN1bnQiLCAiY3VudHMiLCAiZGFtbiIsICJkYW1uZWQiLCAiZGljayIsICJkaWNraGVhZCIsICJkaWNrcyIsICJkaWxkbyIsICJkaWxkb3MiLCAiZGlwc2hpdCIsICJkb3VjaGUiLCAiZG91Y2hlYmFnIiwgImR5a2UiLCAiZHlrZXMiLCAiZWphY3VsYXRpb24iLCAiZXJvdGljIiwgImZhZyIsICJmYWdnb3QiLCAiZmFnZ290cyIsICJmYWdzIiwgImZ1Y2siLCAiZnVja2VkIiwgImZ1Y2tlciIsICJmdWNrZXJzIiwgImZ1Y2tlcnkiLCAiZnVja2luIiwgImZ1Y2tpbmciLCAiZnVja3MiLCAiZ2VuaXRhbHMiLCAiZ29kZGFtbiIsICJnb2RkYW1uZWQiLCAiZ29vayIsICJnb29rcyIsICJoYW5kam9iIiwgImhlbnRhaSIsICJoaXRsZXIiLCAiaG9lIiwgImhvZXMiLCAiaG9tbyIsICJob25reSIsICJob29rZXIiLCAiaG9ybnkiLCAiaG9yc2VzaGl0IiwgImh1bXBpbmciLCAiaW5jZXN0IiwgImludGVyY291cnNlIiwgImphcCIsICJqaWdhYm9vIiwgImppenoiLCAia2lrZSIsICJraWtlcyIsICJraW5reSIsICJra2siLCAibG9saXRhIiwgIm1hc3R1cmJhdGUiLCAibWFzdHVyYmF0aW5nIiwgIm1hc3R1cmJhdGlvbiIsICJtaWxmIiwgIm1vbGVzdCIsICJtb2xlc3RhdGlvbiIsICJtb2xlc3RlZCIsICJtb2xlc3RpbmciLCAibW9uZyIsICJtb3RoZXJmdWNrZXIiLCAibW90aGVyZnVja2VycyIsICJtb3RoZXJmdWNraW5nIiwgIm5hemkiLCAibmF6aXMiLCAibmVncm8iLCAibmVncm9lcyIsICJuaWdnYSIsICJuaWdnYXMiLCAibmlnZ2VyIiwgIm5pZ2dlcnMiLCAibmlwcGxlIiwgIm5pcHBsZXMiLCAibnNmdyIsICJudWRlIiwgIm51ZGVzIiwgIm51ZGl0eSIsICJvcmdhc20iLCAib3JnaWVzIiwgIm9yZ3kiLCAicGFlZG9waGlsZSIsICJwYW50aWVzIiwgInBhbnR5IiwgInBlZG8iLCAicGVkb3BoaWxlIiwgInBlZG9waGlsZXMiLCAicGVkb3BoaWxpYSIsICJwZW5pcyIsICJwZW5pc2VzIiwgInBpc3MiLCAicGlzc2VkIiwgInBpc3NpbmciLCAicGxheWJveSIsICJwb29mIiwgInBvcm4iLCAicG9ybmh1YiIsICJwb3JubyIsICJwb3Jub2dyYXBoaWMiLCAicG9ybm9ncmFwaHkiLCAicG9ybnN0YXIiLCAicG9ybnN0YXJzIiwgInByaWNrIiwgInByaWNrcyIsICJwdXNzaWVzIiwgInB1c3N5IiwgInJhZ2hlYWQiLCAicmFwZSIsICJyYXBlZCIsICJyYXBlcyIsICJyYXBpbmciLCAicmFwaXN0IiwgInJhcGlzdHMiLCAicmVjdHVtIiwgInJlZHNraW5zIiwgInJldGFyZCIsICJyZXRhcmRhdGlvbiIsICJyZXRhcmRlZCIsICJyZXRhcmRzIiwgInNjYXQiLCAic2VtZW4iLCAic2V4IiwgInNleHRpbmciLCAic2V4dWFsIiwgInNleHVhbGl0eSIsICJzZXh1YWxseSIsICJzZXh5IiwgInNoZW1hbGUiLCAic2hpdCIsICJzaGl0ZSIsICJzaGl0aGVhZCIsICJzaGl0aG9sZSIsICJzaGl0bG9hZCIsICJzaGl0cyIsICJzaGl0dGluZyIsICJzaGl0dHkiLCAic2x1dCIsICJzbHV0cyIsICJzbHV0dHkiLCAic211dCIsICJzb2RvbXkiLCAic3BpYyIsICJzcGljcyIsICJzd2FzdGlrYSIsICJzd2luZ2VyIiwgInRlc3RpY2xlcyIsICJ0aG90IiwgInRocmVlc29tZSIsICJ0aXQiLCAidGl0cyIsICJ0aXR0aWVzIiwgInRpdHR5IiwgInRvcGxlc3MiLCAidG93ZWxoZWFkIiwgInRyYW5ueSIsICJ0d2F0IiwgInR3YXRzIiwgInR3aW5rIiwgInZhZ2luYSIsICJ2YWdpbmFzIiwgInZpYWdyYSIsICJ2aWJyYXRvciIsICJ2dWx2YSIsICJ3YW5rIiwgIndhbmtlciIsICJ3ZXRiYWNrIiwgIndob3JlIiwgIndob3JlcyIsICJ3b3AiLCAieHh4Il0sICJyIjogWyJmdWNrIiwgInNoaXQiLCAiY3VudCIsICJuaWdnIiwgImZhZ2ciLCAid2hvcmUiLCAic2x1dCIsICJiaXRjaCIsICJ0d2F0IiwgIndhbmsiLCAiaml6eiIsICJyZXRhcmQiLCAiYXNzaG9sZSIsICJkaWxkbyIsICJwb3JuIiwgIm1vbGVzdCIsICJwZWRvcGhpbCIsICJtb3RoZXJmIiwgImNvY2tzdWNrIiwgImJsb3dqb2IiLCAiaGFuZGpvYiIsICJtYXN0dXJiIiwgInJhcGlzdCIsICJraWtlIiwgIm5hemkiLCAiaGl0bGVyIl19")) as { w: string[]; r: string[] };
const BAD = new Set(BL.w);
function nameProblem(raw: string) {
  const n = raw.trim().replace(/\s+/g, " ");
  if (n.length < 3 || n.length > 16) return "Use 3 to 16 characters.";
  if (!/^[A-Za-z0-9 ]+$/.test(n)) return "Use letters, numbers and spaces only.";
  if (/^(admin|administrator|moderator|mod|official|crank ?reports?|the ?daily ?crank)$/i.test(n)) return "That name is reserved.";
  const leet = n.toLowerCase().replace(/0/g, "o").replace(/1/g, "i").replace(/3/g, "e").replace(/4/g, "a")
    .replace(/5/g, "s").replace(/7/g, "t").replace(/8/g, "b").replace(/9/g, "g");
  for (const v of [leet, leet.replace(/i/g, "l")]) {
    const joined = v.replace(/\s+/g, "");
    if (v.split(" ").some((t) => BAD.has(t)) || BAD.has(joined) ||
        BL.r.some((r) => joined.includes(r)) || BL.w.some((w) => w.length >= 5 && joined.includes(w))) return "Pick a different name.";
  }
  return null;
}

// ---------- helpers ----------
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function dayOk(day: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const diff = (Date.parse(day + "T00:00:00Z") - Date.parse(new Date().toISOString().slice(0, 10) + "T00:00:00Z")) / 864e5;
  return diff >= -1 && diff <= 1;   // the player's local date, anywhere from UTC-12 to UTC+14
}
async function board(day: string, player: string) {
  const { data, error } = await db.rpc("dc_board", { p_day: day, p_player: player });
  if (error) throw error;
  return data;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const url = new URL(req.url);
  try {
    if (req.method === "GET" && url.pathname.endsWith("/board")) {
      const day = url.searchParams.get("day") ?? "", player = url.searchParams.get("player") ?? "";
      if (!dayOk(day) || !UUID.test(player)) return reply({ error: "Bad request." }, 400);
      return reply(await board(day, player));
    }
    if (req.method === "POST" && url.pathname.endsWith("/submit")) {
      const body = await req.json().catch(() => null);
      const player = String(body?.player ?? ""), day = String(body?.day ?? ""), nickname = String(body?.nickname ?? "").trim().replace(/\s+/g, " ");
      if (!UUID.test(player) || !dayOk(day)) return reply({ error: "Bad request." }, 400);
      const problem = nameProblem(nickname);
      if (problem) return reply({ error: problem, field: "nickname" }, 422);
      const all = await questions();
      const quizDay = quizDayFor(all, day);
      if (!quizDay) return reply({ error: "No questions yet." }, 503);
      let result;
      try { result = scoreTrip(all[quizDay], body?.picks); } catch { return reply({ error: "That trip couldn't be scored." }, 422); }
      const up = await db.from("dc_players").upsert({ id: player, nickname, updated_at: new Date().toISOString() });
      if (up.error) throw up.error;
      const ins = await db.from("dc_scores").insert({ player_id: player, day, quiz_day: quizDay, score: result.total, haul: result.haul });
      if (ins.error && ins.error.code !== "23505") throw ins.error;      // 23505 = already posted today; keep the first score
      return reply({ score: result.total, board: await board(day, player) });
    }
    return reply({ error: "Not found." }, 404);
  } catch (e) {
    console.error(e);
    return reply({ error: "Server error. Try again in a minute." }, 500);
  }
});
