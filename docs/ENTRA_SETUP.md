# Entra ID setup

Two app registrations, kept separate on purpose:

| App | Used by | Holds |
|---|---|---|
| **ITOps API** | API Gateway JWT authorizer | App roles (`ITOps.Admin`), no secrets |
| **ITOps Graph Automation** (optional) | `graph.py` in the Lambda | Graph application permissions + client secret |

## A. ITOps API (authentication for the chatbot)

1. **Entra admin center → App registrations → New registration**
   - Name `ITOps API`, single tenant, no redirect URI.
2. **Expose an API**
   - Set the Application ID URI to `api://<client-id>`.
   - Add a scope `access_as_user` (admins and users can consent).
   - **Authorized client applications:** add `04b07795-8ddb-461a-bbee-02f9e1bf7b46` (the Azure CLI) with
     that scope, so `az account get-access-token` works for testing. Also add your chat front-end's client ID.
3. **App roles → Create app role**
   - Display name `ITOps Admin`, allowed member types *Users/Groups*, value **`ITOps.Admin`**.
4. **Manifest:** set `"requestedAccessTokenVersion": 2`. In the older manifest format this is
   `"accessTokenAcceptedVersion": 2`. Without it Entra issues v1 tokens, the issuer becomes
   `https://sts.windows.net/<tenant>/`, and the authorizer rejects every call.
5. **Enterprise applications → ITOps API**
   - *Properties → Assignment required = Yes* (only assigned staff can get tokens).
   - *Users and groups* → assign all staff (default access) and assign your IT team the `ITOps Admin` role.
6. Put these values in `infra/cdk.local.json` (copy `cdk.local.example.json`; it's git-ignored):
   ```json
   "jwtIssuer":   "https://login.microsoftonline.com/<tenant-id>/v2.0",
   "jwtAudience": "<ITOps API client-id GUID>"
   ```
   The `aud` claim of a v2 access token is the client-ID GUID, not the `api://` URI.

## B. ITOps Graph Automation (account actions)

1. **New registration** `ITOps Graph Automation`, single tenant.
2. **API permissions → Microsoft Graph → Application permissions:**
   - `User.ReadWrite.All`: create users, reset passwords, revoke sessions
   - `GroupMember.ReadWrite.All`: add group members
   - Click **Grant admin consent**.
3. **Directory role (required for app-only password resets):**
   *Roles & admins → User Administrator → Add assignments → select the app's service principal.*
   - **Least-privilege option:** create an **Administrative Unit** containing only staff accounts and
     assign User Administrator *scoped to that AU*. Automation then cannot touch anyone outside it.
     Entra already blocks a User Administrator from resetting Global Admin and other privileged accounts.
4. **Certificates & secrets → New client secret** (maximum 12 months; set a calendar reminder to rotate it).
   Store it with `tools\put-secrets.ps1 -IncludeGraph`. Never put it in any config file.
5. `infra/cdk.local.json`:
   ```json
   "graphTenantId": "<tenant-id>",
   "graphClientId": "<automation app client-id>",
   "graphGroupAllowlist": { "Sales Team": "<group-object-id>", "VPN Users": "<group-object-id>" },
   "allowedUpnDomains": "contoso.com",
   "protectedUpns": "ceo@contoso.com,breakglass@contoso.com"
   ```
   Only groups in `graphGroupAllowlist` can be assigned. Role-assignable groups are refused even if listed.

## Notes
- The temporary password from a reset or user creation is returned **once**, to the approving admin in
  the `/approve` response. It is never written to DynamoDB or the logs. Deliver it to the user out-of-band.
- A password reset also revokes the user's refresh tokens, so an attacker's sessions end along with the old password.
- Users synced from on-prem AD can only be reset through Graph if **password writeback** (Entra ID P1) is
  enabled. Otherwise use the AD actions.

## C. Web chat sign-in

On the **ITOps API** app: **Authentication → + Add a platform → Single-page application**, redirect URI
`http://localhost:5173/` (plus your hosted URL later, with the trailing slash). Then **API permissions →
APIs my organization uses → ITOps API → `access_as_user` → Grant admin consent**, so staff aren't asked to
consent on first sign-in. Add the hosted origin (no trailing slash) to `allowedOrigins` in `infra/cdk.json`.
