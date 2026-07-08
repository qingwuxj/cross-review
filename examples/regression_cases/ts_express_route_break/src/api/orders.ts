const router = {
  get(path: string, handler: unknown) {
    return handler;
  }
};

router.get("/orders/:orderId", (req, res) => {
  res.json({ id: req.params.orderId });
});
