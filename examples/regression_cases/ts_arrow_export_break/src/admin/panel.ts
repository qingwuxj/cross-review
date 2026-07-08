import { chargeUser } from "../billing/client";

export function triggerBillingOverride(userId: string) {
  return chargeUser(userId, 100);
}
