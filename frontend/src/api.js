async function req(path, opts = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const msg = data?.detail || data?.error || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export const api = {
  dashboard: () => req("/dashboard"),
  slots: () => req("/slots"),
  executors: () => req("/executors"),
  windows: () => req("/windows"),
  leases: () => req("/leases"),
  conflicts: () => req("/conflicts"),
  openLease: (window_code) =>
    req("/leases/open", { method: "POST", body: JSON.stringify({ window_code }) }),
  closeLease: (lease_no) =>
    req("/leases/close", { method: "POST", body: JSON.stringify({ lease_no }) }),
};
