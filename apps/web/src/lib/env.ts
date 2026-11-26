import "server-only";
import { z } from "zod";

/**
 * Server settings, read from environment variables once, on first use (never at build time).
 * Secrets (RS_DB_PASSWORD) come only from the environment: in AWS from Secrets Manager
 * through the task definition, locally from .env.local, which git ignores.
 */
const schema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  RS_DB_HOST: z.string().default("127.0.0.1"),
  RS_DB_PORT: z.coerce.number().int().positive().default(3306),
  RS_DB_USER: z.string().default("rs_web"),
  RS_DB_PASSWORD: z.string().default(""),
  RS_DB_WAREHOUSE: z.string().default("reitstreet"),
  RS_DB_APP: z.string().default("reitstreet_app"),
  // CA bundle for TLS to Aurora (global-bundle.pem). Required in production.
  RS_DB_SSL_CA: z.string().optional(),
  RS_DB_POOL_SIZE: z.coerce.number().int().positive().default(10),
  // public address of the site, used in links and to check the Origin of form posts
  RS_PUBLIC_BASE_URL: z.string().url().default("http://localhost:3000"),
  // proxies in front of the app that append to X-Forwarded-For (1 behind an ALB)
  RS_TRUSTED_PROXY_HOPS: z.coerce.number().int().min(0).default(0),
  // Name shown on pages and in emails. Placeholder until the business name is settled.
  RS_SITE_NAME: z.string().min(1).max(80).default("REIT Street"),
  // Email for newsletter confirmations and self-service password resets.
  //   none: off (staff send invitation and reset links by hand)
  //   ses:  Amazon SES with the task's IAM role; no keys in the app
  //   file: development and tests only, writes each message to RS_EMAIL_DIR
  RS_EMAIL_PROVIDER: z.enum(["none", "ses", "file"]).default("none"),
  RS_EMAIL_FROM: z.string().email().optional(),
  RS_EMAIL_DIR: z.string().optional(),
  RS_AWS_REGION: z.string().default("us-east-2"),
  // show a banner that the data is synthetic (development and demos)
  RS_SAMPLE_DATA: z
    .enum(["0", "1"])
    .default("0")
    .transform((v) => v === "1"),
});

export type Env = z.infer<typeof schema>;
let cached: Env | undefined;

export function env(): Env {
  if (!cached) {
    const parsed = schema.parse(process.env);
    // A production build may run against a database on this machine (local testing, CI);
    // anywhere else it must use TLS to the database and https for the site.
    const local = (h: string) => ["127.0.0.1", "localhost", "::1"].includes(h);
    if (parsed.NODE_ENV === "production") {
      if (!parsed.RS_DB_SSL_CA && !local(parsed.RS_DB_HOST))
        throw new Error("RS_DB_SSL_CA is required in production (TLS to the database)");
      if (!parsed.RS_PUBLIC_BASE_URL.startsWith("https://") && !local(new URL(parsed.RS_PUBLIC_BASE_URL).hostname))
        throw new Error("RS_PUBLIC_BASE_URL must be https in production");
    }
    if (parsed.RS_EMAIL_PROVIDER === "ses" && !parsed.RS_EMAIL_FROM)
      throw new Error("RS_EMAIL_FROM is required when RS_EMAIL_PROVIDER=ses");
    if (parsed.RS_EMAIL_PROVIDER === "file") {
      if (!parsed.RS_EMAIL_DIR) throw new Error("RS_EMAIL_DIR is required when RS_EMAIL_PROVIDER=file");
      if (!local(new URL(parsed.RS_PUBLIC_BASE_URL).hostname))
        throw new Error("RS_EMAIL_PROVIDER=file is for local development and tests only");
    }
    cached = parsed;
  }
  return cached;
}

/** Cookies are Secure (and use the __Host- prefix) whenever the site is served over https. */
export function secureCookies(): boolean {
  return env().RS_PUBLIC_BASE_URL.startsWith("https://");
}
