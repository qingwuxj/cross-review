import { chargeUser } from "@app/billing/client";

export function triggerBillingOverride(userId: string) {
  return chargeUser(userId, 100);
}
