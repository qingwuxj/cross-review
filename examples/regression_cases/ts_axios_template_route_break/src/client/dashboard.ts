import axios from "axios";

export async function loadOrder(orderId: string) {
  return axios.get(`/orders/${orderId}`);
}
