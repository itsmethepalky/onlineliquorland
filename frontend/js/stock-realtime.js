(function () {
  function subscribe(callback) {
    if (!window.supabaseClient) {
      console.error("Product stock updates are unavailable: Supabase is not configured.");
      return () => {};
    }

    const channelId = window.crypto?.randomUUID
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random()}`;
    const channel = window.supabaseClient
      .channel(`product-stock-${channelId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "product_stock_updates"
        },
        payload => {
          const record = payload.new || payload.old;
          if (!record || record.product_id === undefined) {
            return;
          }

          try {
            callback(record, payload.eventType);
          } catch (error) {
            console.error("Unable to apply a product stock update:", error);
          }
        }
      )
      .subscribe(status => {
        if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          console.error(`Product stock subscription status: ${status}`);
        }
      });

    return () => {
      window.supabaseClient.removeChannel(channel);
    };
  }

  window.productStockRealtime = { subscribe };
})();
