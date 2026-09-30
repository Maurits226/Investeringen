// vault.js — versleuteling van je persoonlijke gegevens (state.json en radar.json).
//
// De repository is openbaar (nodig voor gratis GitHub Pages). Zonder versleuteling kan iedereen je
// aankoopprijzen, verkopen en opgevolgde adviezen lezen. Met dit bestand worden die gegevens versleuteld
// opgeslagen; de sleutel is je GitHub-token, die alleen in jouw browser staat.
//
// Methode: AES-GCM 256 bit. De sleutel wordt afgeleid van de token met PBKDF2-SHA256
// (210.000 rondes) en een willekeurig zout per keer opslaan. Zonder token is de inhoud onleesbaar.
const Vault = (() => {
  const ITER = 210000;
  const te = new TextEncoder(), td = new TextDecoder();

  const toB64 = buf => {
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  };
  const fromB64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));

  async function deriveKey(secret, salt, iter) {
    const base = await crypto.subtle.importKey('raw', te.encode(secret), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' },
      base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt']);
  }

  // Versleutel een object met de token; resultaat is een klein JSON-object dat veilig openbaar kan staan
  async function lock(obj, secret) {
    if (!secret) throw new Error('geen token');
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(secret, salt, ITER);
    const data = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, te.encode(JSON.stringify(obj)));
    return { vault: 1, alg: 'AES-GCM-256', kdf: 'PBKDF2-SHA256', iter: ITER,
             salt: toB64(salt), iv: toB64(iv), data: toB64(data),
             note: 'Versleuteld. Alleen te openen met de juiste sleutel.' };
  }

  // Ontsleutel; gooit een fout als de token niet klopt (bijv. na het vervangen van je token)
  async function unlock(box, secret) {
    if (!secret) throw new Error('geen token');
    const key = await deriveKey(secret, fromB64(box.salt), box.iter || ITER);
    const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromB64(box.iv) }, key, fromB64(box.data));
    return JSON.parse(td.decode(plain));
  }

  const isLocked = x => !!(x && typeof x === 'object' && x.vault === 1 && x.data && x.salt && x.iv);

  // Hulpjes voor de GitHub-API: base64 <-> UTF-8 tekst
  const b64ToText = b64 => td.decode(Uint8Array.from(atob(b64.replace(/\n/g, '')), c => c.charCodeAt(0)));
  const textToB64 = text => toB64(te.encode(text));

  return { lock, unlock, isLocked, b64ToText, textToB64 };
})();
