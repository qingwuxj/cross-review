import { chargeUser as billUser } from "../billing/client";

export function triggerBillingOverride(userId: string) {
  return billUser(userId, 100);
}
