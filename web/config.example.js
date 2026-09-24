// Copy to config.js and fill in (config.js is git-ignored). Deployment settings for the ITOps web chat. None of these are secrets.
// If you change apiBase, also update connect-src in index.html's Content-Security-Policy.
export const CONFIG = {
  tenantId: "<tenant-id>",
  clientId: "<ITOps API app client-id>", // "ITOps API" app registration
  apiBase: "https://<api-id>.execute-api.<region>.amazonaws.com",
  adminRole: "ITOps.Admin",
};
