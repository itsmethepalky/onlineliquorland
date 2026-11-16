import { createClient } from "npm:@supabase/supabase-js@2";
import { importPKCS8, SignJWT } from "npm:jose@5.10.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "null",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const firebaseServiceAccountJson =
  Deno.env.get("FIREBASE_SERVICE_ACCOUNT_JSON") ?? "";

const adminClient = createClient(supabaseUrl, serviceRoleKey, {
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

function hasServiceRoleClaim(token: string): boolean {
  const parts = token.split(".");
  if (parts.length !== 3) return false;

  try {
    const encodedPayload = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = atob(
      encodedPayload.padEnd(Math.ceil(encodedPayload.length / 4) * 4, "="),
    );
    return JSON.parse(payload).role === "service_role";
  } catch {
    return false;
  }
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

function isInvalidRegistration(body: Record<string, unknown>): boolean {
  const error = body.error as
    | { details?: Array<Record<string, unknown>> }
    | undefined;
  const fcmError = error?.details?.find((item) =>
    String(item["@type"] ?? "").endsWith("FcmError")
  );
  const code = String(fcmError?.errorCode ?? "");
  return code === "UNREGISTERED" || code === "INVALID_ARGUMENT";
}

Deno.serve(async (request: Request) => {
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders });
  }
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "Method not allowed." });
  }
  if (!supabaseUrl || !serviceRoleKey || !firebaseServiceAccountJson) {
    console.error("Customer order push function is missing configuration.");
    return jsonResponse(503, { error: "Push notification service is not configured." });
  }

  const bearerMatch = (request.headers.get("Authorization") ?? "")
    .match(/^Bearer\s+(.+)$/i);
  if (!bearerMatch) {
    return jsonResponse(401, { error: "Unauthorized." });
  }
  if (!hasServiceRoleClaim(bearerMatch[1])) {
    return jsonResponse(403, { error: "Service access required." });
  }

  let orderId: number;
  let status: string;
  let requestedCustomerId: number;
  let requestedOrderNo: string;
  try {
    const body = await request.json();
    orderId = Number(body?.order_id);
    status = String(body?.status ?? "").trim().toLowerCase();
    requestedCustomerId = Number(body?.customer_id);
    requestedOrderNo = String(body?.order_no ?? "").trim();
  } catch {
    return jsonResponse(400, { error: "Invalid request body." });
  }
  if (!Number.isSafeInteger(orderId) || orderId <= 0 || !status) {
    return jsonResponse(400, { error: "Order ID and status are required." });
  }
  if (
    ![
      "pending",
      "confirmed",
      "processing",
      "shipped",
      "delivered",
      "cancelled",
    ].includes(status)
  ) {
    return jsonResponse(400, { error: "Invalid order status." });
  }

  let customerId: number;
  let orderNumber: string;
  if (
    status === "cancelled" &&
    Number.isSafeInteger(requestedCustomerId) &&
    requestedCustomerId > 0 &&
    requestedOrderNo
  ) {
    customerId = requestedCustomerId;
    orderNumber = requestedOrderNo;
  } else {
    const { data: order, error: orderError } = await adminClient
      .from("orders")
      .select("id,order_no,customer_id")
      .eq("id", orderId)
      .maybeSingle();
    if (orderError) {
      console.error("Could not load order for customer push.");
      return jsonResponse(503, { error: "Could not load the order." });
    }
    if (!order) {
      return jsonResponse(404, { error: "Order not found." });
    }
    customerId = order.customer_id;
    orderNumber = String(order.order_no ?? orderId);
  }

  const { data: customer, error: customerError } = await adminClient
    .from("customers")
    .select("user_id")
    .eq("id", customerId)
    .maybeSingle();
  if (customerError) {
    console.error("Could not load customer for order push.");
    return jsonResponse(503, { error: "Could not load the customer." });
  }
  if (!customer?.user_id) {
    return jsonResponse(200, { sent: 0, failed: 0 });
  }

  const { data: profile, error: profileError } = await adminClient
    .from("profiles")
    .select("id")
    .eq("id", customer.user_id)
    .eq("role", "customer")
    .eq("is_active", true)
    .maybeSingle();
  if (profileError) {
    console.error("Could not verify customer for order push.");
    return jsonResponse(503, { error: "Could not verify the customer." });
  }
  if (!profile) {
    return jsonResponse(200, { sent: 0, failed: 0 });
  }

  const { data: registrations, error: registrationError } = await adminClient
    .from("customer_push_tokens")
    .select("device_id,token")
    .eq("user_id", customer.user_id);
  if (registrationError) {
    console.error("Could not load customer push registrations.");
    return jsonResponse(503, { error: "Could not load push registrations." });
  }
  if (!registrations?.length) {
    return jsonResponse(200, { sent: 0, failed: 0 });
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

  let accessToken: string;
  try {
    accessToken = await getFirebaseAccessToken(serviceAccount);
  } catch {
    return jsonResponse(503, { error: "Firebase authorization failed." });
  }

  const sentCount = { sent: 0, failed: 0 };
  for (const registration of registrations) {
    let response: Response;
    try {
      response = await fetch(
        `https://fcm.googleapis.com/v1/projects/${encodeURIComponent(serviceAccount.project_id)}/messages:send`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${accessToken}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: {
              token: registration.token,
              data: {
                type: "order_status",
                order_id: String(orderId),
                order_no: orderNumber,
                status,
              },
              android: {
                priority: "HIGH",
                ttl: "86400s",
              },
            },
          }),
        },
      );
    } catch {
      sentCount.failed++;
      continue;
    }

    const result = await response.json().catch(() => ({}));
    if (response.ok) {
      sentCount.sent++;
      continue;
    }
    if (isInvalidRegistration(result)) {
      const { error } = await adminClient
        .from("customer_push_tokens")
        .delete()
        .eq("device_id", registration.device_id)
        .eq("token", registration.token);
      if (error) {
        console.error("Could not remove invalid customer push token.");
      }
    } else {
      console.error("Customer FCM delivery failed:", response.status);
    }
    sentCount.failed++;
  }

  return jsonResponse(200, sentCount);
});
