import "server-only";
import { env } from "./env";

/** The business name on pages and in emails. PLACEHOLDER ("REIT Street") until it is settled. */
export function siteName(): string {
  return env().RS_SITE_NAME;
}
