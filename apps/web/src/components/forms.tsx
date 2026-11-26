"use client";

import { useActionState } from "react";
import type { FormState } from "@/app/actions";
import {
  changePasswordAction,
  newsletterAction,
  setPasswordAction,
  signInAction,
  signOutOthersAction,
  subscriptionsAction,
} from "@/app/actions";

function Status({ state }: { state: FormState }) {
  if (state.error)
    return (
      <p className="error" role="alert">
        {state.error}
      </p>
    );
  if (state.message)
    return (
      <p className="ok" role="status">
        {state.message}
      </p>
    );
  return null;
}

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState(signInAction, {});
  return (
    <form action={action} className="stack">
      <input type="hidden" name="next" value={next} />
      <label>
        Email
        <input type="email" name="email" autoComplete="username" required maxLength={254} />
      </label>
      <label>
        Password
        <input type="password" name="password" autoComplete="current-password" required maxLength={256} />
      </label>
      <Status state={state} />
      <button type="submit" disabled={pending}>
        {pending ? "Signing in..." : "Sign in"}
      </button>
    </form>
  );
}

export function SetPasswordForm({ token, purpose }: { token: string; purpose: "invite" | "password_reset" }) {
  const [state, action, pending] = useActionState(setPasswordAction, {});
  return (
    <form action={action} className="stack">
      <input type="hidden" name="token" value={token} />
      <input type="hidden" name="purpose" value={purpose} />
      <label>
        New password (at least 12 characters)
        <input type="password" name="password" autoComplete="new-password" required minLength={12} maxLength={256} />
      </label>
      <label>
        Type it again
        <input type="password" name="confirm" autoComplete="new-password" required minLength={12} maxLength={256} />
      </label>
      <Status state={state} />
      <button type="submit" disabled={pending}>
        {purpose === "invite" ? "Create my account" : "Set password"}
      </button>
    </form>
  );
}

export function NewsletterForm({ lists }: { lists: { code: string; name: string }[] }) {
  const [state, action, pending] = useActionState(newsletterAction, {});
  return (
    <form action={action} className="stack">
      {lists.length > 1 ? (
        <label>
          Newsletter
          <select name="list" defaultValue={lists[0]?.code}>
            {lists.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <input type="hidden" name="list" value={lists[0]?.code} />
      )}
      <label>
        Email
        <input type="email" name="email" autoComplete="email" required maxLength={254} />
      </label>
      <label className="check">
        <input type="checkbox" name="consent" value="yes" required />
        <span>I agree to receive the newsletter I selected by email. I can unsubscribe at any time.</span>
      </label>
      <Status state={state} />
      <button type="submit" disabled={pending}>
        Sign up
      </button>
    </form>
  );
}

export function ChangePasswordForm() {
  const [state, action, pending] = useActionState(changePasswordAction, {});
  return (
    <form action={action} className="stack">
      <label>
        Current password
        <input type="password" name="current" autoComplete="current-password" required maxLength={256} />
      </label>
      <label>
        New password (at least 12 characters)
        <input type="password" name="password" autoComplete="new-password" required minLength={12} maxLength={256} />
      </label>
      <label>
        Type it again
        <input type="password" name="confirm" autoComplete="new-password" required minLength={12} maxLength={256} />
      </label>
      <Status state={state} />
      <button type="submit" disabled={pending}>
        Change password
      </button>
    </form>
  );
}

export function SubscriptionsForm({
  lists,
}: {
  lists: { code: string; name: string; description: string | null; subscribed: boolean }[];
}) {
  const [state, action, pending] = useActionState(subscriptionsAction, {});
  return (
    <form action={action} className="stack">
      {lists.map((l) => (
        <label key={l.code} className="check">
          <input type="checkbox" name="list" value={l.code} defaultChecked={l.subscribed} />
          <span>
            {l.name}
            {l.description && <span className="muted small"> - {l.description}</span>}
          </span>
        </label>
      ))}
      <p className="muted small">By ticking a newsletter you agree to receive it by email. Untick to unsubscribe.</p>
      <Status state={state} />
      <button type="submit" disabled={pending}>
        Save choices
      </button>
    </form>
  );
}

export function SignOutOthersForm() {
  const [state, action, pending] = useActionState(signOutOthersAction, {});
  return (
    <form action={action} className="stack">
      <Status state={state} />
      <button type="submit" className="secondary" disabled={pending}>
        Sign out my other devices
      </button>
    </form>
  );
}
