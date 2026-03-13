# Bug Report: Google Console UI Does Not Display Client Secret for Desktop OAuth Clients

**Date**: 2026-02-21
**Status**: Identified — Google Console UI issue, not a protocol change
**Severity**: Blocks setup for users who encounter the UI bug
**Affects**: Consumer Gmail and Workspace accounts using the newer Google Auth Platform console UI

---

## Symptoms

When creating a **Desktop application** OAuth 2.0 client in the Google Cloud Console:

1. The confirmation screen after creation shows only the **Client ID** — no Client Secret is visible
2. The **"Download JSON"** button does nothing (no file downloaded, no browser prompt, no blocked-download indicator)
3. Navigating to the client's detail page also does not show a secret

This makes it impossible to set `APPIF_GMAIL_CLIENT_SECRET` in `~/.env`, which blocks `scripts/gmail_consent.py`.

---

## Root Cause

This is a **Google Cloud Console UI rendering bug** in the newer "Google Auth Platform" interface, not a change to how Desktop OAuth clients work.

**Desktop clients still have a client secret.** Google generates both a `client_id` and `client_secret` for every Desktop application OAuth client. The `google-auth-oauthlib` `InstalledAppFlow` requires and uses both values. Desktop clients are **not** public clients — they are "installed application" clients with a secret that is considered non-confidential (because it's embedded in distributed apps), but it still exists and is required by the token exchange endpoint.

The secret is present in Google's backend — it's just not being displayed by the console UI in some cases.

---

## Resolution

The secret created by the new Google Auth Platform UI exists but **cannot be revealed** — it is listed only in masked form (e.g. `GOCSPX-****...`). There is no "show" button, no copy option, and the "Download JSON" button is non-functional. The secret that was auto-created with the client is effectively inaccessible.

**The only working path** is to use the **legacy Credentials page** and create a **new** client secret there:

1. Navigate to the legacy Credentials page:
   ```
   https://console.cloud.google.com/apis/credentials?project=YOUR_PROJECT_ID
   ```
   Replace `YOUR_PROJECT_ID` with your actual project ID.

2. Click on the Desktop application client that was created in the new UI.

3. Under **Client secrets**, you will see the masked secret created by the new UI. You cannot reveal it.

4. Click **Add Secret** to create a new secret. The legacy UI will display the full secret value — **copy it immediately**.

5. Set both values in `~/.env`:
   ```bash
   APPIF_GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
   APPIF_GMAIL_CLIENT_SECRET=the-new-secret-you-just-created
   ```

> **Important**: Do not attempt to use the masked secret from the new UI. It exists but is not retrievable. The new secret you create in the legacy UI is a separate, additional secret for the same client — both are valid, but only the one from the legacy UI can be copied.

## Other Workarounds (Not Required)

These are documented for completeness but are not needed — creating a new secret via the legacy UI (above) is the confirmed fix.

### Use the Google Cloud CLI

```bash
gcloud auth application-default login --client-id-file=<path-to-downloaded-json>
```

### Delete and Recreate the Client

Delete the client entirely and recreate it. This sometimes causes the creation confirmation dialog to show the secret. However, creating a new secret on the existing client (above) is simpler.

---

## What This Is NOT

- **NOT a change to OAuth for Desktop apps**: Desktop clients still get and require a client secret. Google has not switched them to PKCE-only / public-client mode.
- **NOT a reason to use Web application clients**: While Web application clients with a `localhost` redirect URI would work, it adds unnecessary complexity. Desktop application is the correct client type for locally-run scripts.
- **NOT a code change we need to make**: Our consent script correctly requires both `APPIF_GMAIL_CLIENT_ID` and `APPIF_GMAIL_CLIENT_SECRET`. The issue is obtaining those values from the console, not a problem with our implementation.

---

## Verification

To confirm the secret exists even when the UI doesn't show it, you can:

1. Navigate to `https://console.cloud.google.com/apis/credentials?project=YOUR_PROJECT_ID`
2. Click on the Desktop client name
3. The Client Secret should be visible in the right panel under "Client secrets"
4. If using the newer Google Auth Platform UI at `https://console.cloud.google.com/auth/clients`, try switching back to the legacy path above

---

## References

- Google OAuth 2.0 for Desktop Apps: https://developers.google.com/identity/protocols/oauth2/native-app
- Google Cloud Console: https://console.cloud.google.com/apis/credentials
- Google Auth Platform (newer UI): https://console.cloud.google.com/auth/clients