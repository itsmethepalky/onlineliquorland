import { createClient } from "npm:@supabase/supabase-js@2";
import { importPKCS8, SignJWT } from "npm:jose@5.10.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "null",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const supabaseAnonKey = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const firebaseServiceAccountJson =
  Deno.env.get("FIREBASE_SERVICE_ACCOUNT_JSON") ?? "";

const adminClient = createClient(supabaseUrl, serviceRoleKey, {
  auth: {
    autoRefreshToken: false,
    persistSession: false,
  },
});
const claimsClient = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    autoRefreshToken: false,
    persistSession: false,
  },
});

type FirebaseServiceAccount = {
  client_email: string;
  private_key: string;
  project_id: string;
};

let cachedAccessToken = "";
let cachedAccessTokenExpiresAt = 0;

function jsonResponse(status: number, body: Record<string, unknown>) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...corsHeaders,
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

async function getFirebaseAccessToken(
  account: FirebaseServiceAccount,
): Promise<string> {
  if (
    cachedAccessToken &&
    cachedAccessTokenExpiresAt > Date.now() + 60_000
  ) {
    return cachedAccessToken;
  }

  const now = Math.floor(Date.now() / 1000);
  const privateKey = await importPKCS8(account.private_key, "RS256");
  const assertion = await new SignJWT({
    scope: "https://www.googleapis.com/auth/firebase.messaging",
  })
    .setProtectedHeader({ alg: "RS256", typ: "JWT" })
    .setIssuer(account.client_email)
    .setSubject(account.client_email)
    .setAudience("https://oauth2.googleapis.com/token")
    .setIssuedAt(now)
    .setExpirationTime(now + 3600)
    .sign(privateKey);

  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion,
    }),
  });

  if (!response.ok) {
    console.error("FCM OAuth token request failed:", response.status);
    throw new Error("Unable to authorize Firebase message delivery.");
  }

  const result = await response.json();
  if (
    typeof result.access_token !== "string" ||
    typeof result.expires_in !== "number"
  ) {
    throw new Error("Firebase returned an invalid OAuth token response.");
  }

  cachedAccessToken = result.access_token;
  cachedAccessTokenExpiresAt = Date.now() + result.expires_in * 1000;
  return cachedAccessToken;
}

function getFcmErrorCode(body: Record<string, unknown>): string {
  const error = body.error as
    | { status?: string; details?: Array<Record<string, unknown>> }
    | undefined;
  const detail = error?.details?.find((item) =>
    String(item["@type"] ?? "").endsWith("FcmError")
  );
  return String(detail?.errorCode ?? error?.status ?? "fcm_send_failed");
}

function isInvalidRegistrationError(body: Record<string, unknown>): boolean {
  const error = body.error as
    | { details?: Array<Record<string, unknown>> }
    | undefined;
  const fcmError = error?.details?.find((item) =>
    String(item["@type"] ?? "").endsWith("FcmError")
  );
  const code = String(fcmError?.errorCode ?? "");
  return code === "UNREGISTERED" || code === "INVALID_ARGUMENT";
}

async function deactivateToken(registration: {
  token: string;
  user_id: string;
  browser_id: string;
}) {
  const { error } = await adminClient
    .from("admin_push_tokens")
    .update({
      is_active: false,
      admin_notification_eligible: false,
      updated_at: new Date().toISOString(),
    })
    .eq("token", registration.token)
    .eq("user_id", registration.user_id)
    .eq("browser_id", registration.browser_id);

  if (error) {
    console.error("Could not deactivate invalid FCM registration.");
    throw new Error("Could not update an invalid FCM registration.");
  }
}

