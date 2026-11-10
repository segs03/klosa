const b64uToBuf = (s) => {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)).buffer;
};
const bufToB64u = (b) =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]").value;
const post = (url, data) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
    body: JSON.stringify(data || {}),
  });

async function passkeyLogin(urls, next) {
  const opts = await (await post(urls.options)).json();
  opts.challenge = b64uToBuf(opts.challenge);
  (opts.allowCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));

  const cred = await navigator.credentials.get({ publicKey: opts });
  const r = cred.response;
  const res = await post(urls.verify, {
    next,
    credential: {
      id: cred.id,
      rawId: bufToB64u(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64u(r.clientDataJSON),
        authenticatorData: bufToB64u(r.authenticatorData),
        signature: bufToB64u(r.signature),
        userHandle: r.userHandle ? bufToB64u(r.userHandle) : null,
      },
      clientExtensionResults: cred.getClientExtensionResults(),
    },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Sign-in failed.");
  window.location.href = data.next;
}

async function passkeyRegister(urls, name) {
  const opts = await (await post(urls.options)).json();
  opts.challenge = b64uToBuf(opts.challenge);
  opts.user.id = b64uToBuf(opts.user.id);
  (opts.excludeCredentials || []).forEach((c) => (c.id = b64uToBuf(c.id)));

  const cred = await navigator.credentials.create({ publicKey: opts });
  const r = cred.response;
  const res = await post(urls.verify, {
    name,
    credential: {
      id: cred.id,
      rawId: bufToB64u(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufToB64u(r.clientDataJSON),
        attestationObject: bufToB64u(r.attestationObject),
        transports: r.getTransports ? r.getTransports() : [],
      },
      clientExtensionResults: cred.getClientExtensionResults(),
    },
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "Could not add passkey.");
  return true;
}