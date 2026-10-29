/**
 * Generic OAuth2 Authorization Code + PKCE flow for Electron.
 *
 * Opens a BrowserWindow to the provider's auth URL, captures the redirect
 * via a custom protocol handler (compass://oauth/callback), exchanges the
 * code for tokens, and stores the credential in the vault.
 *
 * Token refresh is handled automatically when tokens are near expiration.
 */

import { BrowserWindow } from "electron";
import { handleMainIPC } from "./ipc";
import crypto from "crypto";
import http from "http";
import type { AddressInfo } from "net";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface OAuthProviderConfig {
  id: string;
  name: string;
  auth_url: string;
  token_url: string;
  client_id: string;
  client_secret?: string;
  scopes: string[];
  icon?: string;
  /** Some providers require additional params (e.g. Atlassian needs audience) */
  extra_auth_params?: Record<string, string>;
  /** For providers that use API keys instead of OAuth */
  api_key_only?: boolean;
}

interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
  token_type?: string;
  scope?: string;
}

interface PendingAuth {
  provider: OAuthProviderConfig;
  codeVerifier: string;
  state: string;
  resolve: (result: OAuthResult) => void;
  reject: (err: Error) => void;
}

export interface OAuthResult {
  provider: string;
  access_token: string;
  refresh_token?: string;
  expires_at?: number;
  scopes: string[];
}

// ---------------------------------------------------------------------------
// PKCE Helpers
// ---------------------------------------------------------------------------

function generateCodeVerifier(): string {
  return crypto.randomBytes(32).toString("base64url");
}

function generateCodeChallenge(verifier: string): string {
  return crypto.createHash("sha256").update(verifier).digest("base64url");
}

function generateState(): string {
  return crypto.randomBytes(16).toString("hex");
}

// ---------------------------------------------------------------------------
// OAuth Manager
// ---------------------------------------------------------------------------

let pendingAuth: PendingAuth | null = null;
let authWindow: BrowserWindow | null = null;
let loopbackServer: http.Server | null = null;

/** Providers that require http://localhost redirect (no custom protocol support). */
const LOOPBACK_PROVIDERS = new Set(["google"]);

/**
 * Start a temporary loopback HTTP server to capture the OAuth callback.
 * Returns the redirect URI with the assigned port.
 */
function startLoopbackServer(): Promise<{ server: http.Server; redirectUri: string }> {
  return new Promise((resolve, reject) => {
    const server = http.createServer();
    server.listen(0, "127.0.0.1", () => {
      const port = (server.address() as AddressInfo).port;
      const redirectUri = `http://127.0.0.1:${port}`;
      resolve({ server, redirectUri });
    });
    server.on("error", reject);
  });
}

/**
 * Start an OAuth2 Authorization Code + PKCE flow.
 *
 * Opens a BrowserWindow to the provider's auth URL. The user authenticates
 * in that window. On redirect, the code is exchanged for tokens.
 *
 * For providers that require loopback redirects (e.g. Google), a temporary
 * local HTTP server captures the callback. Others use the custom protocol.
 *
 * Returns the tokens on success, throws on failure or cancellation.
 */
export async function startOAuthFlow(
  provider: OAuthProviderConfig
): Promise<OAuthResult> {
  const useLoopback = LOOPBACK_PROVIDERS.has(provider.id);
  let redirectUri = "compass://oauth/callback";
  let loopback: { server: http.Server; redirectUri: string } | null = null;

  if (useLoopback) {
    loopback = await startLoopbackServer();
    redirectUri = loopback.redirectUri;
    loopbackServer = loopback.server;
  }

  return new Promise((resolve, reject) => {
    const codeVerifier = generateCodeVerifier();
    const codeChallenge = generateCodeChallenge(codeVerifier);
    const state = generateState();

    pendingAuth = { provider, codeVerifier, state, resolve, reject };

    // Build authorization URL
    const params = new URLSearchParams({
      response_type: "code",
      client_id: provider.client_id,
      redirect_uri: redirectUri,
      scope: provider.scopes.join(" "),
      state,
      code_challenge: codeChallenge,
      code_challenge_method: "S256",
      ...provider.extra_auth_params,
    });

    const authUrl = `${provider.auth_url}?${params.toString()}`;

    // Set up loopback server request handler
    if (loopback) {
      loopback.server.on("request", async (req, res) => {
        const reqUrl = new URL(req.url || "/", redirectUri);
        const code = reqUrl.searchParams.get("code");
        const cbState = reqUrl.searchParams.get("state");
        const error = reqUrl.searchParams.get("error");

        // Send a nice response to the browser
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(
          "<html><body style=\"font-family:system-ui;text-align:center;padding:60px;background:#1a1a2e;color:#fff\">" +
          "<h2>Connected!</h2><p>You can close this window and return to Compass.</p>" +
          "<script>window.close()</script></body></html>"
        );

        // Clean up server
        loopback!.server.close();
        loopbackServer = null;

        if (!pendingAuth) return;

        const { provider: p, codeVerifier: cv, state: expected, resolve: res2, reject: rej2 } = pendingAuth;
        pendingAuth = null;

        if (authWindow) {
          authWindow.close();
          authWindow = null;
        }

        if (error) {
          rej2(new Error(`OAuth error: ${error}`));
          return;
        }
        if (!code) {
          rej2(new Error("No authorization code received"));
          return;
        }
        if (cbState !== expected) {
          rej2(new Error("OAuth state mismatch — possible CSRF attack"));
          return;
        }

        try {
          const tokens = await exchangeCodeForTokens(p, code, cv, redirectUri);
          res2({
            provider: p.id,
            access_token: tokens.access_token,
            refresh_token: tokens.refresh_token,
            expires_at: tokens.expires_in ? Date.now() + tokens.expires_in * 1000 : undefined,
            scopes: tokens.scope ? tokens.scope.split(" ") : p.scopes,
          });
        } catch (err) {
          rej2(err instanceof Error ? err : new Error(String(err)));
        }
      });
    }

    // Open auth window
    authWindow = new BrowserWindow({
      width: 600,
      height: 700,
      show: true,
      title: `Sign in to ${provider.name}`,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
      },
    });

    authWindow.loadURL(authUrl);

    authWindow.on("closed", () => {
      authWindow = null;
      if (loopbackServer) {
        loopbackServer.close();
        loopbackServer = null;
      }
      if (pendingAuth) {
        const p = pendingAuth;
        pendingAuth = null;
        p.reject(new Error("OAuth flow cancelled by user"));
      }
    });
  });
}

