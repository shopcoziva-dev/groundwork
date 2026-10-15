// Keeps GTS Holdings' copy of every signed Groundwork agreement.
// The page sends the exact agreement text it showed, the plan and the typed signature. This function checks the
// fingerprint, adds the server time, IP and browser, and saves both a readable .txt and a .json record to a PRIVATE
// GitHub repo. Settings (Vercel > Project > Settings > Environment Variables):
//   GW_SIGN_TOKEN  fine-grained GitHub token, Contents: Read and write, only on the agreements repo
//   GW_SIGN_REPO   owner/name of that private repo (default shopcoziva-dev/groundwork-agreements)
const crypto = require('crypto');

function json(res, code, body){ res.statusCode = code; res.setHeader('Content-Type', 'application/json'); res.setHeader('Cache-Control', 'no-store'); res.end(JSON.stringify(body)); }
function slug(t){ return String(t).toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'signer'; }

async function put(repo, token, path, content, message){
  const r = await fetch('https://api.github.com/repos/' + repo + '/contents/' + path, { method: 'PUT',
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json', 'User-Agent': 'groundwork-sign' },
    body: JSON.stringify({ message, content: Buffer.from(content, 'utf8').toString('base64') }) });
  if (!r.ok){ const t = await r.text(); throw new Error('GitHub ' + r.status + ': ' + t.slice(0, 160)); }
  return r.json();
}

module.exports = async function(req, res){
  if (req.method !== 'POST') return json(res, 405, { ok: false, why: 'POST only' });
  const token = process.env.GW_SIGN_TOKEN, repo = process.env.GW_SIGN_REPO || 'shopcoziva-dev/groundwork-agreements';
  if (!token) return json(res, 503, { ok: false, why: 'not-configured' });
  let b = req.body;
  if (typeof b === 'string'){ try { b = JSON.parse(b); } catch (e) { b = null; } }
  if (!b || typeof b !== 'object') return json(res, 400, { ok: false, why: 'no body' });
  const product = String(b.product || ''), plan = String(b.plan || ''), name = String(b.name || '').trim(), email = String(b.email || '').trim(),
    text = String(b.text || ''), hash = String(b.hash || ''), version = String(b.v || ''), signedAt = Number(b.at) || 0;
  if (!/^(beta|solo)$/.test(product)) return json(res, 400, { ok: false, why: 'product' });
  if (!/^(once|partner)$/.test(plan)) return json(res, 400, { ok: false, why: 'plan' });
  if (name.length < 3 || name.length > 120 || name.split(/\s+/).length < 2) return json(res, 400, { ok: false, why: 'name' });
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email) || email.length > 200) return json(res, 400, { ok: false, why: 'email' });
  if (text.length < 500 || text.length > 60000) return json(res, 400, { ok: false, why: 'text' });
  const fp = crypto.createHash('sha256').update(text, 'utf8').digest('hex');
  if (fp !== hash) return json(res, 400, { ok: false, why: 'fingerprint does not match the agreement text' });
  const now = new Date(), ip = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || (req.socket && req.socket.remoteAddress) || '',
    ua = String(req.headers['user-agent'] || '').slice(0, 300), origin = String(req.headers.origin || req.headers.referer || '').slice(0, 200);
  const id = now.toISOString().replace(/[-:]/g, '').replace(/\..*/, '') + '-' + product + '-' + slug(name) + '-' + fp.slice(0, 8);
  const dir = 'signed/' + now.toISOString().slice(0, 7) + '/';
  const record = { id, product, plan, agreement_version: version, signer: { name, email }, signed_at_browser: signedAt ? new Date(signedAt).toISOString() : null,
    received_at: now.toISOString(), ip, user_agent: ua, page: origin, fingerprint_sha256: fp, text };
  const readable = text + '\n\n---\nSigned electronically by ' + name + ' <' + email + '>\nPlan: ' + plan + ' · Product: Groundwork ' + product + ' · Agreement version ' + version +
    '\nSigned (signer\'s browser): ' + record.signed_at_browser + '\nReceived by GTS Holdings: ' + record.received_at + '\nIP: ' + ip + '\nBrowser: ' + ua + '\nFingerprint (SHA-256): ' + fp + '\n';
  try {
    await put(repo, token, dir + id + '.json', JSON.stringify(record, null, 1) + '\n', 'Signed: ' + product + ' · ' + name);
    await put(repo, token, dir + id + '.txt', readable, 'Signed copy: ' + product + ' · ' + name);
  } catch (e) { return json(res, 502, { ok: false, why: 'could not save the copy' }); }
  return json(res, 200, { ok: true, id });
};
