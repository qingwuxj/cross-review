export async function loadOrder(orderId: string) {
  return fetch(`/orders/${orderId}`);
}
