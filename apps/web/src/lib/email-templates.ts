/**
 * Plain-text emails the site sends. Wording is PLACEHOLDER copy for review
 * (docs/web-copy-review.md). Pure functions, no I/O, so they are unit tested.
 */
export type Email = { to: string; subject: string; text: string };

export function newsletterConfirmEmail(site: string, to: string, listName: string, url: string, days: number): Email {
  return {
    to,
    subject: `Confirm your ${site} newsletter sign-up`,
    text:
      `Someone, hopefully you, asked to receive "${listName}" from ${site} at this address.\n\n` +
      `To confirm, open this link and press Confirm:\n\n${url}\n\n` +
      `The link expires in ${days} days. If you did not ask for this, ignore this email and nothing will be sent.\n`,
  };
}

export function passwordResetEmail(site: string, to: string, url: string, minutes: number): Email {
  return {
    to,
    subject: `Reset your ${site} password`,
    text:
      `Someone, hopefully you, asked to reset the password for your ${site} account.\n\n` +
      `Open this link to choose a new password:\n\n${url}\n\n` +
      `The link works once and expires in ${minutes} minutes. If you did not ask for this, ignore this ` +
      `email; your password stays as it is.\n`,
  };
}
