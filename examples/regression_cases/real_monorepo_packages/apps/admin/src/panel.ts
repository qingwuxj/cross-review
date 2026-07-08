import { chargeUser } from "@acme/billing-sdk/client";

export function triggerBillingOverride(userId: string) {
  return chargeUser(userId, 100);
}
