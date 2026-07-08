import { BillingPlan } from "../billing/plan";

export function createDefaultPlan(userId: string) {
  return new BillingPlan(userId);
}
