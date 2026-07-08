import express from "express";

export const router = express.Router();

router.get("/orders/:orderId", async (req, res) => {
  res.json({ id: req.params.orderId });
});
