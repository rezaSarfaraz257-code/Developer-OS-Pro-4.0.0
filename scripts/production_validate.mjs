#!/usr/bin/env node
/**
 * Production black-box validation for Developer OS.
 *
 * Required:
 *   PRODUCTION_BASE_URL=https://...
 *   PRODUCTION_BILLING_WEBHOOK_SECRET=...
 *
 * This intentionally creates disposable users and exercises real HTTP
 * contracts. It never fabricates a successful billing state: the billing
 * assertion only passes when the deployed webhook accepts a valid signature
 * and upgrades the disposable account.
 */
import crypto from "node:crypto";

const base = (process.env.PRODUCTION_BASE_URL || "").replace(/\/$/, "");
const webhookSecret = process.env.PRODUCTION_BILLING_WEBHOOK_SECRET || "";
if (!base) throw new Error("PRODUCTION_BASE_URL is required.");
if (!webhookSecret) throw new Error("PRODUCTION_BILLING_WEBHOOK_SECRET is required.");

const api = `${base}/api`;
const suffix = `${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
const password = "Production-E2E-Strong-12345!";
const referrer = {username:`e2e_ref_${suffix}`, email:`e2e_ref_${suffix}@example.test`};
const referred = {username:`e2e_new_${suffix}`, email:`e2e_new_${suffix}@example.test`};

async function request(path, options = {}) {
  const headers = {"Content-Type":"application/json", ...(options.headers || {})};
  const timeoutMs = options.timeoutMs || 15000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    const {timeoutMs: _timeoutMs, ...fetchOptions} = options;
    response = await fetch(`${api}${path}`, {...fetchOptions, headers, signal: controller.signal});
  } catch (error) {
    if (error?.name === "AbortError") throw new Error(`HTTP timeout after ${timeoutMs}ms: ${options.method || "GET"} ${path}`);
    throw error;
  } finally {
    clearTimeout(timer);
  }
  let body = {};
  try { body = await response.json(); } catch {}
  if (!response.ok) {
    throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${JSON.stringify(body)}`);
  }
  return body;
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function sign(body, secret) {
  const timestamp = Math.floor(Date.now() / 1000);
  const digest = crypto.createHmac("sha256", secret).update(`${timestamp}.${body}`).digest("hex");
  return {timestamp, header:`t=${timestamp},v1=${digest}`};
}

const health = await fetch(`${api}/health/`, {signal: AbortSignal.timeout(10000)});
assert(health.ok, `Health failed: HTTP ${health.status}`);
const ready = await fetch(`${api}/health/ready/`, {signal: AbortSignal.timeout(10000)});
assert(ready.ok, `Readiness failed: HTTP ${ready.status}`);
const securityHeaders = {
  "x-content-type-options": "nosniff",
  "referrer-policy": "same-origin",
  "x-frame-options": "DENY",
};
for (const [name, expected] of Object.entries(securityHeaders)) {
  assert((ready.headers.get(name) || "").toLowerCase() === expected, `Missing/incorrect ${name} security header.`);
}
if (base.startsWith("https://")) {
  assert((ready.headers.get("strict-transport-security") || "").toLowerCase().includes("max-age="), "Missing HSTS header on HTTPS production endpoint.");
}

const pricing = await request("/pricing/");
assert(pricing.plans?.free?.monthly_usd === 0, "Free pricing contract failed.");
assert(pricing.plans?.pro?.monthly_usd === 29, "Pro pricing contract failed.");
assert(pricing.plans?.team?.monthly_usd === 15 && pricing.plans?.team?.billing_model === "per_seat", "Team pricing contract failed.");
assert(pricing.plans?.enterprise?.monthly_usd === 299 && pricing.plans?.enterprise?.billing_model === "custom", "Enterprise pricing contract failed.");
assert(pricing.entitlements?.free?.workspaces === 3 && pricing.entitlements?.free?.projects === 5, "Free entitlement contract failed.");
assert(pricing.entitlements?.pro?.workspaces === 25 && pricing.entitlements?.pro?.projects === 50, "Pro entitlement contract failed.");
assert(pricing.entitlements?.team?.org_members === 50 && pricing.entitlements?.team?.workspaces === 100, "Team entitlement contract failed.");
assert(pricing.entitlements?.enterprise?.org_members === 500 && pricing.entitlements?.enterprise?.workspaces === 1000, "Enterprise entitlement contract failed.");
for (const tier of ["free","pro","team","enterprise"]) {
  assert(pricing.feature_matrix?.[tier]?.web_ide === true, tier + " Web IDE entitlement failed.");
  assert(pricing.feature_matrix?.[tier]?.ai_assistant === true, tier + " AI entitlement failed.");
}
assert(pricing.feature_matrix?.free?.collaboration === false && pricing.feature_matrix?.pro?.collaboration === false, "Solo collaboration entitlement failed.");
assert(pricing.feature_matrix?.team?.collaboration === true && pricing.feature_matrix?.team?.governance === true, "Team collaboration/governance entitlement failed.");
assert(pricing.feature_matrix?.enterprise?.enterprise_controls === true, "Enterprise control entitlement failed.");

await request("/register/", {method:"POST", body:JSON.stringify({...referrer,password,first_name:"Production",last_name:"Referrer"})});
const refToken = await request("/token/", {method:"POST", body:JSON.stringify({username:referrer.username,password})});
const refAuth = {"Authorization":`Bearer ${refToken.access}`};
const referralInfo = await request("/referrals/", {headers:refAuth});
assert(typeof referralInfo.code === "string" && referralInfo.code.length === 10, "Referral code contract failed.");

