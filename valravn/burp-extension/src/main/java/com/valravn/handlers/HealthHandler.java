package com.valravn.handlers;

import com.valravn.http.HttpExchange;
import com.valravn.server.BaseHandler;
import com.valravn.util.JsonUtil;

public class HealthHandler extends BaseHandler {

    private final String version;

    public HealthHandler(String version) {
        this.version = version;
    }

    @Override
    protected void handleRequest(HttpExchange exchange) throws Exception {
        sendJson(exchange, JsonUtil.object(
            "status", "ok",
            "version", version,
            "extension", "Valravn MCP"
        ));
    }
}
