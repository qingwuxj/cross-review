export async function loadOrder(orderId: string) {
  const response = await fetch(`/orders/${orderId}`);
  return response.json();
}
