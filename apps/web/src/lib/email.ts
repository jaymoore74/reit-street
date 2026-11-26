import "server-only";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { Email } from "./email-templates";
import { env } from "./env";
import { ulid } from "./tokens";

/**
 * Sends the site's own emails (newsletter confirmations, self-service password resets).
 * Off unless RS_EMAIL_PROVIDER is set (ADR 0013). With "ses" the AWS SDK uses the ECS task
 * role or the instance role, so the app holds no keys. Links in a message are credentials:
 * they go into the message and nowhere else, never into logs or errors.
 */
export function emailEnabled(): boolean {
  return env().RS_EMAIL_PROVIDER !== "none";
}

export function sesInput(from: string, msg: Email) {
  return {
    FromEmailAddress: from,
    Destination: { ToAddresses: [msg.to] },
    Content: {
      Simple: {
        Subject: { Data: msg.subject, Charset: "UTF-8" },
        Body: { Text: { Data: msg.text, Charset: "UTF-8" } },
      },
    },
  };
}

type SesClient = { send(command: unknown): Promise<{ MessageId?: string }> };
let ses: { client: SesClient; Command: new (input: ReturnType<typeof sesInput>) => unknown } | undefined;

async function sesSend(msg: Email): Promise<string> {
  const e = env();
  if (!ses) {
    // loaded on first use, so a site with email off never loads the SDK
    const sdk = await import("@aws-sdk/client-sesv2");
    ses = { client: new sdk.SESv2Client({ region: e.RS_AWS_REGION }), Command: sdk.SendEmailCommand };
  }
  const out = await ses.client.send(new ses.Command(sesInput(e.RS_EMAIL_FROM!, msg)));
  return out.MessageId ?? "";
}

async function fileSend(msg: Email): Promise<string> {
  const e = env();
  const dir = e.RS_EMAIL_DIR!;
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const id = ulid();
  const body = JSON.stringify({ from: e.RS_EMAIL_FROM ?? null, ...msg });
  await writeFile(path.join(dir, `${id}.json`), body, { mode: 0o600, flag: "wx" });
  return id;
}

/** Sends one message. Returns false when email is off or the send failed (logged without the message). */
export async function sendEmail(msg: Email): Promise<boolean> {
  const provider = env().RS_EMAIL_PROVIDER;
  if (provider === "none") return false;
  try {
    const id = provider === "ses" ? await sesSend(msg) : await fileSend(msg);
    console.info(JSON.stringify({ event: "email_sent", provider, id }));
    return true;
  } catch (err) {
    const name = err instanceof Error ? err.name : "Error";
    console.error(JSON.stringify({ event: "email_failed", provider, error: name }));
    return false;
  }
}

/**
 * Sends without making the visitor wait. The page answers the same way, at the same speed,
 * whether or not an email went out, so a form can't be used to find out who has an account.
 */
export function sendInBackground(msg: Email): void {
  void sendEmail(msg);
}
