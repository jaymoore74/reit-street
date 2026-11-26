"use server";

import { redirect } from "next/navigation";
import { z } from "zod";
import { changePassword, setPasswordWithToken, signIn } from "@/lib/auth";
import { audit } from "@/lib/audit";
import { emailEnabled } from "@/lib/email";
import { requestPasswordReset } from "@/lib/forgot";
import { confirmSubscription, publicSignup, setMySubscriptions } from "@/lib/newsletter";
import { PASSWORD_MAX } from "@/lib/password";
import { clientInfo } from "@/lib/request";
import { currentUser, revokeSessions } from "@/lib/session";
import { email as emailSchema } from "@/lib/validation";

/**
 * Server actions. Next.js checks that each call comes from this site's own pages (Origin check)
 * and the session cookie is SameSite=Lax, so forms can't be posted from other sites.
 */
export type FormState = { error?: string; message?: string };

const str = (form: FormData, key: string, max = 1000) => {
  const v = form.get(key);
  return typeof v === "string" ? v.slice(0, max) : "";
};

/** Only same-site paths under /app, so a crafted link can't send someone elsewhere after sign-in. */
export async function safeNext(next: string): Promise<string> {
  return /^\/app(?:[/?#][^\\\s]*)?$/.test(next) && !next.startsWith("//") ? next : "/app";
}

export async function signInAction(_: FormState, form: FormData): Promise<FormState> {
  const email = emailSchema.safeParse(str(form, "email", 300));
  const password = str(form, "password", PASSWORD_MAX + 1);
  if (!email.success || !password) return { error: "Enter your email and password." };
  const result = await signIn(email.data, password, await clientInfo());
  if (!result.ok) return { error: result.error };
  redirect(await safeNext(str(form, "next", 500)));
}

export async function setPasswordAction(_: FormState, form: FormData): Promise<FormState> {
  const purpose = str(form, "purpose") === "invite" ? "invite" : "password_reset";
  const token = str(form, "token", 200);
  const password = str(form, "password", PASSWORD_MAX + 1);
  if (password !== str(form, "confirm", PASSWORD_MAX + 1)) return { error: "The two passwords don't match." };
  const result = await setPasswordWithToken(token, purpose, password, await clientInfo());
  if (!result.ok) return { error: result.error };
  redirect("/app");
}

export async function newsletterAction(_: FormState, form: FormData): Promise<FormState> {
  const email = emailSchema.safeParse(str(form, "email", 300));
  if (!email.success) return { error: "Enter a valid email address." };
  if (form.get("consent") !== "yes") return { error: "Tick the box to agree to receive the newsletter." };
  const list = z.string().regex(/^[a-z0-9_]{1,48}$/).safeParse(str(form, "list", 48));
  if (!list.success) return { error: "Choose a newsletter." };
  const result = await publicSignup(email.data, list.data, await clientInfo());
  if (result === "throttled") return { error: "Too many sign-ups from your network. Try again later." };
  if (result === "no_list") return { error: "That newsletter isn't available." };
  return {
    message: emailEnabled()
      ? "Thanks. Check your inbox for a link to confirm your sign-up."
      : "Thanks. We've saved your sign-up; you'll get an email to confirm it before anything is sent.",
  };
}

export async function confirmNewsletterAction(_: FormState, form: FormData): Promise<FormState> {
  const ok = await confirmSubscription(str(form, "token", 200), await clientInfo());
  return ok ? { message: "Confirmed." } : { error: "This link has expired or was already used." };
}

export async function forgotPasswordAction(_: FormState, form: FormData): Promise<FormState> {
  if (!emailEnabled()) return { error: "Ask your account contact for a reset link." };
  const email = emailSchema.safeParse(str(form, "email", 300));
  if (!email.success) return { error: "Enter a valid email address." };
  await requestPasswordReset(email.data, await clientInfo());
  return { message: "If that address has an account, we've sent it a link to reset the password. The link works for an hour." };
}

export async function changePasswordAction(_: FormState, form: FormData): Promise<FormState> {
  const user = await currentUser();
  if (!user) redirect("/login");
  const next = str(form, "password", PASSWORD_MAX + 1);
  if (next !== str(form, "confirm", PASSWORD_MAX + 1)) return { error: "The two new passwords don't match." };
  const result = await changePassword(user, str(form, "current", PASSWORD_MAX + 1), next, await clientInfo());
  return result.ok ? { message: result.message } : { error: result.error };
}

export async function subscriptionsAction(_: FormState, form: FormData): Promise<FormState> {
  const user = await currentUser();
  if (!user) redirect("/login");
  const selected = form.getAll("list").filter((v): v is string => typeof v === "string").slice(0, 50);
  await setMySubscriptions(user, selected, await clientInfo());
  return { message: "Newsletter choices saved." };
}

export async function signOutOthersAction(): Promise<FormState> {
  const user = await currentUser();
  if (!user) redirect("/login");
  const n = await revokeSessions(user.userId, user.sessionHash);
  await audit("sessions_revoked", await clientInfo(), { userId: user.userId, orgId: user.orgId }, null, { count: n });
  return { message: n === 1 ? "Signed out 1 other session." : `Signed out ${n} other sessions.` };
}