await request("/register/", {
  method:"POST",
  body:JSON.stringify({...referred,password,first_name:"Production",last_name:"Referred",referral_code:referralInfo.code}),
});
const referredToken = await request("/token/", {method:"POST",body:JSON.stringify({username:referred.username,password})});
const auth = {"Authorization":`Bearer ${referredToken.access}`};

const referrerAfter = await request("/referrals/", {headers:refAuth});
assert(referrerAfter.referrals?.some(x => x.status === "pending"), "Referral attribution was not persisted.");

const caps = await request("/ide/capabilities/", {headers:auth});
assert(caps.plan === "free", "Disposable account must start on Free.");
assert(caps.runner?.runtimes?.python?.available, "Production Runner does not expose Python runtime.");
assert(caps.runner?.operations?.execute === true, "Runner execution capability is unavailable.");

const blockedOrg = await fetch(`${api}/organizations/`, {
  method:"POST", headers:{"Content-Type":"application/json",...auth},
  body:JSON.stringify({name:`Free blocked ${suffix}`}),
});
const blockedBody = await blockedOrg.json().catch(()=>({}));
assert(blockedOrg.status === 403 && blockedBody.code === "plan_upgrade_required", "Free plan organization enforcement failed.");

const workspace = await request("/ide/workspaces/", {
  method:"POST", headers:auth,
  body:JSON.stringify({name:`Production Validation ${suffix}`,files:{"main.py":"print('production-runner-ok')\n"},active_file:"main.py"}),
});
assert(workspace.id, "IDE workspace creation failed.");

const created = await request(`/ide/workspaces/${workspace.id}/files/`, {
  method:"POST", headers:auth,
  body:JSON.stringify({action:"create",path:"src/runtime_check.py",content:"print('file-created-ok')\n",revision:workspace.revision}),
});
assert(created.files?.["src/runtime_check.py"] === "print('file-created-ok')\n", "IDE file creation failed.");

const executed = await request(`/ide/workspaces/${workspace.id}/execute/`, {
  method:"POST", headers:auth,
  body:JSON.stringify({command:"python3 main.py",active_file:"main.py"}),
});
assert(executed.status === "success" && executed.exit_code === 0, "IDE Runner execution failed.");
assert(String(executed.stdout || "").trim() === "production-runner-ok", `Unexpected Runner output: ${JSON.stringify(executed.stdout)}`);

const process = await request(`/ide/workspaces/${workspace.id}/process/start/`, {
  method:"POST", headers:auth,
  body:JSON.stringify({command:"python3 -c \"import time; print('process-ok', flush=True); time.sleep(20)\""}),
});
assert(process.id, "IDE process start failed.");
const processes = await request(`/ide/workspaces/${workspace.id}/processes/`, {headers:auth});
const list = Array.isArray(processes) ? processes : processes.processes;
assert(list?.some(x => String(x.id) === String(process.id)), "Runner process lifecycle visibility failed.");
const stopped = await request(`/ide/workspaces/${workspace.id}/process/${encodeURIComponent(process.id)}/stop/`, {method:"POST",headers:auth,body:"{}"});
assert(stopped.status !== "running", "Runner process stop failed.");

const event = {
  id:`evt_production_validation_${suffix}`,
  type:"customer.subscription.updated",
  data:{object:{
    id:`sub_e2e_${suffix}`,
    customer:`cus_e2e_${suffix}`,
    status:"active",
    metadata:{user_id:String((await request("/profile/",{headers:auth})).id),plan:"pro"},
    items:{data:[{quantity:1,price:{id:"price_pro_validation"}}]},
  }},
};
const body = JSON.stringify(event);
const signature = sign(body, webhookSecret);
const billing = await fetch(`${api}/billing/webhook/`, {
  method:"POST",
  headers:{"Content-Type":"application/json","Stripe-Signature":signature.header},
  body,
});
const billingBody = await billing.json().catch(()=>({}));
assert(billing.ok && billingBody.received === true && billingBody.duplicate === false, `Billing webhook failed: ${billing.status} ${JSON.stringify(billingBody)}`);

const tamperedSignature = `${signature.header.slice(0, -1)}${signature.header.endsWith("0") ? "1" : "0"}`;
const rejected = await fetch(`${api}/billing/webhook/`, {
  method:"POST",
  headers:{"Content-Type":"application/json","Stripe-Signature":tamperedSignature},
  body,
});
const rejectedBody = await rejected.json().catch(()=>({}));
assert(rejected.status === 400 && rejectedBody.error === "Invalid webhook signature.", "Invalid billing signature was not rejected.");

const duplicate = await fetch(`${api}/billing/webhook/`, {
  method:"POST",
  headers:{"Content-Type":"application/json","Stripe-Signature":signature.header},
  body,
});
const duplicateBody = await duplicate.json().catch(()=>({}));
assert(duplicate.ok && duplicateBody.duplicate === true, "Billing webhook idempotency failed.");

const usage = await request("/usage/", {headers:auth});
assert(usage.plan === "pro", "Verified billing webhook did not change entitlement to Pro.");

console.log("PRODUCTION VALIDATION PASS", JSON.stringify({
  base,
  checks:["health","readiness","pricing","referral-attribution","free-plan-enforcement","workspace-file-create","runner-execute","process-start-stop","billing-signature","billing-idempotency","pro-plan-entitlement"],
  workspace:workspace.id,
  account:referred.username,
}));
