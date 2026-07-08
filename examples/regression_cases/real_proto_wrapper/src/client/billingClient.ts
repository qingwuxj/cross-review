export async function chargeCustomer(client: any, userId: string) {
  return client.chargeUser({ userId, amountCents: 1000 });
}