async function finishDelivery(
  orderId: number,
  browserId: string,
  sent: boolean,
  errorCode: string | null = null,
) {
  const { data, error } = await adminClient.rpc(
    "finish_admin_order_push_delivery",
    {
      p_order_id: orderId,
      p_browser_id: browserId,
      p_sent: sent,
      p_error_code: errorCode,
    },
  );

  if (error || data !== true) {
    console.error("Could not finish admin push delivery record.");
    throw new Error("Could not record admin push delivery.");
  }
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }

  if (request.method !== "POST") {
    return jsonResponse(405, { error: "Method not allowed." });
  }

  if (
    !supabaseUrl ||
    !supabaseAnonKey ||
    !serviceRoleKey ||
    !firebaseServiceAccountJson
  ) {
    console.error("Admin order push function is missing server configuration.");
    return jsonResponse(503, { error: "Push notification service is not configured." });
  }

  const authorization = request.headers.get("Authorization") ?? "";
  const bearerMatch = authorization.match(/^Bearer\s+(.+)$/i);
  if (!bearerMatch) {
    return jsonResponse(401, { error: "Unauthorized." });
  }

  const { data: claimData, error: claimsError } =
    await claimsClient.auth.getClaims(bearerMatch[1]);
  const claims = claimData?.claims;
  if (claimsError || claims?.role !== "service_role") {
    return jsonResponse(403, { error: "Service access required." });
  }

  let orderId: number;
  try {
    const body = await request.json();
    orderId = Number(body?.order_id);
  } catch {
    return jsonResponse(400, { error: "Invalid request body." });
  }
  if (!Number.isSafeInteger(orderId) || orderId <= 0) {
    return jsonResponse(400, { error: "A valid order ID is required." });
  }

  const { data: order, error: orderError } = await adminClient
    .from("orders")
    .select("id,order_no,total_amount")
    .eq("id", orderId)
    .maybeSingle();

  if (orderError) {
    console.error("Could not load order for admin push.");
    return jsonResponse(503, { error: "Could not load the order." });
  }
  if (!order) {
    return jsonResponse(404, { error: "Order not found." });
  }

  const { data: registrations, error: registrationError } = await adminClient
    .from("admin_push_tokens")
    .select("token,user_id,browser_id")
    .eq("platform", "web")
    .eq("role", "admin")
    .eq("is_active", true)
    .eq("admin_notification_eligible", true);

  if (registrationError) {
    console.error("Could not load admin push registrations.");
    return jsonResponse(503, { error: "Could not load push registrations." });
  }
  if (!registrations?.length) {
    return jsonResponse(200, { sent: 0, skipped: 0, failed: 0 });
  }

  const userIds = [...new Set(registrations.map((item) => item.user_id))];
  const { data: profiles, error: profileError } = await adminClient
    .from("profiles")
    .select("id")
    .in("id", userIds)
    .eq("role", "admin")
    .eq("is_active", true);

  if (profileError) {
    console.error("Could not verify admin push registration roles.");
    return jsonResponse(503, { error: "Could not verify admin registrations." });
  }

  const activeAdminIds = new Set((profiles ?? []).map((profile) => profile.id));
  const eligibleRegistrations = registrations.filter((item) =>
    activeAdminIds.has(item.user_id) &&
    typeof item.token === "string" &&
    typeof item.browser_id === "string"
  );
  if (!eligibleRegistrations.length) {
    return jsonResponse(200, { sent: 0, skipped: 0, failed: 0 });
  }

  let serviceAccount: FirebaseServiceAccount;
  try {
    serviceAccount = JSON.parse(firebaseServiceAccountJson);
  } catch {
    console.error("Firebase service account secret is not valid JSON.");
    return jsonResponse(503, { error: "Firebase is not configured correctly." });
  }
  if (
    !serviceAccount.client_email ||
    !serviceAccount.private_key ||
    !serviceAccount.project_id
  ) {
    console.error("Firebase service account secret is missing required fields.");
    return jsonResponse(503, { error: "Firebase is not configured correctly." });
  }

  let fcmAccessToken: string;
  try {
    fcmAccessToken = await getFirebaseAccessToken(serviceAccount);
  } catch {
    return jsonResponse(503, { error: "Firebase authorization failed." });
  }

  const orderNumber = String(order.order_no ?? orderId);
  let sent = 0;
  let skipped = 0;
  let failed = 0;

  for (const registration of eligibleRegistrations) {
    const { data: claimed, error: claimError } = await adminClient.rpc(
      "claim_admin_order_push_delivery",
      {
        p_order_id: orderId,
        p_browser_id: registration.browser_id,
      },
    );

    if (claimError) {
      console.error("Could not claim admin order push delivery.");
      failed++;
      continue;
    }
    if (claimed !== true) {
      skipped++;
      continue;
    }

    let response: Response;
    try {
      response = await fetch(
        `https://fcm.googleapis.com/v1/projects/${encodeURIComponent(serviceAccount.project_id)}/messages:send`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${fcmAccessToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: {
              token: registration.token,
              data: {
                type: "new_order",
                title: `New order ${orderNumber}`,
                body: `A new order of NPR ${order.total_amount} has been placed.`,
                order_id: String(orderId),
                order_no: orderNumber,
              },
              android: {
                priority: "HIGH",
                ttl: "86400s",
              },
              webpush: {
                headers: { Urgency: "high", TTL: "86400" },
              },
            },
          }),
        },
      );
    } catch {
      await finishDelivery(orderId, registration.browser_id, false, "network_error");
      failed++;
      continue;
    }

    const result = await response.json().catch(() => ({}));
    if (response.ok) {
      await finishDelivery(orderId, registration.browser_id, true);
      sent++;
      continue;
    }

    const errorCode = getFcmErrorCode(result);
    if (isInvalidRegistrationError(result)) {
      await deactivateToken(registration);
      await finishDelivery(orderId, registration.browser_id, false, errorCode);
      skipped++;
      continue;
    }

    await finishDelivery(orderId, registration.browser_id, false, errorCode);
    failed++;
    console.error("FCM admin delivery failed:", response.status, errorCode);
  }

  return jsonResponse(200, { sent, skipped, failed });
});
