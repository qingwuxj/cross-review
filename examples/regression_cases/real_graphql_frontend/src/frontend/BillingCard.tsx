export function SubscriptionCard() {
  const query = `
    query SubscriptionCard($id: ID!) {
      subscription(id: $id) {
        id
        planName
      }
    }
  `;
  return query;
}
