import Fastify from "fastify";

const fastify = Fastify();

fastify.get("/orders/:orderId", async (request, reply) => {
  return { id: request.params.orderId, status: "paid" };
});