/**
 * Handle the OAuth callback from the custom protocol.
 * Called when the app receives a compass://oauth/callback?code=...&state=... URL.
 */
export async function handleOAuthCallback(
  callbackUrl: string
): Promise<void> {
  if (!pendingAuth) {
    console.warn("[oauth] Received callback but no pending auth flow");
    return;
  }

  const url = new URL(callbackUrl);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const error = url.searchParams.get("error");

  const { provider, codeVerifier, state: expectedState, resolve, reject } =
    pendingAuth;
  pendingAuth = null;

  // Close the auth window
  if (authWindow) {
    authWindow.close();
    authWindow = null;
  }

  // Validate
  if (error) {
    reject(new Error(`OAuth error: ${error}`));
    return;
  }

  if (!code) {
    reject(new Error("No authorization code received"));
    return;
  }

  if (state !== expectedState) {
    reject(new Error("OAuth state mismatch — possible CSRF attack"));
    return;
  }

  // Exchange code for tokens
  try {
    const tokens = await exchangeCodeForTokens(provider, code, codeVerifier, "compass://oauth/callback");
    const result: OAuthResult = {
      provider: provider.id,
      access_token: tokens.access_token,
      refresh_token: tokens.refresh_token,
      expires_at: tokens.expires_in
        ? Date.now() + tokens.expires_in * 1000
        : undefined,
      scopes: tokens.scope
        ? tokens.scope.split(" ")
        : provider.scopes,
    };
    resolve(result);
  } catch (err) {
    reject(err instanceof Error ? err : new Error(String(err)));
  }
}

/**
 * Exchange an authorization code for access/refresh tokens.
 */
async function exchangeCodeForTokens(
  provider: OAuthProviderConfig,
  code: string,
  codeVerifier: string,
  redirectUri: string
): Promise<TokenResponse> {
  const params: Record<string, string> = {
    grant_type: "authorization_code",
    client_id: provider.client_id,
    code,
    redirect_uri: redirectUri,
    code_verifier: codeVerifier,
  };
  if (provider.client_secret) {
    params.client_secret = provider.client_secret;
  }
  const body = new URLSearchParams(params);

  const res = await fetch(provider.token_url, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
    },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `Token exchange failed (${res.status}): ${text}`
    );
  }

  return (await res.json()) as TokenResponse;
}

/**
 * Refresh an expired access token using a refresh token.
 */
export async function refreshAccessToken(
  provider: OAuthProviderConfig,
  refreshToken: string
): Promise<TokenResponse> {
  const refreshParams: Record<string, string> = {
    grant_type: "refresh_token",
    client_id: provider.client_id,
    refresh_token: refreshToken,
  };
  if (provider.client_secret) {
    refreshParams.client_secret = provider.client_secret;
  }
  const body = new URLSearchParams(refreshParams);

  const res = await fetch(provider.token_url, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Accept: "application/json",
    },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(
      `Token refresh failed (${res.status}): ${text}`
    );
  }

  return (await res.json()) as TokenResponse;
}

// ---------------------------------------------------------------------------
// IPC Registration — call this from main.ts
// ---------------------------------------------------------------------------

export function registerOAuthIPC(): void {
  handleMainIPC(
    "oauth-start",
    async (
      _event,
      providerConfig: OAuthProviderConfig
    ): Promise<OAuthResult> => {
      return startOAuthFlow(providerConfig);
    }
  );

  handleMainIPC(
    "oauth-refresh",
    async (
      _event,
      providerConfig: OAuthProviderConfig,
      refreshToken: string
    ) => {
      const tokens = await refreshAccessToken(providerConfig, refreshToken);
      return {
        access_token: tokens.access_token,
        refresh_token: tokens.refresh_token || refreshToken,
        expires_at: tokens.expires_in
          ? Date.now() + tokens.expires_in * 1000
          : undefined,
      };
    }
  );
}
