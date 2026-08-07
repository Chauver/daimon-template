/**
 * worker.js — Porte d'accès à l'appli Daïmon (Cloudflare Workers).
 *
 * Verrou par COOKIE (et non plus Basic Auth) : le Basic Auth HTTP ne s'affiche pas dans une PWA iOS
 * en mode standalone (lancée depuis l'icône) → 401 « Accès privé ». Ici, une page de login pose un
 * cookie signé qui persiste en standalone. Plus de boîte de dialogue navigateur.
 *
 * Le mot de passe reste la variable d'environnement APP_PASSWORD (Cloudflare > Settings > Variables
 * and Secrets, type Secret). Le cookie = HMAC-SHA256(APP_PASSWORD, "daimon-v1") : impossible à forger
 * sans le mot de passe, et invalidé automatiquement si on change APP_PASSWORD.
 *
 * "run_worker_first" (wrangler.jsonc) fait tourner ce Worker sur CHAQUE requête.
 */

const COOKIE = "dai_auth";
const MSG = "daimon-v1";
const MAX_AGE = 60 * 60 * 24 * 180; // 180 jours

const enc = new TextEncoder();

async function token(secret) {
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(MSG));
  return btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function hasCookie(request, value) {
  const raw = request.headers.get("Cookie") || "";
  return raw.split(/;\s*/).some((c) => c === `${COOKIE}=${value}`);
}

function loginPage(error) {
  const msg = error
    ? '<p class="err">Mot de passe incorrect.</p>'
    : '<p class="sub">Accès privé — entre ton mot de passe.</p>';
  const html = `<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Δaï — accès</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;background:#08182E;
    color:#E7EEF4;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:24px}
  .card{width:100%;max-width:320px;text-align:center}
  .logo{font-size:44px;font-weight:800;letter-spacing:-.02em;margin:0 0 4px;color:#46B3BE}
  h1{font-size:18px;font-weight:600;margin:0 0 6px}
  .sub,.err{font-size:14px;margin:0 0 20px;color:#9FB2C2}
  .err{color:#E5806F}
  form{display:flex;flex-direction:column;gap:12px}
  input{font-size:17px;padding:13px 15px;border-radius:12px;border:1px solid #22384C;
    background:#101F2D;color:#E7EEF4;width:100%}
  input:focus{outline:2px solid #46B3BE;border-color:#46B3BE}
  button{font-size:16px;font-weight:600;padding:13px;border-radius:12px;border:none;
    background:#46B3BE;color:#052229;cursor:pointer}
  button:active{opacity:.85}
</style></head><body>
<div class="card">
  <p class="logo">Δaï</p>
  <h1>Cap sur Les Sables 2027</h1>
  ${msg}
  <form method="POST" action="/__login">
    <input type="password" name="password" placeholder="Mot de passe" autofocus
      autocomplete="current-password" required>
    <button type="submit">Entrer</button>
  </form>
</div></body></html>`;
  return new Response(html, {
    status: error ? 401 : 200,
    headers: {
      "Content-Type": "text/html; charset=UTF-8",
      "Cache-Control": "no-store",
      "X-Daimon-Auth": "login", // le service worker ne met PAS cette page en cache
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Ressources de MARQUE (icônes + manifeste) : publiques, sinon iOS ne récupère pas l'icône.
    if (url.pathname.startsWith("/icons/") || url.pathname === "/manifest.webmanifest") {
      return env.ASSETS.fetch(request);
    }

    const PASS = env.APP_PASSWORD;
    if (!PASS) {
      return new Response(
        "Configuration manquante : définis APP_PASSWORD dans Cloudflare (Settings > Variables and Secrets).",
        { status: 503, headers: { "Content-Type": "text/plain; charset=UTF-8" } }
      );
    }

    const tok = await token(PASS);

    // Soumission du formulaire de login.
    if (request.method === "POST" && url.pathname === "/__login") {
      const form = await request.formData().catch(() => null);
      if (form && form.get("password") === PASS) {
        return new Response(null, {
          status: 303,
          headers: {
            "Location": "./",
            "Set-Cookie": `${COOKIE}=${tok}; Max-Age=${MAX_AGE}; Path=/; HttpOnly; Secure; SameSite=Lax`,
          },
        });
      }
      return loginPage(true);
    }

    // Déjà authentifié → on sert l'appli.
    if (hasCookie(request, tok)) {
      return env.ASSETS.fetch(request);
    }

    // Non authentifié : page de login pour une navigation HTML, 401 sec pour le reste (données/JS).
    const accept = request.headers.get("Accept") || "";
    if (request.method === "GET" && accept.includes("text/html")) {
      return loginPage(false);
    }
    return new Response("Accès privé — Daïmon", {
      status: 401,
      headers: { "Content-Type": "text/plain; charset=UTF-8" },
    });
  },
};
